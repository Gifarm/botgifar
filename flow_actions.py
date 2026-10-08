import os
import re
import time
import logging
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

# ==============================================================================
# KONSTANTA
# ==============================================================================
MORE_BTN_SELECTOR = "button[aria-label*='more' i], button[title*='more' i], button[aria-haspopup='menu']"
STABLE_THRESHOLD_SECONDS = 15
POLL_INTERVAL_SECONDS = 3

# Tombol saran AI (tanda AI selesai generate & bertanya)
SUGGESTION_BTN_SELECTOR = (
    "button:has-text('Take 1'), button:has-text('Take 2'), button:has-text('Take 3'), "
    "button:has-text('sounds warmer'), button:has-text('hear Take'), "
    "button:has-text('Make it more'), button:has-text('Tweak the'), "
    "button:has-text('Try a')"
)


# ==============================================================================
# HELPER: DETEKSI LAGU (UID UNIK PER BARIS)
# ==============================================================================
def _get_song_rows(page) -> list:
    """
    Return list of dict per baris lagu.

    Setiap item: {
        "uid": str,        # identifier unik (title + subtitle + Y)
        "title": str,      # judul lagu
        "subtitle": str,   # deskripsi singkat
        "y": float,        # posisi Y (untuk sorting)
        "button": Locator, # tombol titik 3
    }

    PENTING: UID unik pakai kombinasi title + subtitle + Y-bucket,
    supaya lagu dengan judul sama tetap terdeteksi sebagai baris berbeda.
    """
    results = []
    try:
        win = page.evaluate("({w: window.innerWidth, h: window.innerHeight})")
        cands = page.locator(MORE_BTN_SELECTOR)
        total = cands.count()

        for idx in range(total):
            btn = cands.nth(idx)
            try:
                box = btn.bounding_box()
                if not box:
                    continue
                if box["x"] < win["w"] * 0.4:  # skip sidebar
                    continue
                if box["y"] < 0 or box["y"] > win["h"]:
                    continue
                if not (btn.is_visible() and btn.is_enabled()):
                    continue

                info = _find_song_info_near_button(page, btn)
                title = info.get("title") or f"__unknown_{int(box['y'])}"
                subtitle = info.get("subtitle") or ""

                # UID: gabungan title + subtitle + Y-bucket (10px granularity)
                y_bucket = int(box["y"] / 10)
                uid = f"{title}||{subtitle[:60]}||y{y_bucket}"

                results.append({
                    "uid": uid,
                    "title": title,
                    "subtitle": subtitle,
                    "y": box["y"],
                    "button": btn,
                })
            except Exception as e:
                logger.debug(f"Skip tombol #{idx}: {e}")
                continue

        # Sort dari atas ke bawah
        results.sort(key=lambda r: r["y"])

        # Dedup berdasarkan UID (yang benar-benar unik)
        seen = set()
        deduped = []
        for r in results:
            if r["uid"] in seen:
                continue
            seen.add(r["uid"])
            deduped.append(r)

        return deduped
    except Exception as e:
        logger.debug(f"Gagal ambil baris lagu: {e}")
        return []


def _find_song_info_near_button(page, btn) -> dict:
    """
    Cari judul + subtitle dari container tombol titik 3.
    Return: {"title": str, "subtitle": str}
    """
    try:
        info = page.evaluate("""
        (btn) => {
            let node = btn;
            for (let i = 0; i < 8; i++) {
                if (!node.parentElement) break;
                node = node.parentElement;
                if (node.children.length >= 2) {
                    const texts = node.querySelectorAll('span, div, h3, h4, p, a');
                    const candidates = [];
                    for (const t of texts) {
                        const txt = (t.innerText || '').trim();
                        if (txt.length < 2 || txt.length > 200) continue;
                        if (/^\\d+$/.test(txt)) continue;
                        if (/^(play|pause|download|share|like|dislike|more|options|wav|mp3)$/i.test(txt)) continue;
                        candidates.push({txt, y: t.getBoundingClientRect().top});
                    }
                    if (candidates.length === 0) continue;
                    candidates.sort((a, b) => a.y - b.y);
                    const title = candidates[0] ? candidates[0].txt : null;
                    const subtitle = candidates[1] ? candidates[1].txt : '';
                    if (title && title.length >= 2) {
                        return {title, subtitle};
                    }
                }
            }
            return {title: null, subtitle: ''};
        }
        """, btn.element_handle())
        return info or {"title": None, "subtitle": ""}
    except Exception as e:
        logger.debug(f"Gagal cari info lagu: {e}")
        return {"title": None, "subtitle": ""}


def _has_suggestion_buttons(page) -> bool:
    """Cek apakah Flow Music menampilkan tombol saran (AI bertanya)."""
    try:
        btns = page.locator(SUGGESTION_BTN_SELECTOR)
        count = btns.count()
        for i in range(min(count, 5)):
            try:
                if btns.nth(i).is_visible(timeout=300):
                    return True
            except:
                continue
        return False
    except:
        return False


# ==============================================================================
# ALERT BROWSER
# ==============================================================================
def show_alert(page, message):
    try:
        page.evaluate(f'alert("{message}");')
    except:
        logger.warning(f"Gagal menampilkan alert: {message}")


# ==============================================================================
# INPUT PROMPT
# ==============================================================================
def input_prompt(page, prompt: str) -> bool:
    logger.info("Mengisi prompt...")
    input_selectors = [
        "textarea[placeholder*='prompt' i]",
        "textarea[placeholder*='Describe' i]",
        "textarea[placeholder*='Ask' i]",
        "textarea"
    ]
    for selector in input_selectors:
        try:
            page.wait_for_selector(selector, timeout=5000)
            page.fill(selector, prompt)
            logger.info("Prompt berhasil diisi.")
            return True
        except:
            continue
    logger.error("Kolom input tidak ditemukan.")
    return False


# ==============================================================================
# GENERATE & TUNGGU SEMUA LAGU BARU
# ==============================================================================
def generate_and_wait_all_songs(page, max_wait_seconds: int = 300) -> list:
    """
    Tekan Enter, tunggu SEMUA lagu baru muncul (berdasarkan UID unik).

    Strategi:
    1. Snapshot UID lagu SEBELUM Enter.
    2. Tekan Enter.
    3. Polling: cari UID baru.
    4. Kalau muncul tombol saran AI → anggap selesai.
    5. Tunggu sampai stabil → return list lagu baru.
    """
    try:
        page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
        page.wait_for_timeout(800)
    except:
        pass

    baseline_rows = _get_song_rows(page)
    baseline_uids = set(r["uid"] for r in baseline_rows)
    logger.info(f"📸 Snapshot lagu sebelum generate: {len(baseline_uids)} lagu")

    logger.info("Menekan Enter untuk generate...")
    page.keyboard.press("Enter")
    page.wait_for_timeout(2000)

    start_time = time.time()
    last_change_time = time.time()
    last_new_uids = set()
    attempt = 0
    suggestion_seen = False

    loading_selectors = [
        "text=/generating/i",
        "text=/loading/i",
        "[class*='spinner']",
        "[class*='loading']",
        "svg[class*='animate-spin']",
    ]

    logger.info(f"⏳ Menunggu lagu baru (stabil {STABLE_THRESHOLD_SECONDS}s)...")

    while True:
        elapsed = time.time() - start_time
        attempt += 1

        if elapsed > max_wait_seconds:
            logger.error(f"❌ Timeout total setelah {max_wait_seconds}s.")
            break

        # Cek loading
        still_loading = False
        for sel in loading_selectors:
            try:
                if page.locator(sel).first.is_visible(timeout=400):
                    still_loading = True
                    break
            except:
                continue

        # Cek suggestion buttons
        if _has_suggestion_buttons(page):
            if not suggestion_seen:
                logger.info(f"   💬 Tombol saran AI terdeteksi — AI selesai generate.")
            suggestion_seen = True

        # Ambil UID lagu sekarang
        current_rows = _get_song_rows(page)
        current_uids = set(r["uid"] for r in current_rows)
        new_uids = current_uids - baseline_uids

        # Deteksi perubahan
        if new_uids != last_new_uids:
            logger.info(f"   🔄 [{int(elapsed)}s] Lagu baru: {len(new_uids)}")
            for uid in list(new_uids)[:5]:
                logger.info(f"      • {uid[:80]}")
            last_change_time = time.time()
            last_new_uids = new_uids
        else:
            stable_duration = time.time() - last_change_time
            status = "loading" if still_loading else ("suggestion" if suggestion_seen else "idle")
            logger.info(f"   ⏳ [{int(elapsed)}s] Lagu baru: {len(new_uids)} | Stabil: {int(stable_duration)}s | {status}")

        # === KONDISI SELESAI ===
        stable_duration = time.time() - last_change_time
        if len(new_uids) >= 1 and not still_loading:
            if suggestion_seen or stable_duration >= STABLE_THRESHOLD_SECONDS:
                logger.info(f"🎵 Selesai! {len(new_uids)} lagu baru terdeteksi.")
                page.wait_for_timeout(1500)
                result = [r for r in current_rows if r["uid"] in new_uids]
                result.sort(key=lambda r: r["y"])
                return result

        page.wait_for_timeout(POLL_INTERVAL_SECONDS * 1000)

    # Timeout fallback
    final_rows = _get_song_rows(page)
    final_uids = set(r["uid"] for r in final_rows)
    new_uids = final_uids - baseline_uids
    if new_uids:
        logger.warning(f"⚠️ Timeout tapi dapat {len(new_uids)} lagu. Lanjut download.")
        result = [r for r in final_rows if r["uid"] in new_uids]
        result.sort(key=lambda r: r["y"])
        return result

    logger.error("❌ Tidak ada lagu baru yang muncul.")
    return []


# ==============================================================================
# DOWNLOAD BERDASARKAN UID
# ==============================================================================
def download_song_by_uid(page, target_uid: str, download_dir: str, downloads_list: list, label: str = "") -> bool:
    """
    Download lagu berdasarkan UID unik (title + subtitle + Y-bucket).
    Ini memastikan lagu dengan judul sama tetap ter-download semua.
    """
    logger.info(f"🎯 Download lagu UID '{target_uid[:70]}' {label}...")

    # Handle popup AI
    try:
        ai_buttons = page.locator(
            "button:has-text('Continue'), button:has-text('Gunakan'), "
            "button:has-text('Accept'), button:has-text('Yes'), button:has-text('Lanjutkan')"
        )
        if ai_buttons.count() > 0:
            logger.info("Popup AI terdeteksi. Menutup...")
            ai_buttons.first.click()
            time.sleep(1.5)
    except:
        pass

    # === A. Cari tombol titik 3 berdasarkan UID ===
    logger.info("Mencari tombol titik 3 target...")
    try:
        rows = _get_song_rows(page)
        target_row = None
        for r in rows:
            if r["uid"] == target_uid:
                target_row = r
                break

        if target_row is None:
            logger.error(f"Lagu UID '{target_uid[:70]}' tidak ditemukan.")
            return False

        target = target_row["button"]
        target.scroll_into_view_if_needed(timeout=5000)
        target.hover(timeout=5000)
        time.sleep(0.5)
        target.click(timeout=5000)
        logger.info(f"Berhasil klik titik 3.")
    except Exception as e:
        logger.error(f"Gagal klik titik 3: {e}")
        return False

    time.sleep(1)

    # === B. Buka Submenu Download ===
    logger.info("Membuka menu 'Download'...")
    try:
        wav_item = page.locator("[role='menuitem']:visible").filter(has_text=re.compile(r"^\s*WAV\s*$")).first
        dl_item = page.locator("[role='menuitem']:visible").filter(has_text=re.compile(r"^\s*Download\s*$")).first

        dl_item.wait_for(state="visible", timeout=5000)
        dbox = dl_item.bounding_box()
        dcx = dbox["x"] + dbox["width"] / 2
        dcy = dbox["y"] + dbox["height"] / 2

        page.mouse.move(dbox["x"] - 8, dcy, steps=8)
        time.sleep(0.2)
        page.mouse.move(dcx, dcy, steps=10)
        time.sleep(0.9)

        if not wav_item.is_visible():
            logger.info("Hover kurang, pakai keyboard (ArrowRight)...")
            dl_item.focus()
            page.keyboard.press("ArrowRight")
            time.sleep(0.9)

        wav_item.wait_for(state="visible", timeout=4000)
        logger.info("Submenu Download terbuka.")
    except Exception as e:
        logger.error(f"Gagal buka submenu Download: {e}")
        return False

    # === C. Klik WAV ===
    logger.info("Mengklik format 'WAV'...")
    try:
        wav_item.wait_for(state="visible", timeout=4000)
        wbox = wav_item.bounding_box()
        wcx = wbox["x"] + wbox["width"] / 2
        wcy = wbox["y"] + wbox["height"] / 2

        try:
            dbox = dl_item.bounding_box()
            row_y = dbox["y"] + dbox["height"] / 2
            page.mouse.move(wcx, row_y, steps=10)
            time.sleep(0.2)
        except:
            pass

        page.mouse.move(wcx, wcy, steps=8)
        time.sleep(0.4)
        page.mouse.click(wcx, wcy)
        time.sleep(1)

        if wav_item.is_visible():
            logger.info("Klik belum kena, fokus + Enter...")
            wav_item.focus()
            page.keyboard.press("Enter")
            time.sleep(1)

        if wav_item.is_visible():
            raise Exception("Menu WAV masih terbuka")

        logger.info("Berhasil klik WAV.")
    except Exception as e:
        logger.error(f"Gagal klik WAV: {e}")
        return False

    # === D. Tunggu & Simpan ===
    logger.info("Menunggu file download...")
    waited = 0
    while not downloads_list and waited < 60000:
        page.wait_for_timeout(500)
        waited += 500

    if downloads_list:
        dl = downloads_list.pop(0)

        # Nama file: prioritas suggested_filename dari server,
        # fallback: title + subtitle + Y-bucket biar unik
        suggested = dl.suggested_filename
        if suggested and os.path.splitext(suggested)[1]:
            name = suggested
        else:
            parts = target_uid.split("||")
            title = re.sub(r'[<>:"/\\|?*]', '_', parts[0])[:70]
            sub = re.sub(r'[<>:"/\\|?*]', '_', parts[1])[:40] if len(parts) > 1 else ""
            ybucket = parts[2] if len(parts) > 2 else ""
            name = f"{title}_{sub}_{ybucket}.wav" if sub else f"{title}_{ybucket}.wav"

        base, ext = os.path.splitext(name)
        target_path = os.path.join(download_dir, name)
        n = 1
        while os.path.exists(target_path):
            target_path = os.path.join(download_dir, f"{base}_{n}{ext}")
            n += 1

        dl.save_as(target_path)
        logger.info(f"✅ File tersimpan: {target_path}")
        return True
    else:
        logger.warning("Tidak ada download terdeteksi dalam 60 detik.")
        return False


# ==============================================================================
# UTIL: TUTUP MENU
# ==============================================================================
def close_menus(page):
    try:
        page.keyboard.press("Escape")
        time.sleep(0.5)
        page.keyboard.press("Escape")
        time.sleep(0.5)
    except:
        pass