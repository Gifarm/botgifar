"""
Aksi otomasi di halaman Flow Music.

Semua selector dikumpulkan di bagian atas supaya kalau tampilan situs berubah,
perbaikannya cuma di satu tempat.

Prinsip "jalan di belakang layar":
- TIDAK pernah memanggil page.bring_to_front() saat bekerja, karena itu akan
  mencuri fokus dari Zoom/aplikasi lain.
- Semua interaksi memakai CDP (fill/click/keyboard) yang tidak butuh jendela
  sedang aktif di depan.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import time
from pathlib import Path

from config import (
    DOWNLOAD_STABLE_SECONDS,
    DOWNLOAD_STALL_WARN_SECONDS,
    DOWNLOAD_TIMEOUT_SECONDS,
    GENERATE_STABLE_SECONDS,
    POLL_INTERVAL_SECONDS,
)

logger = logging.getLogger(__name__)

# ==============================================================================
# SELECTOR
# ==============================================================================
MORE_BTN_SELECTOR = (
    "button[aria-label*='more' i], button[title*='more' i], "
    "button[aria-haspopup='menu']"
)

PROMPT_INPUT_SELECTORS = [
    "textarea[placeholder*='prompt' i]",
    "textarea[placeholder*='describe' i]",
    "textarea[placeholder*='song' i]",
    "textarea[placeholder*='music' i]",
    "textarea[placeholder*='ask' i]",
    "textarea",
]

GENERATE_BUTTON_SELECTORS = [
    "button:has-text('Generate')",
    "button:has-text('Create')",
    "button[aria-label*='generate' i]",
    "button[title*='generate' i]",
    "button[type='submit']",
]

SUGGESTION_BTN_SELECTOR = (
    "button:has-text('Take 1'), button:has-text('Take 2'), button:has-text('Take 3'), "
    "button:has-text('sounds warmer'), button:has-text('hear Take'), "
    "button:has-text('Make it more'), button:has-text('Tweak the'), "
    "button:has-text('Try a')"
)

ERROR_KEYWORD_SELECTOR = (
    "text=/no credits/i, "
    "text=/credit.*habis/i, "
    "text=/out of credits/i, "
    "text=/insufficient/i, "
    "text=/limit reached/i, "
    "text=/daily limit/i, "
    "text=/try again later/i, "
    "text=/rate limit/i"
)

LOADING_SELECTORS = [
    "text=/generating/i",
    "text=/loading/i",
    "[class*='spinner']",
    "[class*='loading']",
    "svg[class*='animate-spin']",
]

# Penanda halaman marketing (belum login). Sengaja hanya frasa yang khas
# halaman depan; "log in"/"sign up" dicek lewat elemen link di header karena
# teks itu bisa juga muncul di tempat lain.
LOGIN_PAGE_SIGNALS = (
    "welcome to the studio",
    "free to start",
)

# Link/tombol khas header halaman yang belum login.
LOGIN_LINK_SELECTOR = (
    "a:has-text('Log in'), a:has-text('Sign up'), "
    "button:has-text('Log in'), button:has-text('Sign up')"
)


# ==============================================================================
# UTILITAS UMUM
# ==============================================================================
def _stopped(stop_event) -> bool:
    return stop_event is not None and stop_event.is_set()


def _sanitize_filename(name: str, max_len: int = 120) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().strip(".")
    if not name:
        name = "song"
    return name[:max_len]


def close_menus(page) -> None:
    """Tutup menu/popup yang mungkin masih terbuka."""
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
    except Exception:
        pass


# ==============================================================================
# DETEKSI HALAMAN
# ==============================================================================
def find_prompt_box(page):
    """Cari textarea prompt yang benar-benar terlihat."""
    for selector in PROMPT_INPUT_SELECTORS:
        try:
            locator = page.locator(selector)
            count = min(locator.count(), 10)
        except Exception:
            continue
        for i in range(count):
            box = locator.nth(i)
            try:
                if box.is_visible():
                    return box
            except Exception:
                continue
    return None


def is_logged_in(page) -> tuple[bool | None, str]:
    """
    Return (status, pesan):
      True  -> sudah masuk aplikasi
      False -> masih di halaman login/marketing
      None  -> tidak bisa dipastikan

    Halaman depan Flow Music juga punya textarea demo ("Describe your song..."),
    jadi keberadaan textarea saja BUKAN bukti sudah login. Penanda halaman
    marketing harus dicek lebih dulu.
    """
    try:
        url = page.url
    except Exception:
        url = ""
    if "accounts.google.com" in url:
        return False, "Masih di halaman login Google"

    try:
        body = page.locator("body").inner_text(timeout=5_000)
    except Exception:
        return None, "Halaman belum selesai dimuat"

    # Halaman yang masih kosong/baru dimuat jangan diputuskan dulu.
    if len(body.strip()) < 80:
        return None, "Halaman belum selesai dimuat"

    try:
        if page.locator(LOGIN_LINK_SELECTOR).first.is_visible(timeout=1_500):
            return False, "Silakan login manual di browser ini"
    except Exception:
        pass

    lowered = body.lower()
    if any(signal in lowered for signal in LOGIN_PAGE_SIGNALS):
        return False, "Silakan login manual di browser ini"

    box = find_prompt_box(page)
    if box is not None:
        return True, "Siap"

    return None, "Status login tidak terdeteksi"


def wait_for_login_state(
    page, timeout: float = 30.0, stop_event=None
) -> tuple[bool | None, str]:
    """Tunggu sampai status login pasti (atau timeout)."""
    deadline = time.time() + timeout
    result: tuple[bool | None, str] = (None, "Menunggu halaman dimuat")
    while time.time() < deadline:
        if _stopped(stop_event):
            return None, "Bot dihentikan"
        result = is_logged_in(page)
        if result[0] is not None:
            return result
        try:
            page.wait_for_timeout(1_500)
        except Exception as e:
            return None, f"Halaman bermasalah: {e}"
    return result


# ==============================================================================
# DETEKSI ERROR / CREDITS HABIS
# ==============================================================================
def check_credits_or_rate_limit(page) -> bool:
    """True bila ada pesan credits habis / limit."""
    try:
        if page.locator(ERROR_KEYWORD_SELECTOR).first.is_visible(timeout=1_000):
            logger.warning("🚫 Terdeteksi pesan error (credits/limit).")
            return True
    except Exception:
        pass
    return False


# ==============================================================================
# SCAN BARIS LAGU
# ==============================================================================
def _get_song_rows(page) -> list[dict]:
    """
    Ambil daftar tombol 'titik tiga' milik kartu lagu beserta judulnya.

    UID sengaja TIDAK memakai posisi Y. Dulu posisi ikut masuk UID sehingga
    saat daftar bergeser/scroll, lagu yang sama dianggap lagu baru atau
    sebaliknya — akibatnya 2 lagu bisa terdeteksi lalu tinggal 1.
    """
    results: list[dict] = []
    try:
        win = page.evaluate("({w: window.innerWidth, h: window.innerHeight})")
        candidates = page.locator(MORE_BTN_SELECTOR)
        total = min(candidates.count(), 120)

        for idx in range(total):
            button = candidates.nth(idx)
            try:
                box = button.bounding_box()
                if not box:
                    continue
                # Tombol di panel kiri (navigasi) diabaikan.
                if box["x"] < win["w"] * 0.38:
                    continue
                # Baris di luar viewport TETAP dihitung; lagu yang baru selesai
                # bisa berada sedikit di atas/bawah layar.
                if not (button.is_visible() and button.is_enabled()):
                    continue

                info = _find_song_info_near_button(page, button)
                title = info.get("title") or ""
                subtitle = info.get("subtitle") or ""
                if not title:
                    title = _title_from_button(button) or f"__unknown_{idx}"

                results.append(
                    {
                        # Subjudul hanya dipakai kalau memang ada; subjudul bisa
                        # berubah dinamis sehingga UID harus tetap pendek & stabil.
                        "base_uid": f"{title}||{subtitle[:60]}" if subtitle else title,
                        "title": title,
                        "subtitle": subtitle,
                        "y": box["y"],
                        "button": button,
                    }
                )
            except Exception as e:
                logger.debug(f"Lewati tombol #{idx}: {e}")
                continue

        # Urutkan sesuai posisi di layar, lalu beri nomor urut untuk judul
        # yang benar-benar kembar (mis. dua take dengan judul sama).
        results.sort(key=lambda row: row["y"])
        counts: dict[str, int] = {}
        deduped: list[dict] = []
        for row in results:
            key = row["base_uid"]
            counts[key] = counts.get(key, 0) + 1
            row["uid"] = key if counts[key] == 1 else f"{key}||#{counts[key]}"
            deduped.append(row)
        return deduped
    except Exception as e:
        logger.debug(f"Gagal memindai daftar lagu: {e}")
        return []


def _title_from_button(button) -> str | None:
    """Ambil judul dari aria-label/title tombol titik tiga bila ada."""
    for attr in ("aria-label", "title"):
        try:
            value = button.get_attribute(attr)
        except Exception:
            continue
        if not value:
            continue
        cleaned = re.sub(
            r"(?i)\b(more|options?|menu|actions?|open|for)\b", " ", value
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" :-–—")
        if 2 <= len(cleaned) <= 80:
            return cleaned
    return None


def _find_song_info_near_button(page, button) -> dict:
    """
    Cari judul & subtitle di sekitar tombol titik tiga.

    Versi lama bisa mengambil teks prompt yang panjang (atau teks tombol lain)
    sebagai "judul", sehingga identitas lagu berubah-ubah. Sekarang teks
    dibatasi pendek, tombol dikecualikan, dan heading/thumbnail diutamakan.
    """
    try:
        info = page.evaluate(
            """
            (btn) => {
                const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
                const bad = /^(play|pause|download|share|like|dislike|more|options|menu|wav|mp3|create|generate|\\d+)$/i;
                const isVisible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);

                let node = btn;
                for (let i = 0; i < 7; i++) {
                    if (!node.parentElement) break;
                    node = node.parentElement;

                    const found = [];
                    const heads = node.querySelectorAll(
                        'h1,h2,h3,h4,h5,[class*="title" i],[class*="song" i],[class*="track" i]'
                    );
                    for (const el of heads) {
                        if (el.closest('button')) continue;
                        if (!isVisible(el)) continue;
                        const txt = clean(el.innerText);
                        if (txt.length < 2 || txt.length > 80) continue;
                        if (bad.test(txt)) continue;
                        const r = el.getBoundingClientRect();
                        found.push({txt, y: r.top, w: r.width});
                    }
                    if (!found.length) {
                        for (const el of node.querySelectorAll('span,div,p,a')) {
                            if (el.closest('button')) continue;
                            if (el.children.length > 0) continue;
                            if (!isVisible(el)) continue;
                            const txt = clean(el.innerText);
                            if (txt.length < 2 || txt.length > 80) continue;
                            if (bad.test(txt)) continue;
                            const r = el.getBoundingClientRect();
                            found.push({txt, y: r.top, w: r.width});
                        }
                    }
                    if (!found.length) continue;

                    found.sort((a, b) => a.y - b.y);
                    const img = node.querySelector('img[alt]');
                    const alt = img ? clean(img.alt) : '';
                    const title = (alt.length >= 2 && alt.length <= 80) ? alt : found[0].txt;
                    const rest = found.find((x) => x.txt !== title);
                    return {title, subtitle: rest ? rest.txt : ''};
                }
                return {title: null, subtitle: ''};
            }
            """,
            button.element_handle(),
        )
        return info or {"title": None, "subtitle": ""}
    except Exception:
        return {"title": None, "subtitle": ""}


def find_row(page, target_uid: str) -> dict | None:
    """Cari baris lagu berdasarkan UID, dengan fallback bila nomor urut berubah."""
    rows = _get_song_rows(page)
    for row in rows:
        if row["uid"] == target_uid:
            return row

    # Pisahkan akhiran nomor urut, mis. "Judul||Sub||#2" atau "Judul||#2".
    base = target_uid
    occurrence = 1
    match = re.search(r"(?:\|\|)?#(\d+)$", target_uid)
    if match:
        occurrence = max(1, int(match.group(1)))
        base = target_uid[: match.start()]

    candidates = [row for row in rows if row["base_uid"] == base]
    if not candidates and "||" in base:
        # Format lama "judul||subjudul": cocokkan lewat judul saja.
        candidates = [row for row in rows if row["title"] == base.split("||")[0]]
    if candidates:
        index = min(occurrence - 1, len(candidates) - 1)
        return candidates[index]
    return None


def _has_loading(page) -> bool:
    for selector in LOADING_SELECTORS:
        try:
            if page.locator(selector).first.is_visible(timeout=300):
                return True
        except Exception:
            continue
    return False


# ==============================================================================
# INPUT PROMPT
# ==============================================================================
def input_prompt(page, prompt: str) -> bool:
    """Isi prompt ke textarea. Return True bila berhasil."""
    logger.info("Mengisi prompt...")
    box = find_prompt_box(page)
    if box is None:
        logger.error("Kolom input prompt tidak ditemukan.")
        return False
    try:
        box.click(timeout=5_000)
        box.fill("")
        box.fill(prompt)
        value = box.input_value(timeout=3_000)
        if value.strip() != prompt.strip():
            # Fallback: ketik langsung.
            box.click(timeout=3_000)
            page.keyboard.press("Control+A")
            page.keyboard.insert_text(prompt)
            value = box.input_value(timeout=3_000)
        if not value.strip():
            logger.error("Kolom prompt kosong setelah diisi.")
            return False
        logger.info("✅ Prompt berhasil diisi.")
        return True
    except Exception as e:
        logger.error(f"Gagal mengisi prompt: {e}")
        return False


def _try_click_generate(page) -> bool:
    """Fallback bila Enter tidak memulai generate."""
    try:
        viewport = page.evaluate("({w: window.innerWidth, h: window.innerHeight})")
    except Exception:
        viewport = {"w": 1280, "h": 900}

    for selector in GENERATE_BUTTON_SELECTORS:
        try:
            locator = page.locator(selector)
            for i in range(min(locator.count(), 5)):
                button = locator.nth(i)
                if not button.is_visible():
                    continue
                text = (button.inner_text(timeout=1_000) or "").strip().lower()
                if not (text in {"create", "generate"} or "generate" in text):
                    continue
                # Hanya tombol di bagian bawah layar (area composer),
                # supaya tidak salah klik menu "Create" di sidebar.
                box = button.bounding_box()
                if box and box["y"] < viewport["h"] * 0.45:
                    continue
                button.click(timeout=4_000)
                logger.info(f"Klik tombol generate: {text or selector}")
                return True
        except Exception:
            continue
    return False


# ==============================================================================
# GENERATE & TUNGGU SEMUA LAGU
# ==============================================================================
def generate_and_wait_all_songs(
    page, max_wait_seconds: int, stop_event=None
) -> list[dict]:
    """
    Tekan Enter untuk generate, lalu tunggu sampai SEMUA lagu baru selesai.
    Return daftar row lagu baru (bisa lebih dari satu).
    """
    try:
        page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
        page.wait_for_timeout(700)
    except Exception:
        pass

    baseline_rows = _get_song_rows(page)
    baseline_uids = {row["uid"] for row in baseline_rows}
    logger.info(f"📸 Lagu sebelum generate: {len(baseline_uids)}")

    logger.info("Menekan Enter untuk generate...")
    try:
        page.keyboard.press("Enter")
    except Exception as e:
        logger.error(f"Gagal menekan Enter: {e}")
        return []
    page.wait_for_timeout(1_500)

    # Tunggu reaksi awal (maks 20 detik), lalu coba tombol Generate.
    start = time.time()
    started = False
    while time.time() - start < 20:
        if _stopped(stop_event):
            return []
        if check_credits_or_rate_limit(page):
            logger.error("🚫 Credits habis / rate limit.")
            return []
        current_uids = {row["uid"] for row in _get_song_rows(page)}
        if current_uids - baseline_uids or _has_loading(page):
            started = True
            break
        page.wait_for_timeout(1_000)

    if not started:
        if _try_click_generate(page):
            page.wait_for_timeout(1_500)
            started = True
        else:
            logger.warning("⚠️ Generate belum terlihat; tetap menunggu...")

    logger.info(f"⏳ Menunggu lagu (stabil {GENERATE_STABLE_SECONDS}s)...")
    start_time = time.time()
    last_change = time.time()
    last_new: set[str] = set()
    suggestion_seen = False

    while True:
        if _stopped(stop_event):
            logger.warning("⏹️ Stop diminta saat menunggu generate.")
            return []

        elapsed = time.time() - start_time
        if elapsed > max_wait_seconds:
            logger.error(f"❌ Timeout menunggu generate setelah {max_wait_seconds}s.")
            break

        if check_credits_or_rate_limit(page):
            logger.error("🚫 Credits habis di tengah generate.")
            return []

        loading = _has_loading(page)
        try:
            if page.locator(SUGGESTION_BTN_SELECTOR).first.is_visible(timeout=300):
                if not suggestion_seen:
                    logger.info("💬 Tombol saran AI terdeteksi.")
                suggestion_seen = True
        except Exception:
            pass

        current_rows = _get_song_rows(page)
        current_uids = {row["uid"] for row in current_rows}
        new_uids = current_uids - baseline_uids

        if new_uids != last_new:
            logger.info(f"   🔄 [{int(elapsed)}s] Lagu baru: {len(new_uids)}")
            last_change = time.time()
            last_new = new_uids
        else:
            stable = time.time() - last_change
            logger.info(
                f"   ⏳ [{int(elapsed)}s] Lagu baru: {len(new_uids)} | "
                f"Stabil: {int(stable)}s | {'loading' if loading else 'idle'}"
            )

        stable = time.time() - last_change
        if len(new_uids) >= 1 and not loading:
            if suggestion_seen or stable >= GENERATE_STABLE_SECONDS:
                page.wait_for_timeout(1_500)
                # Ambil ulang supaya baris yang muncul belakangan ikut.
                final_rows = _get_song_rows(page)
                result = [row for row in final_rows if row["uid"] in new_uids]
                result.sort(key=lambda row: row["y"])
                logger.info(f"🎵 Selesai! {len(result)} lagu baru.")
                return result

        page.wait_for_timeout(POLL_INTERVAL_SECONDS * 1000)

    final_rows = _get_song_rows(page)
    new_uids = {row["uid"] for row in final_rows} - baseline_uids
    if new_uids:
        result = [row for row in final_rows if row["uid"] in new_uids]
        result.sort(key=lambda row: row["y"])
        logger.warning(f"⚠️ Timeout, tetapi ada {len(result)} lagu.")
        return result

    logger.error("❌ Tidak ada lagu baru.")
    return []


# ==============================================================================
# DOWNLOAD
# ==============================================================================
_MENU_TARGET_ATTR = "data-flowbot-menu-target"

# Kata yang mungkin dipakai UI untuk item menu download / format MP3.
DOWNLOAD_MENU_WORDS = (
    r"^download$",
    r"download",
    r"^unduh$",
    r"^save$",
    r"^simpan$",
    r"^export$",
)
MP3_MENU_WORDS = (r"\bmp3\b", r"\b320\s*kbps\b", r"\bhigh quality\b")
WAV_MENU_WORDS = (r"\bwav\b",)

# Cari elemen teks yang cocok di dalam overlay/menu (termasuk shadow DOM),
# lalu tandai elemen yang paling kecil & benar-benar terlihat.
_TAG_MENU_TEXT_JS = r"""
(args) => {
  const [source, flags] = args;
  const re = new RegExp(source, flags);
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();

  document.querySelectorAll('[data-flowbot-menu-target]')
    .forEach((el) => el.removeAttribute('data-flowbot-menu-target'));

  // Kumpulkan root: overlay dulu (menu/dialog), lalu seluruh dokumen.
  const roots = [];
  const walkRoot = (root) => {
    roots.push(root);
    for (const el of root.querySelectorAll('*')) {
      if (el.shadowRoot) walkRoot(el.shadowRoot);
    }
  };
  for (const ov of document.querySelectorAll(
    '[role="menu"],[role="dialog"],[role="listbox"],' +
    '[data-radix-popper-content-wrapper],[data-headlessui-portal]'
  )) walkRoot(ov);
  walkRoot(document);

  const hits = [];
  const seen = new Set();
  for (const root of roots) {
    for (const el of root.querySelectorAll('*')) {
      if (seen.has(el)) continue;
      seen.add(el);
      if (!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)) continue;
      const text = clean(el.innerText);
      if (!text || text.length > 40) continue;
      if (!re.test(text)) continue;

      const style = getComputedStyle(el);
      if (style.visibility === 'hidden' || style.display === 'none') continue;

      const r = el.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) continue;
      if (r.bottom < 0 || r.top > window.innerHeight) continue;
      if (r.right < 0 || r.left > window.innerWidth) continue;

      // Pilih elemen paling spesifik (tidak punya anak dengan teks sama).
      let childSame = false;
      for (const child of el.children) {
        if (clean(child.innerText) === text) { childSame = true; break; }
      }
      if (childSame) continue;

      // Kalau elemen tidak menerima pointer, pakai induk yang bisa diklik.
      let target = el;
      if (style.pointerEvents === 'none') {
        target = el.closest('button,a,[role="menuitem"],[role="option"],li');
        if (!target) continue;
      }
      hits.push({el: target, area: r.width * r.height});
    }
  }

  if (!hits.length) return false;
  hits.sort((a, b) => a.area - b.area);
  hits[0].el.setAttribute('data-flowbot-menu-target', '1');
  return true;
}
"""


def _tag_visible_menu_text(page, pattern: str) -> bool:
    """Tandai elemen visible paling kecil yang teksnya cocok `pattern`."""
    try:
        return bool(page.evaluate(_TAG_MENU_TEXT_JS, [pattern, "i"]))
    except Exception:
        return False


def _menu_locator(page):
    return page.locator(f"[{_MENU_TARGET_ATTR}='1']").first


# ------------------------------------------------------------------------------
# Klik menu berbasis locator Playwright (menembus open shadow DOM).
# ------------------------------------------------------------------------------
def _all_frames(page) -> list:
    """Halaman utama + semua iframe — menu kadang dirender di dalam iframe."""
    frames = []
    try:
        frames.append(page)
        main = page.main_frame
        for frame in page.frames:
            if frame is not main:
                frames.append(frame)
    except Exception:
        pass
    return frames or [page]


def _keyword_locators(target, keyword: str):
    """Kumpulan locator yang mungkin berisi item menu dengan kata kunci ini."""
    rx = re.compile(keyword, re.I)
    result = []
    for getter in (
        lambda: target.get_by_role("menuitem", name=rx),
        lambda: target.get_by_role("option", name=rx),
        lambda: target.get_by_role("button", name=rx),
        lambda: target.get_by_role("link", name=rx),
        lambda: target.get_by_text(rx),
    ):
        try:
            locator = getter()
            if locator.count() > 0:
                result.append(locator)
        except Exception:
            continue
    return result


def _visible_items(locator, limit: int = 6) -> list:
    """Ambil item locator yang terlihat, dari yang paling akhir (overlay)."""
    try:
        count = min(locator.count(), limit)
    except Exception:
        return []
    items = []
    for index in range(count):
        try:
            item = locator.nth(index)
            if item.is_visible():
                items.append(item)
        except Exception:
            continue
    items.reverse()
    return items


def _do_click(page, item, strategy: int = 0) -> bool:
    """Satu strategi klik: 0 normal, 1 mouse, 2 paksa, 3 JS."""
    if strategy == 0:
        try:
            item.scroll_into_view_if_needed(timeout=800)
        except Exception:
            pass
        try:
            item.click(timeout=2_500)
            return True
        except Exception:
            return False

    if strategy == 1:
        try:
            box = item.bounding_box()
            if not box or box["width"] < 1 or box["height"] < 1:
                return False
            x = box["x"] + box["width"] / 2
            y = box["y"] + box["height"] / 2
            page.mouse.move(x, y)
            page.wait_for_timeout(80)
            page.mouse.click(x, y)
            return True
        except Exception:
            return False

    if strategy == 2:
        try:
            item.click(force=True, timeout=1_500)
            return True
        except Exception:
            return False

    try:
        handle = item.element_handle(timeout=1_000)
        if handle:
            handle.evaluate(
                "el => { const t = el.closest(\"button,a,[role='menuitem'],"
                "[role='option'],li\") || el; t.click(); }"
            )
            return True
    except Exception:
        return False
    return False


def _do_hover(page, item) -> bool:
    try:
        box = item.bounding_box()
        if box and box["width"] > 1 and box["height"] > 1:
            page.mouse.move(
                box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            )
        item.hover(timeout=1_500)
        return True
    except Exception:
        return False


def _words(keyword) -> list[str]:
    return [keyword] if isinstance(keyword, str) else list(keyword)


# Submenu di Flow Music muncul saat item "Download" di-hover lalu cepat
# menutup lagi. Untuk itu hover dan klik dilakukan lewat event DOM langsung,
# tanpa menggeser mouse ke item submenu (mouse tetap di "Download").
_MENU_EVENT_JS = r"""
(args) => {
  const [parentPattern, childPattern, mode] = args;
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);

  const all = [];
  const walk = (root) => {
    for (const el of root.querySelectorAll('*')) {
      all.push(el);
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  };
  // Overlay (menu/dialog) diprioritaskan, lalu seluruh dokumen.
  const overlays = document.querySelectorAll(
    '[role="menu"],[role="dialog"],[role="listbox"],' +
    '[data-radix-popper-content-wrapper],[data-headlessui-portal]'
  );
  for (const ov of overlays) walk(ov);
  walk(document);

  const find = (pattern) => {
    const re = new RegExp(pattern, 'i');
    const seen = new Set();
    let best = null;
    for (const el of all) {
      if (seen.has(el)) continue;
      seen.add(el);
      const text = clean(el.innerText);
      if (!text || text.length > 40) continue;
      if (!re.test(text)) continue;
      if (!visible(el)) continue;
      let childSame = false;
      for (const c of el.children) {
        if (clean(c.innerText) === text) { childSame = true; break; }
      }
      if (childSame) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) continue;
      const area = r.width * r.height;
      if (!best || area < best.area) best = {el, area};
    }
    return best ? best.el : null;
  };

  if (mode === 'hover') {
    const p = find(parentPattern);
    if (!p) return false;
    const r = p.getBoundingClientRect();
    const opts = {
      bubbles: true, cancelable: true,
      clientX: r.left + r.width / 2, clientY: r.top + r.height / 2,
    };
    p.dispatchEvent(new MouseEvent('mouseover', opts));
    p.dispatchEvent(new MouseEvent('mouseenter', {...opts, bubbles: false}));
    p.dispatchEvent(new MouseEvent('mousemove', opts));
    return true;
  }

  const c = find(childPattern);
  if (!c) return false;
  const r = c.getBoundingClientRect();
  const base = {
    bubbles: true, cancelable: true,
    clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, button: 0,
  };
  const pointer = {pointerId: 1, isPrimary: true, pointerType: 'mouse'};
  for (const type of [
    'pointerover', 'pointerenter', 'pointermove', 'pointerdown',
    'mousedown', 'pointerup', 'mouseup', 'click',
  ]) {
    try {
      const Ctor = type.startsWith('pointer') ? PointerEvent : MouseEvent;
      c.dispatchEvent(new Ctor(type, {...base, ...(Ctor === PointerEvent ? pointer : {})}));
    } catch (e) { /* lanjut ke event berikutnya */ }
  }
  return true;
}
"""


def _js_menu_event(page, pattern: str, mode: str) -> bool:
    for target in _all_frames(page):
        try:
            if target.evaluate(_MENU_EVENT_JS, ["", pattern, mode]):
                return True
        except Exception:
            continue
    return False


def _js_hover_parent(page, keyword) -> bool:
    for target in _all_frames(page):
        for word in _words(keyword):
            try:
                if target.evaluate(_MENU_EVENT_JS, [word, "", "hover"]):
                    return True
            except Exception:
                continue
    return False


def _js_click_text(page, keyword) -> bool:
    for target in _all_frames(page):
        for word in _words(keyword):
            try:
                if target.evaluate(_MENU_EVENT_JS, ["", word, "click"]):
                    return True
            except Exception:
                continue
    return False


def _fast_click_submenu(page, keyword, verify, timeout: float = 4.0) -> bool:
    """
    Klik item submenu yang muncul saat hover, tanpa menggeser mouse.

    Mouse dibiarkan di item induk, lalu item submenu diklik lewat event DOM.
    Ini menghindari submenu tertutup karena pointer bergerak keluar.
    """
    label = "/".join(_words(keyword))
    deadline = time.time() + timeout
    while time.time() < deadline:
        if verify():
            return True
        if not _js_click_text(page, keyword):
            page.wait_for_timeout(120)
            continue
        page.wait_for_timeout(1_200)
        if verify():
            logger.info(f"✅ Pilihan '{label}' diklik saat submenu terbuka.")
            return True
        logger.info(f"⚠️ Klik '{label}' belum berefek, mencoba lagi...")
    return bool(verify())


def _collect_keyword_items(page, keyword) -> list:
    """Semua elemen kandidat (locator) untuk kata kunci, dari semua frame."""
    items = []
    words = _words(keyword)
    for target in _all_frames(page):
        for word in words:
            for locator in _keyword_locators(target, word):
                items.extend(_visible_items(locator))
            if _tag_visible_menu_text(target, word):
                items.append(target.locator(f"[{_MENU_TARGET_ATTR}='1']").first)
    return items


def _has_menu_item(page, keyword: str) -> bool:
    """Apakah ada item menu (role=menuitem/option) dengan kata kunci ini?"""
    rx = re.compile(keyword, re.I)
    for target in _all_frames(page):
        for role in ("menuitem", "option"):
            try:
                locator = target.get_by_role(role, name=rx)
                if _visible_items(locator):
                    return True
            except Exception:
                continue
    return False


def _hover_keyword(page, keyword, timeout: float = 6.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for item in _collect_keyword_items(page, keyword):
            if _do_hover(page, item):
                return True
        page.wait_for_timeout(250)
    return False


def _click_keyword(page, keyword, timeout: float = 6.0,
                   verify=None, settle: float = 0.9) -> bool:
    """
    Klik item menu dengan kata kunci ini (boleh satu regex atau daftar).

    `verify` dipanggil setelah setiap klik; kalau hasilnya belum sesuai,
    klik diulang dengan strategi lain (normal -> mouse -> paksa -> JS).
    Ini penting karena UI kadang punya lapisan transparan yang menelan klik.
    """
    label = "/".join(_words(keyword))
    deadline = time.time() + timeout
    last_log = 0.0
    while time.time() < deadline:
        items = _collect_keyword_items(page, keyword)
        for item in items:
            # Satu putaran seluruh kandidat bisa lambat di halaman besar;
            # hormati deadline supaya total tunggu tidak melebihi `timeout`.
            if time.time() >= deadline:
                break
            for strategy in range(4):
                if time.time() >= deadline:
                    break
                if not _do_click(page, item, strategy):
                    continue
                if verify is None:
                    logger.info(f"✅ Klik menu '{label}'")
                    return True
                page.wait_for_timeout(int(settle * 1000))
                if verify():
                    logger.info(f"✅ Klik menu '{label}' berhasil")
                    return True
                logger.info(
                    f"⚠️ Klik '{label}' (cara {strategy + 1}) belum berefek, "
                    f"mencoba cara lain..."
                )
        if time.time() - last_log > 3:
            logger.info(f"…menunggu item menu '{label}'")
            last_log = time.time()
        page.wait_for_timeout(250)
    return False


def _menu_inventory(page) -> list[str]:
    """Daftar label yang terlihat di menu/dialog — untuk diagnosa log."""
    found: list[str] = []
    for target in _all_frames(page):
        try:
            labels = target.evaluate(
                r"""
                () => {
                  const out = [];
                  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
                  const overlays = document.querySelectorAll(
                    '[role="menu"],[role="dialog"],[role="listbox"],' +
                    '[data-radix-popper-content-wrapper],[data-headlessui-portal]'
                  );
                  const rootsToScan = overlays.length ? overlays : [document];
                  for (const root of rootsToScan) {
                    for (const el of root.querySelectorAll(
                      'button,[role="menuitem"],[role="option"],a,li,span,div'
                    )) {
                      if (!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)) continue;
                      const t = clean(el.innerText);
                      if (!t || t.length > 40) continue;
                      let childSame = false;
                      for (const c of el.children) {
                        if (clean(c.innerText) === t) { childSame = true; break; }
                      }
                      if (childSame) continue;
                      out.push(t);
                    }
                  }
                  return [...new Set(out)].slice(0, 25);
                }
                """
            )
            for label in labels or []:
                if label not in found:
                    found.append(label)
        except Exception:
            continue
    return found[:25]


def _confirm_download_dialog(page) -> None:
    """Kalau memilih MP3 memunculkan dialog dengan tombol konfirmasi, klik."""
    try:
        dialogs = page.locator("[role='dialog']")
        count = min(dialogs.count(), 3)
        dialog = None
        for index in range(count):
            candidate = dialogs.nth(index)
            if candidate.is_visible():
                dialog = candidate
                break
        if dialog is None:
            return
        for keyword in (r"^download$", r"^unduh$", r"download", r"save", r"simpan"):
            try:
                buttons = dialog.get_by_role("button", name=re.compile(keyword, re.I))
            except Exception:
                continue
            for item in _visible_items(buttons, limit=3):
                for strategy in range(3):
                    if _do_click(page, item, strategy):
                        logger.info("✅ Tombol konfirmasi download diklik.")
                        page.wait_for_timeout(600)
                        return
    except Exception:
        pass


def _log_menu_labels(page) -> None:
    """Catat label menu yang terlihat — untuk diagnosa kalau klik gagal."""
    labels = _menu_inventory(page)
    if labels:
        logger.warning(f"Label menu yang terlihat: {labels}")


def configure_downloads(browser, download_dir: str | os.PathLike):
    """
    Arahkan download Chrome ke folder khusus browser ini.
    Return CDPSession (harus disimpan selama worker hidup).
    """
    path = Path(download_dir)
    path.mkdir(parents=True, exist_ok=True)
    cdp = browser.new_browser_cdp_session()
    cdp.send(
        "Browser.setDownloadBehavior",
        {
            "behavior": "allow",
            "downloadPath": str(path),
            "eventsEnabled": True,
        },
    )

    # Log kejadian download (hanya nama file & status, bukan URL/token).
    try:
        def _on_begin(event):
            name = event.get("suggestedFilename") or "(tanpa nama)"
            logger.info(f"⬇️  Browser mulai mengunduh: {name}")

        def _on_progress(event):
            if event.get("state") == "completed":
                logger.info("✅ Browser menyelesaikan unduhan.")

        cdp.on("Browser.downloadWillBegin", _on_begin)
        cdp.on("Browser.downloadProgress", _on_progress)
    except Exception as e:
        logger.debug(f"Event download tidak aktif: {e}")

    logger.info(f"📥 Folder download: {path}")
    return cdp


def _snapshot(directory: Path) -> set[str]:
    try:
        return {p.name for p in directory.iterdir()}
    except Exception:
        return set()


def _any_new_file(directory: Path, before: set[str]) -> bool:
    """Apakah sudah ada file baru (termasuk .crdownload) di folder?"""
    try:
        return any(
            p.is_file() and p.name not in before for p in directory.iterdir()
        )
    except Exception:
        return False


def _wait_for_new_download(
    directory: Path,
    before: set[str],
    timeout: float,
    stop_event=None,
) -> Path | None:
    """Tunggu file baru muncul dan selesai ditulis."""
    deadline = time.time() + timeout
    last_size = -1
    stable_since = None
    candidate: Path | None = None

    while time.time() < deadline:
        if _stopped(stop_event):
            return None
        try:
            files = [
                p
                for p in directory.iterdir()
                if p.is_file() and p.name not in before
            ]
        except Exception:
            files = []

        # Abaikan file sementara .crdownload
        complete = [p for p in files if not p.name.endswith(".crdownload")]
        if complete:
            newest = max(complete, key=lambda p: p.stat().st_mtime)
            if candidate != newest:
                candidate = newest
                last_size = -1
                stable_since = None
            try:
                size = candidate.stat().st_size
            except Exception:
                size = -1
            if size > 0 and size == last_size:
                if stable_since is None:
                    stable_since = time.time()
                elif time.time() - stable_since >= DOWNLOAD_STABLE_SECONDS:
                    return candidate
            else:
                stable_since = None
                last_size = size
        time.sleep(0.5)

    return None


def _unique_destination(directory: Path, filename: str) -> Path:
    filename = _sanitize_filename(filename)
    base, ext = os.path.splitext(filename)
    if not ext:
        ext = ".mp3"
    destination = directory / f"{base}{ext}"
    counter = 1
    while destination.exists():
        destination = directory / f"{base}_{counter}{ext}"
        counter += 1
    return destination


def _move_finished_file(temp_file: Path, destination_dir: Path) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = _unique_destination(destination_dir, temp_file.name)
    try:
        shutil.move(str(temp_file), str(destination))
    except Exception:
        # Kalau beda filesystem, copy lalu hapus.
        shutil.copy2(str(temp_file), str(destination))
        try:
            temp_file.unlink()
        except Exception:
            pass
    return destination


def download_song_by_uid(
    page,
    target_uid: str,
    download_dir: str | os.PathLike,
    downloads_list: list | None = None,
    label: str = "",
    stop_event=None,
    final_dir: str | os.PathLike | None = None,
    row: dict | None = None,
) -> bool:
    """
    Download satu lagu berdasarkan UID.

    `download_dir` = folder sementara per browser.
    `final_dir`   = folder tujuan akhir (default: folder sementara itu sendiri).
    `row`         = hasil scan baris lagu (opsional, supaya klik lebih presisi).
    """
    directory = Path(download_dir)
    directory.mkdir(parents=True, exist_ok=True)

    if _stopped(stop_event):
        return False

    logger.info(f"🎯 Download '{target_uid[:70]}' {label}")

    # Tutup dialog saran AI kalau ada.
    try:
        buttons = page.locator(
            "button:has-text('Continue'), button:has-text('Gunakan'), "
            "button:has-text('Accept'), button:has-text('Yes'), "
            "button:has-text('Lanjutkan')"
        )
        if buttons.count() > 0 and buttons.first.is_visible():
            buttons.first.click()
            page.wait_for_timeout(1_200)
    except Exception:
        pass

    before = _snapshot(directory)

    # ------------------------------------------------------------------
    # 1. Buka menu titik tiga
    # ------------------------------------------------------------------
    target = None
    if row is not None and row.get("button") is not None:
        try:
            if row["button"].is_visible(timeout=1_000):
                target = row["button"]
        except Exception:
            target = None

    if target is None:
        fresh = find_row(page, target_uid)
        if fresh is None:
            logger.warning("Baris lagu belum ketemu, coba scroll & pindai ulang...")
            try:
                page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
                page.wait_for_timeout(800)
            except Exception:
                pass
            fresh = find_row(page, target_uid)
        if fresh is None:
            logger.error("❌ Lagu tidak ditemukan di halaman.")
            return False
        target = fresh["button"]

    clicked = False
    for attempt in range(2):
        try:
            target.scroll_into_view_if_needed(timeout=5_000)
            target.hover(timeout=5_000)
            page.wait_for_timeout(400)
            target.click(timeout=5_000)
            clicked = True
            break
        except Exception as e:
            logger.warning(f"Klik titik tiga gagal (percobaan {attempt + 1}): {e}")
            close_menus(page)
            fresh = find_row(page, target_uid)
            if fresh is None:
                break
            target = fresh["button"]
            page.wait_for_timeout(500)

    if not clicked:
        logger.error("Gagal klik titik tiga.")
        return False
    logger.info("Klik titik tiga berhasil.")

    page.wait_for_timeout(900)

    # ------------------------------------------------------------------
    # 2. Buka menu Download -> pilih MP3
    # ------------------------------------------------------------------
    try:
        page.wait_for_timeout(400)
        logger.info(f"🧾 Isi menu setelah titik tiga: {_menu_inventory(page)}")

        def download_started() -> bool:
            return _any_new_file(directory, before)

        # Submenu (MP3/WAV) di Flow Music muncul saat item Download di-hover
        # dan cepat menutup lagi. Jadi: hover Download dulu (mouse berhenti di
        # sana), lalu klik MP3 lewat event DOM tanpa menggeser mouse.
        if not _has_menu_item(page, r"\bmp3\b"):
            _hover_keyword(page, DOWNLOAD_MENU_WORDS, timeout=3)
            if not _has_menu_item(page, r"\bmp3\b"):
                _js_hover_parent(page, DOWNLOAD_MENU_WORDS)
                page.wait_for_timeout(350)

        picked = bool(download_started())
        if not picked:
            picked = _fast_click_submenu(
                page, MP3_MENU_WORDS, download_started, timeout=3
            )

        if not picked and not download_started():
            # Sebagian UI baru membuka submenu setelah item Download diklik —
            # dan sebagian lagi langsung mengunduh MP3 saat Download diklik.
            _click_keyword(
                page,
                DOWNLOAD_MENU_WORDS,
                timeout=3,
                settle=0.6,
                verify=lambda: _has_menu_item(page, r"\bmp3\b")
                or download_started(),
            )
            if download_started():
                logger.info("📥 Download langsung mulai setelah klik Download.")
                picked = True
            else:
                picked = _fast_click_submenu(
                    page, MP3_MENU_WORDS, download_started, timeout=3
                )

        if not picked and not download_started():
            # Klik biasa dengan locator (semua strategi: normal/mouse/paksa/JS).
            picked = _click_keyword(
                page, MP3_MENU_WORDS, timeout=4, settle=1.0,
                verify=download_started,
            )

        if not picked and not download_started():
            # Fallback keyboard: buka submenu dengan panah kanan.
            try:
                page.keyboard.press("ArrowRight")
                page.wait_for_timeout(500)
            except Exception:
                pass
            picked = _click_keyword(
                page, MP3_MENU_WORDS, timeout=3, settle=1.0,
                verify=download_started,
            )

        if not picked and not download_started():
            if _click_keyword(
                page, WAV_MENU_WORDS, timeout=3, settle=1.0,
                verify=download_started,
            ):
                logger.warning("⚠️ Pilihan MP3 tidak ada; memakai WAV.")
            else:
                logger.warning(f"🧾 Isi menu terakhir: {_menu_inventory(page)}")
                raise RuntimeError("Pilihan MP3/WAV tidak ditemukan")

        logger.info("Format MP3 dipilih.")
        page.wait_for_timeout(800)

        # Sebagian UI menampilkan tombol konfirmasi setelah memilih format.
        _confirm_download_dialog(page)
    except Exception as e:
        logger.error(f"Gagal memilih Download → MP3: {e}")
        close_menus(page)
        return False

    # ------------------------------------------------------------------
    # 3. Tunggu file
    # ------------------------------------------------------------------
    logger.info("Menunggu file ter-download...")
    temp_file = _wait_for_new_download(
        directory,
        before,
        timeout=DOWNLOAD_STALL_WARN_SECONDS,
        stop_event=stop_event,
    )

    if temp_file is None and not _stopped(stop_event):
        # Kalau sama sekali tidak ada file (termasuk .crdownload) setelah
        # beberapa detik, kemungkinan besar dialog Save native masih muncul.
        # Dialog itu tidak terlihat CDP; satu-satunya jalan adalah mencegahnya
        # lewat preferensi profil + CDP download behavior.
        if not _any_new_file(directory, before):
            logger.warning(
                "⚠️ Belum ada file download setelah "
                f"{DOWNLOAD_STALL_WARN_SECONDS}s — kemungkinan dialog Save "
                "muncul. Periksa preferensi download profil browser."
            )
            labels = _menu_inventory(page)
            if labels:
                logger.warning(f"🧾 Isi menu saat menunggu: {labels}")
        remaining = max(1.0, DOWNLOAD_TIMEOUT_SECONDS - DOWNLOAD_STALL_WARN_SECONDS)
        temp_file = _wait_for_new_download(
            directory,
            before,
            timeout=remaining,
            stop_event=stop_event,
        )

    if temp_file is None:
        # Bantu diagnosa: apa saja yang masih terlihat di layar.
        _log_menu_labels(page)
        close_menus(page)
        logger.warning("❌ Tidak ada file download terdeteksi.")
        return False

    close_menus(page)

    logger.info(f"✅ File terunduh: {temp_file.name} ({temp_file.stat().st_size:,} bytes)")

    # Pindahkan ke folder download utama.
    if final_dir is None:
        final_dir = directory
    try:
        destination = _move_finished_file(temp_file, Path(final_dir))
        logger.info(f"📁 Disimpan: {destination}")
        return True
    except Exception as e:
        logger.error(f"Gagal memindahkan file hasil download: {e}")
        return temp_file.exists()


def download_song_row(page, row: dict, temp_dir, final_dir,
                      stop_event=None) -> bool:
    """Helper: download satu row lagu ke folder tujuan akhir."""
    return download_song_by_uid(
        page,
        row["uid"],
        temp_dir,
        label=f"- {row.get('title', '')[:40]}",
        stop_event=stop_event,
        final_dir=final_dir,
        row=row,
    )
