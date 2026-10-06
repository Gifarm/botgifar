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


# ==============================================================================
# HELPER: DETEKSI LAGU (BERDASARKAN JUDUL)
# ==============================================================================
def _get_song_rows(page) -> list:
    """
    Mengembalikan list of dict untuk setiap baris lagu yang terdeteksi.

    Setiap item: {
        "title": str,        # judul lagu (unik)
        "y": float,          # posisi Y untuk sorting
        "button": Locator,   # tombol titik 3 di baris itu
        "row": Locator,      # container baris lagu (untuk hover)
    }

    Strategi:
    - Cari semua tombol titik 3 (more options).
    - Untuk tiap tombol, cari judul lagu terdekat di kiri/atasnya.
    - Filter agar hanya tombol di area utama (bukan sidebar).
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
                # Abaikan sidebar kiri
                if box["x"] < win["w"] * 0.4:
                    continue
                # Abaikan di luar viewport
                if box["y"] < 0 or box["y"] > win["h"]:
                    continue
                if not (btn.is_visible() and btn.is_enabled()):
                    continue

                # Cari judul lagu di sekitar tombol ini
                # Strategi: cari ancestor container, lalu cari teks judul di dalamnya
                title = _find_song_title_near_button(page, btn, box)
                if not title:
                    # Fallback: pakai koordinat Y sebagai "judul" sementara
                    title = f"__unknown_y_{int(box['y'])}"

                results.append({
                    "title": title,
                    "y": box["y"],
                    "button": btn,
                })
            except Exception as e:
                logger.debug(f"Skip tombol #{idx}: {e}")
                continue

        # Sort dari atas ke bawah
        results.sort(key=lambda r: r["y"])

        # Deduplikasi berdasarkan title (kalau ada judul sama persis, ambil yang paling atas)
        seen_titles = set()
        deduped = []
        for r in results:
            if r["title"] in seen_titles:
                continue
            seen_titles.add(r["title"])
            deduped.append(r)

        return deduped
    except Exception as e:
        logger.debug(f"Gagal ambil baris lagu: {e}")
        return []


def _find_song_title_near_button(page, btn, box) -> str | None:
    """
    Mencari judul lagu terdekat dari tombol 'titik 3'.
    Strategi:
    1. Naik ke ancestor container (div yang membungkus baris lagu).
    2. Cari elemen dengan text yang cocok pola judul lagu (bukan angka, bukan ikon).
    """
    try:
        # Coba pakai JS untuk cari ancestor yang membungkus tombol + judul
        title = page.evaluate("""
        (btn) => {
            // Naik max 6 level untuk cari container baris lagu
            let node = btn;
            for (let i = 0; i < 6; i++) {
                if (!node.parentElement) break;
                node = node.parentElement;
                // Container yang bagus biasanya punya minimal 3 child
                if (node.children.length >= 2) {
                    // Cari elemen teks yang mirip judul
                    // Judul biasanya di sibling kiri dari tombol, atau di dalam container
                    const texts = node.querySelectorAll('span, div, h3, h4, p, a');
                    for (const t of texts) {
                        const txt = (t.innerText || '').trim();
                        // Skip teks kosong, terlalu pendek, atau angka
                        if (txt.length < 3 || txt.length > 120) continue;
                        if (/^\\d+$/.test(txt)) continue;
                        // Skip teks yang jelas-jelas bukan judul (tombol, dll)
                        if (/^(play|pause|download|share|like|dislike|more|options|wav|mp3)$/i.test(txt)) continue;
                        // Skip kalau mengandung newline (kemungkinan deskripsi panjang)
                        if (txt.includes('\\n')) continue;
                        return txt;
                    }
                }
            }
            return null;
        }
        """, btn.element_handle())
        return title
    except Exception as e:
        logger.debug(f"Gagal cari judul: {e}")
        return None


def _get_song_titles(page) -> list:
    """Mengembalikan list judul lagu yang terdeteksi (urutan atas→bawah)."""
    return [r["title"] for r in _get_song_rows(page)]


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
# GENERATE & TUNGGU SEMUA LAGU BARU (BERBASIS JUDUL)
# ==============================================================================
def generate_and_wait_all_songs(page, max_wait_seconds: int = 300) -> list:
    """
    Menekan Enter, lalu menunggu SEMUA lagu baru muncul.

    Strategi berbasis JUDUL (bukan count):
    1. Snapshot daftar judul SEBELUM Enter.
    2. Tekan Enter.
    3. Polling: cari judul yang BELUM ADA di snapshot = lagu baru.
    4. Tunggu sampai daftar lagu baru STABIL selama STABLE_THRESHOLD_SECONDS.
    5. Return list dict lagu baru (dengan title, y, button).

    Return: list of dict (kosong jika gagal).
    """
    # Scroll ke atas dulu
    try:
        page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
        page.wait_for_timeout(800)
    except:
        pass

    # === SNAPSHOT JUDUL SEBELUM ENTER ===
    baseline_rows = _get_song_rows(page)
    baseline_titles = set(r["title"] for r in baseline_rows)
    logger.info(f"📸 Snapshot judul sebelum generate: {len(baseline_titles)} lagu")
    for t in baseline_titles:
        logger.info(f"   - {t[:60]}")

    # === TEKAN ENTER ===
    logger.info("Menekan Enter untuk generate...")
    page.keyboard.press("Enter")
    page.wait_for_timeout(2000)

    start_time = time.time()
    last_change_time = time.time()
    last_new_titles = set()
    attempt = 0

    loading_selectors = [
        "text=/generating/i",
        "text=/loading/i",
        "[class*='spinner']",
        "[class*='loading']",
        "svg[class*='animate-spin']",
    ]

    logger.info(f"⏳ Menunggu lagu baru (stabil {STABLE_THRESHOLD_SECONDS}s untuk dianggap selesai)...")

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

        # Ambil semua judul sekarang
        current_rows = _get_song_rows(page)
        current_titles = set(r["title"] for r in current_rows)

        # Lagu baru = judul yang TIDAK ADA di baseline
        new_titles = current_titles - baseline_titles
        # Filter judul fallback "__unknown_y_..." biar tidak salah deteksi
        new_titles_real = set(t for t in new_titles if not t.startswith("__unknown_y_"))

        # Deteksi perubahan
        if new_titles_real != last_new_titles:
            logger.info(f"   🔄 [{int(elapsed)}s] Lagu baru: {len(new_titles_real)} ({sorted(new_titles_real)})")
            last_change_time = time.time()
            last_new_titles = new_titles_real
        else:
            stable_duration = time.time() - last_change_time
            status = "loading" if still_loading else "idle"
            logger.info(f"   ⏳ [{int(elapsed)}s] Lagu baru: {len(new_titles_real)} | Stabil: {int(stable_duration)}s | {status}")

        # === KONDISI SELESAI ===
        stable_duration = time.time() - last_change_time
        if len(new_titles_real) >= 1 and not still_loading and stable_duration >= STABLE_THRESHOLD_SECONDS:
            logger.info(f"🎵 Selesai! Total {len(new_titles_real)} lagu baru terdeteksi.")
            page.wait_for_timeout(1500)

            # Return hanya lagu baru, dalam urutan atas→bawah
            result = [r for r in current_rows if r["title"] in new_titles_real]
            # Sort dari atas ke bawah (lagu terbaru biasanya di atas)
            result.sort(key=lambda r: r["y"])
            return result

        page.wait_for_timeout(POLL_INTERVAL_SECONDS * 1000)

    # Timeout — ambil apa pun yang ada
    final_rows = _get_song_rows(page)
    final_titles = set(r["title"] for r in final_rows)
    new_titles = final_titles - baseline_titles
    new_titles_real = set(t for t in new_titles if not t.startswith("__unknown_y_"))

    if new_titles_real:
        logger.warning(f"⚠️ Timeout tapi dapat {len(new_titles_real)} lagu. Lanjut download.")
        result = [r for r in final_rows if r["title"] in new_titles_real]
        result.sort(key=lambda r: r["y"])
        return result

    logger.error("❌ Tidak ada lagu baru yang muncul.")
    return []


# ==============================================================================
# DOWNLOAD BERDASARKAN TITLE
# ==============================================================================
def download_song_by_title(page, target_title: str, download_dir: str, downloads_list: list, label: str = "") -> bool:
    """
    Download lagu berdasarkan JUDUL-nya (bukan index).
    Ini menghindari bug "download lagu yang sama 2x".
    """
    logger.info(f"🎯 Download lagu '{target_title[:50]}' {label}...")

    # Handle popup AI
    try:
        ai_buttons = page.locator(
            "button:has-text('Continue'), button:has-text('Gunakan'), "
            "button:has-text('Accept'), button:has-text('Yes'), button:has-text('Lanjutkan')"
        )
        if ai_buttons.count() > 0:
            logger.info("Rekomendasi AI terdeteksi. Menutup...")
            ai_buttons.first.click()
            time.sleep(1.5)
    except:
        pass

    # === A. Cari tombol titik 3 berdasarkan title ===
    logger.info("Mencari tombol titik 3 untuk lagu target...")
    try:
        rows = _get_song_rows(page)
        target_row = None
        for r in rows:
            if r["title"] == target_title:
                target_row = r
                break

        if target_row is None:
            logger.error(f"Lagu '{target_title[:50]}' tidak ditemukan di halaman.")
            return False

        target = target_row["button"]
        target.scroll_into_view_if_needed(timeout=5000)
        target.hover(timeout=5000)
        time.sleep(0.5)
        target.click(timeout=5000)
        logger.info(f"Berhasil klik titik 3 untuk '{target_title[:50]}'.")
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
            logger.info("Hover belum cukup, pakai keyboard (panah kanan)...")
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
            logger.info("Klik belum kena, pakai fokus langsung + Enter...")
            wav_item.focus()
            page.keyboard.press("Enter")
            time.sleep(1)

        if wav_item.is_visible():
            raise Exception("Menu WAV masih terbuka")

        logger.info("Berhasil klik WAV.")
    except Exception as e:
        logger.error(f"Gagal klik WAV: {e}")
        return False

    # === D. Tunggu dan Simpan Download ===
    logger.info("Menunggu file download...")
    waited = 0
    while not downloads_list and waited < 60000:
        page.wait_for_timeout(500)
        waited += 500

    if downloads_list:
        dl = downloads_list.pop(0)
        # Pakai judul lagu sebagai nama file (biar tidak bentrok)
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', target_title)[:100]
        name = dl.suggested_filename or f"{safe_title}.wav"
        if not os.path.splitext(name)[1]:
            name += ".wav"

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