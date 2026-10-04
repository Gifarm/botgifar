import time
import logging
import sys

from config import (
    PROMPT_FILE, DOWNLOAD_DIR, MAX_LOOPS,
    WEBSITE_URL, DEBUGGING_PORT
)
from prompts import get_next_prompt
from browser import BrowserManager
from flow_actions import (
    input_prompt,
    generate_and_wait_all_songs,
    download_song_at_index,
    close_menus,
    show_alert,
)

# ==============================================================================
# SETUP LOGGING
# ==============================================================================
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
# MAIN AUTOMATION
# ==============================================================================
def run_automation():
    logger.info("🚀 Memulai Bot - Mode Multi-Song per Prompt")

    browser_manager = BrowserManager(DEBUGGING_PORT, WEBSITE_URL)

    try:
        page = browser_manager.connect()

        # Cek Login (info saja, tanpa pause panjang)
        logger.info("⏳ Mengecek status login...")
        time.sleep(2)
        show_alert(page, "👋 Pastikan sudah login. Bot mulai dalam 5 detik...")
        time.sleep(5)

        downloads = []
        page.on("download", lambda d: downloads.append(d))

        for i in range(MAX_LOOPS):
            prompt = get_next_prompt(PROMPT_FILE)
            if not prompt:
                show_alert(page, "✅ Selesai! Tidak ada prompt tersisa.")
                logger.info("✅ Tidak ada prompt tersisa. Proses selesai!")
                break

            logger.info(f"\n{'='*70}")
            logger.info(f"📊 [LOOP {i+1}/{MAX_LOOPS}]")
            logger.info(f"📝 Prompt: {prompt[:80]}{'...' if len(prompt) > 80 else ''}")
            logger.info(f"{'='*70}\n")

            try:
                page.bring_to_front()

                # === 1. INPUT PROMPT ===
                if not input_prompt(page, prompt):
                    logger.error(f"Gagal input prompt di loop {i+1}. Skip.")
                    continue

                # === 2. GENERATE & TUNGGU SEMUA LAGU BARU ===
                num_songs = generate_and_wait_all_songs(page, max_wait_seconds=300)
                if num_songs == 0:
                    logger.error(f"Tidak ada lagu yang muncul di loop {i+1}. Skip.")
                    show_alert(page, f"⚠️ Loop {i+1}: Tidak ada lagu muncul.")
                    close_menus(page)
                    continue

                logger.info(f"🎼 Total lagu baru dari prompt ini: {num_songs}")

                # === 3. DOWNLOAD SEMUA LAGU BARU ===
                # Lagu terbaru diasumsikan di ATAS (index 0 = terbaru)
                success_count = 0
                for song_idx in range(num_songs):
                    label = f"(lagu {song_idx + 1}/{num_songs})"
                    logger.info(f"\n--- Download {label} ---")

                    if download_song_at_index(page, song_idx, DOWNLOAD_DIR, downloads, label):
                        success_count += 1
                        time.sleep(2)
                        close_menus(page)
                        time.sleep(1)
                    else:
                        logger.warning(f"Gagal download {label}")
                        close_menus(page)
                        time.sleep(1)

                logger.info(f"✅ Loop {i+1} selesai: {success_count}/{num_songs} lagu terdownload.\n")
                time.sleep(2)

            except Exception as e:
                logger.error(f"❌ Error di loop {i+1}: {e}", exc_info=True)
                show_alert(page, f"❌ Error di loop {i+1}: {str(e)[:100]}")
                close_menus(page)
                continue

        logger.info("\n" + "="*70)
        show_alert(page, "🎉 Semua tugas selesai!")
        logger.info("🏁 BOT SELESAI - Semua prompt telah diproses!")
        logger.info("="*70)

    except Exception as e:
        logger.critical(f"❌ Error fatal: {e}", exc_info=True)
    finally:
        browser_manager.close()


if __name__ == "__main__":
    run_automation()