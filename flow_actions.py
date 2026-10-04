import os
import re
import time
import logging
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

# ==============================================================================
# KONSTANTA
# ==============================================================================
# Selector tombol "titik 3" (More Options) di baris lagu
MORE_BTN_SELECTOR = "button[aria-label*='more' i], button[title*='more' i], button[aria-haspopup='menu']"

# Berapa lama harus stabil (tidak ada lagu baru) sebelum dianggap selesai
STABLE_THRESHOLD_SECONDS = 15

# Interval polling (detik)
POLL_INTERVAL_SECONDS = 3


# ==============================================================================
# HELPER: DETEKSI TOMBOL LAGU
# ==============================================================================
def _get_song_buttons(page) -> list:
    """
    Mengembalikan list of Playwright Locator untuk tombol 'titik 3'
    yang visible di area utama (bukan sidebar), diurutkan dari atas ke bawah.

    Asumsi: lagu terbaru muncul di ATAS daftar.
    """
    result = []
    try:
        win = page.evaluate("({w: window.innerWidth, h: window.innerHeight})")
        cands = page.locator(MORE_BTN_SELECTOR)
        total = cands.count()

        temp = []
        for idx in range(total):
            btn = cands.nth(idx)
            try:
                box = btn.bounding_box()
                if not box:
                    continue
                if box["x"] < win["w"] * 0.4:  # abaikan sidebar kiri
                    continue
                if box["y"] < 0 or box["y"] > win["h"]:
                    continue
                if btn.is_visible() and btn.is_enabled():
                    temp.append((box["y"], btn))
            except:
                continue

        # Urutkan dari atas ke bawah (y terkecil = paling atas)
        temp.sort(key=lambda x: x[0])
        result = [btn for _, btn in temp]
    except Exception as e:
        logger.debug(f"Gagal ambil tombol lagu: {e}")

    return result


def _count_song_buttons(page) -> int:
    """Menghitung jumlah tombol lagu yang visible di area utama."""
    return len(_get_song_buttons(page))


# ==============================================================================
# ALERT BROWSER
# ==============================================================================
def show_alert(page, message):
    """Menampilkan alert/popup di browser."""
    try:
        page.evaluate(f'alert("{message}");')
    except:
        logger.warning(f"Gagal menampilkan alert: {message}")


# ==============================================================================
# INPUT PROMPT
# ==============================================================================
def input_prompt(page, prompt: str) -> bool:
    """Mengisi prompt ke kolom input."""
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
def generate_and_wait_all_songs(page, max_wait_seconds: int = 300) -> int:
    """
    Menekan Enter, lalu menunggu SEMUA lagu baru muncul (bisa 1, 2, 3, dst).

    Strategi:
    1. Snapshot baseline jumlah lagu SEBELUM Enter.
    2. Tekan Enter.
    3. Polling: tunggu sampai jumlah lagu baru STABIL selama STABLE_THRESHOLD_SECONDS.
       (artinya tidak ada lagu tambahan yang muncul lagi)
    4. Return jumlah lagu BARU yang muncul.

    Return: jumlah lagu baru (0 jika gagal).
    """
    # Scroll ke atas dulu supaya lagu terbaru masuk viewport
    try:
        page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
        page.wait_for_timeout(800)
    except:
        pass

    baseline_count = _count_song_buttons(page)
    logger.info(f"📸 Baseline lagu sebelum generate: {baseline_count}")

    # Tekan Enter
    logger.info("Menekan Enter untuk generate...")
    page.keyboard.press("Enter")
    page.wait_for_timeout(2000)  # jeda awal supaya UI mulai transisi

    start_time = time.time()
    last_change_time = time.time()
    last_count = baseline_count
    attempt = 0

    loading_selectors = [
        "text=/generating/i",
        "text=/loading/i",
        "[class*='spinner']",
        "[class*='loading']",
        "svg[class*='animate-spin']",
    ]

    logger.info(f"⏳ Menunggu lagu baru (dianggap selesai jika stabil {STABLE_THRESHOLD_SECONDS}s)...")

    while True:
        elapsed = time.time() - start_time
        attempt += 1

        # Timeout total
        if elapsed > max_wait_seconds:
            logger.error(f"❌ Timeout total setelah {max_wait_seconds}s.")
            break

        # Cek indikator loading
        still_loading = False
        for sel in loading_selectors:
            try:
                if page.locator(sel).first.is_visible(timeout=400):
                    still_loading = True
                    break
            except:
                continue

        current_count = _count_song_buttons(page)
        new_songs = current_count - baseline_count

        # Deteksi perubahan
        if current_count != last_count:
            logger.info(f"   🔄 [{int(elapsed)}s] Jumlah lagu berubah: {last_count} → {current_count} (baru: {new_songs})")
            last_change_time = time.time()
            last_count = current_count
        else:
            stable_duration = time.time() - last_change_time
            status = "loading" if still_loading else "idle"
            logger.info(f"   ⏳ [{int(elapsed)}s] Lagu baru: {new_songs} | Stabil: {int(stable_duration)}s | {status}")

        # === KONDISI SELESAI ===
        stable_duration = time.time() - last_change_time
        if new_songs >= 1 and not still_loading and stable_duration >= STABLE_THRESHOLD_SECONDS:
            logger.info(f"🎵 Selesai! Total {new_songs} lagu baru terdeteksi (stabil {int(stable_duration)}s).")
            page.wait_for_timeout(1500)  # jeda ekstra
            return new_songs

        page.wait_for_timeout(POLL_INTERVAL_SECONDS * 1000)

    # Timeout — kembalikan apa pun yang sudah didapat
    final_count = _count_song_buttons(page)
    new_songs = final_count - baseline_count
    if new_songs > 0:
        logger.warning(f"⚠️ Timeout tapi dapat {new_songs} lagu. Lanjut download.")
        return new_songs

    logger.error("❌ Tidak ada lagu baru yang muncul.")
    return 0


# ==============================================================================
# DOWNLOAD LAGU BERDASARKAN INDEX
# ==============================================================================
def download_song_at_index(page, song_index: int, download_dir: str, downloads_list: list, label: str = "") -> bool:
    """
    Download lagu pada posisi song_index (0 = paling atas = lagu terbaru).

    song_index dihitung dari ATAS daftar lagu yang visible.
    """
    logger.info(f"🎯 Download lagu index {song_index} {label}...")

    # Handle popup rekomendasi AI jika ada
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

    # === A. Klik Titik 3 pada lagu ke-song_index ===
    logger.info(f"Mencari tombol titik 3 untuk lagu #{song_index + 1}...")
    try:
        buttons = _get_song_buttons(page)
        if song_index >= len(buttons):
            logger.error(f"Tombol lagu index {song_index} tidak ada (total: {len(buttons)}).")
            return False

        target = buttons[song_index]
        target.scroll_into_view_if_needed(timeout=5000)
        target.hover(timeout=5000)
        time.sleep(0.5)
        target.click(timeout=5000)
        logger.info(f"Berhasil klik titik 3 lagu #{song_index + 1}.")
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

        # Hover aman: dari luar menu, masuk ke Download
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

        # Verifikasi: kalau submenu masih terbuka, klik gagal
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
        name = dl.suggested_filename or f"song_{song_index + 1}.wav"
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
    """Tutup menu/popup yang mungkin masih terbuka."""
    try:
        page.keyboard.press("Escape")
        time.sleep(0.5)
        page.keyboard.press("Escape")
        time.sleep(0.5)
    except:
        pass