import time
import logging
import sys
import threading

from playwright.sync_api import sync_playwright

from config import (
    PROMPT_FILE, DOWNLOAD_DIR,
    WEBSITE_URL, BROWSER_PORTS,
    GENERATE_MAX_WAIT_SECONDS,
)
from prompts import get_next_prompt, count_prompts
from flow_actions import (
    input_prompt,
    generate_and_wait_all_songs,
    download_song_by_uid,
    close_menus,
    show_alert,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


# ==============================================================================
# KONEKSI PER-THREAD
# ==============================================================================
def connect_to_port(port: int):
    """
    Connect ke browser di port tertentu.
    HARUS dipanggil DI DALAM thread worker,
    karena Playwright Sync API tidak thread-safe.
    Return: (playwright, browser, page) atau (None, None, None) kalau gagal.
    """
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.connect_over_cdp(f"http://localhost:{port}")
        context = browser.contexts[0]

        # Cari tab flowmusic
        page = None
        for pg in context.pages:
            if "flowmusic.app" in pg.url:
                page = pg
                break

        if page is None:
            page = context.pages[0] if context.pages else context.new_page()
            logger.info(f"[P{port}] Membuka {WEBSITE_URL}...")
            page.goto(WEBSITE_URL, wait_until="domcontentloaded")
            time.sleep(3)

        logger.info(f"[P{port}] ✅ Terhubung.")
        return pw, browser, page
    except Exception as e:
        logger.error(f"[P{port}] ❌ Gagal connect: {e}")
        return None, None, None


# ==============================================================================
# WORKER: 1 thread per browser
# ==============================================================================
def worker(port: int, worker_id: str, stop_event: threading.Event):
    """
    Worker untuk 1 profile browser.
    Setiap worker punya Playwright instance sendiri (thread-safe).
    """
    logger.info(f"[{worker_id}] 🟢 Worker port {port} starting...")

    pw = None
    page = None
    downloads = []

    try:
        # === Connect DI DALAM thread ===
        pw, browser, page = connect_to_port(port)
        if page is None:
            logger.error(f"[{worker_id}] ❌ Gagal connect. Worker berhenti.")
            return

        # Setup download listener
        page.on("download", lambda d: downloads.append(d))

        # Tunggu 3 detik supaya semua thread siap
        time.sleep(3)

        # === LOOP: ambil prompt → paste → tunggu → download ===
        while not stop_event.is_set():
            prompt = get_next_prompt(PROMPT_FILE)
            if not prompt:
                logger.info(f"[{worker_id}] 🏁 File prompt habis. Worker selesai.")
                break

            sisa = count_prompts(PROMPT_FILE)
            logger.info(f"\n{'='*70}")
            logger.info(f"[{worker_id}] 📝 Prompt: {prompt[:60]}{'...' if len(prompt) > 60 else ''}")
            logger.info(f"[{worker_id}] 📊 Sisa prompt: {sisa}")
            logger.info(f"{'='*70}")

            try:
                page.bring_to_front()

                # === 1. INPUT PROMPT ===
                if not input_prompt(page, prompt):
                    logger.error(f"[{worker_id}] ❌ Gagal input prompt.")
                    continue

                # === 2. GENERATE & TUNGGU LAGU ===
                new_songs = generate_and_wait_all_songs(
                    page, max_wait_seconds=GENERATE_MAX_WAIT_SECONDS
                )

                if not new_songs:
                    logger.warning(f"[{worker_id}] ⚠️ Tidak ada lagu baru.")
                    close_menus(page)
                    continue

                logger.info(f"[{worker_id}] 🎼 {len(new_songs)} lagu baru.")

                # === 3. DOWNLOAD SEMUA LAGU ===
                success = 0
                for idx, song in enumerate(new_songs):
                    uid = song["uid"]
                    label = f"(lagu {idx+1}/{len(new_songs)})"
                    logger.info(f"[{worker_id}] --- Download {label}: {song['title'][:50]} ---")

                    if download_song_by_uid(page, uid, DOWNLOAD_DIR, downloads, label):
                        success += 1
                        time.sleep(2)
                        close_menus(page)
                        time.sleep(1)
                    else:
                        logger.warning(f"[{worker_id}] Gagal download {label}")
                        close_menus(page)
                        time.sleep(1)

                logger.info(f"[{worker_id}] ✅ Selesai: {success}/{len(new_songs)} lagu.\n")
                time.sleep(2)

            except Exception as e:
                logger.error(f"[{worker_id}] ❌ Error: {e}", exc_info=True)
                try:
                    close_menus(page)
                except:
                    pass
                continue

    finally:
        # Cleanup Playwright di thread ini
        try:
            if pw:
                pw.stop()
                logger.info(f"[{worker_id}] 🔌 Koneksi Playwright ditutup.")
        except Exception as e:
            logger.debug(f"[{worker_id}] Error saat stop playwright: {e}")


# ==============================================================================
# MAIN
# ==============================================================================
def run_automation():
    logger.info("🚀 Memulai Bot - Multi-Profile PARALEL (Per-Thread Playwright)")

    # Cek file prompt
    total = count_prompts(PROMPT_FILE)
    if total == 0:
        logger.error("❌ FILE PROMPT KOSONG / TIDAK ADA PROMPT!")
        logger.error(f"   Path: {PROMPT_FILE}")
        logger.error("   Isi dulu file prompts.txt, baru jalankan bot.")
        return
    logger.info(f"📚 Total prompt di file: {total}")

    logger.info(f"🎯 Akan connect ke {len(BROWSER_PORTS)} browser: {BROWSER_PORTS}")
    logger.info("👋 Pastikan semua browser sudah terbuka & login ke flowmusic.app.")
    logger.info("⏳ Bot mulai dalam 5 detik...")
    time.sleep(5)

    stop_event = threading.Event()

    # Jalankan 1 thread per port
    threads = []
    for idx, port in enumerate(BROWSER_PORTS):
        worker_id = f"W{idx+1}-P{port}"
        t = threading.Thread(
            target=worker,
            args=(port, worker_id, stop_event),
            daemon=False,  # non-daemon supaya main thread tunggu
            name=worker_id,
        )
        t.start()
        threads.append(t)
        logger.info(f"🧵 Thread {worker_id} dijalankan.")
        time.sleep(0.3)

    # Tunggu semua thread selesai
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        logger.warning("\n⚠️ Dihentikan oleh user (Ctrl+C).")
        stop_event.set()

    logger.info("\n" + "="*70)
    logger.info("🏁 BOT SELESAI - Semua prompt diproses!")
    logger.info("="*70)


if __name__ == "__main__":
    run_automation()