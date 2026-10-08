import time
import logging
import sys

from config import (
    PROMPT_FILE, DOWNLOAD_DIR, MAX_LOOPS,
    WEBSITE_URL, BROWSER_PORTS,
    PAUSE_AFTER_PASTE_SECONDS, GENERATE_MAX_WAIT_SECONDS,
)
from prompts import get_next_prompt
from browser import MultiBrowserManager
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


def paste_prompt_to_browser(page, prompt: str) -> bool:
    """
    Paste prompt ke browser + tekan Enter, tanpa tunggu generate selesai.
    """
    try:
        page.bring_to_front()
        if not input_prompt(page, prompt):
            return False
        # Tekan Enter untuk mulai generate
        logger.info("   ⏎ Menekan Enter...")
        page.keyboard.press("Enter")
        time.sleep(PAUSE_AFTER_PASTE_SECONDS)
        return True
    except Exception as e:
        logger.error(f"   ❌ Gagal paste prompt: {e}")
        return False


def run_automation():
    logger.info("🚀 Memulai Bot - Multi-Profile PIPELINE Mode")

    manager = MultiBrowserManager(BROWSER_PORTS, WEBSITE_URL)

    try:
        workers = manager.connect_all()
        if not workers:
            logger.error("❌ Tidak ada profile yang terhubung. Keluar.")
            return

        ports = manager.get_ports()
        logger.info(f"🎯 {len(ports)} profile siap: {ports}")

        logger.info("👋 Pastikan semua profile sudah login ke flowmusic.app.")
        logger.info("⏳ Bot mulai dalam 5 detik...")
        time.sleep(5)

        # Setup download listener per profile
        download_queues = {}
        for port in ports:
            page = manager.get_page(port)
            download_queues[port] = []
            page.on("download", lambda d, p=port: download_queues[p].append(d))

        # ==================================================================
        # PIPELINE:
        # Fase 1: Bagi prompt ke semua browser (paste + Enter, tanpa tunggu)
        # Fase 2: Tunggu semua selesai generate, lalu download semua
        # Fase 3: Ulangi kalau masih ada prompt sisa
        # ==================================================================

        total_prompts_processed = 0
        batch_number = 0

        while total_prompts_processed < MAX_LOOPS:
            batch_number += 1

            # Ambil sejumlah prompt sesuai jumlah profile
            batch_prompts = []
            for _ in range(len(ports)):
                if total_prompts_processed + len(batch_prompts) >= MAX_LOOPS:
                    break
                p = get_next_prompt(PROMPT_FILE)
                if not p:
                    break
                batch_prompts.append(p)

            if not batch_prompts:
                logger.info("✅ Tidak ada prompt tersisa. Selesai!")
                break

            logger.info(f"\n{'#'*70}")
            logger.info(f"📦 BATCH #{batch_number}: {len(batch_prompts)} prompt")
            logger.info(f"{'#'*70}")

            # ==============================================================
            # FASE 1: PASTE PROMPT KE SEMUA BROWSER (tanpa nunggu generate)
            # ==============================================================
            logger.info(f"\n▶️  FASE 1: Paste prompt ke {len(batch_prompts)} browser...")

            # Mapping: port -> prompt yang di-paste
            port_to_prompt = {}

            for idx, prompt in enumerate(batch_prompts):
                port = ports[idx]
                page = manager.get_page(port)
                port_to_prompt[port] = prompt

                logger.info(f"\n  [{idx+1}/{len(batch_prompts)}] Port {port}")
                logger.info(f"    📝 Prompt: {prompt[:60]}{'...' if len(prompt) > 60 else ''}")

                if paste_prompt_to_browser(page, prompt):
                    logger.info(f"    ✅ Paste & Enter berhasil.")
                else:
                    logger.warning(f"    ⚠️ Gagal paste prompt ke port {port}.")
                    port_to_prompt.pop(port, None)

                total_prompts_processed += 1

            logger.info(f"\n  📋 Port yang dapat prompt: {list(port_to_prompt.keys())}")

            # ==============================================================
            # FASE 2: TUNGGU SEMUA SELESAI GENERATE, LALU DOWNLOAD SEMUA
            # ==============================================================
            logger.info(f"\n▶️  FASE 2: Tunggu semua generate, lalu download...")

            for idx, (port, prompt) in enumerate(port_to_prompt.items()):
                page = manager.get_page(port)
                downloads = download_queues[port]

                logger.info(f"\n  [{idx+1}/{len(port_to_prompt)}] Port {port}")

                # Tunggu lagu baru muncul
                new_songs = generate_and_wait_all_songs(
                    page, max_wait_seconds=GENERATE_MAX_WAIT_SECONDS
                )

                if not new_songs:
                    logger.warning(f"    ⚠️ Port {port}: Tidak ada lagu baru. Skip.")
                    close_menus(page)
                    continue

                logger.info(f"    🎼 Port {port}: {len(new_songs)} lagu baru terdeteksi.")

                # Download semua lagu baru
                success_count = 0
                for song_idx, song in enumerate(new_songs):
                    uid = song["uid"]
                    label = f"(lagu {song_idx + 1}/{len(new_songs)})"
                    logger.info(f"    --- Download {label}: {song['title'][:50]} ---")

                    if download_song_by_uid(page, uid, DOWNLOAD_DIR, downloads, label):
                        success_count += 1
                        time.sleep(2)
                        close_menus(page)
                        time.sleep(1)
                    else:
                        logger.warning(f"    Gagal download {label}")
                        close_menus(page)
                        time.sleep(1)

                logger.info(f"    ✅ Port {port}: {success_count}/{len(new_songs)} lagu terdownload.")

            logger.info(f"\n✅ BATCH #{batch_number} selesai.\n")
            time.sleep(3)

        logger.info("\n" + "="*70)
        logger.info(f"🏁 BOT SELESAI - {total_prompts_processed} prompt telah diproses!")
        logger.info("="*70)

        # Alert ke browser pertama
        first_port = ports[0]
        show_alert(manager.get_page(first_port), f"🎉 Selesai! {total_prompts_processed} prompt diproses.")

    except Exception as e:
        logger.critical(f"❌ Error fatal: {e}", exc_info=True)
    finally:
        manager.close_all()


if __name__ == "__main__":
    run_automation()