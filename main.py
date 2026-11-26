"""
Mode command-line (opsional).

GUI adalah cara pemakaian utama (jalankan `python app.py`), tetapi file ini
berguna untuk testing atau dijalankan di server tanpa tampilan.

Contoh:
    ./venv/bin/python main.py --browsers 5 --prompt "promt_lagu/100.txt"
    ./venv/bin/python main.py --login          # buka semua browser untuk login
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading

import config
from browser_manager import BrowserManager
from prompts import PromptQueue, count_prompts
from worker import BotRunner

logger = logging.getLogger("bot")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def cmd_login(count: int) -> None:
    manager = BrowserManager(count)
    logger.info(f"🌐 Membuka {count} browser untuk login manual...")
    for profile in manager.profiles:
        try:
            profile.launch(visible=True)
            logger.info(f"   ✅ {profile.name} — port {profile.port}")
        except Exception as e:
            logger.error(f"   ❌ {profile.name}: {e}")

    print(
        "\nLogin ke Flow Music di setiap jendela browser yang terbuka.\n"
        "Setelah selesai, tekan ENTER di sini. Browser akan tetap berjalan\n"
        "(cookies tersimpan di folder profiles/).\n"
    )
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        pass
    logger.info("Selesai. Browser dibiarkan terbuka.")


def cmd_run(count: int, prompt_file: str, visible: bool) -> None:
    total = count_prompts(prompt_file)
    if total == 0:
        logger.error(
            f"File prompt kosong atau tidak ditemukan: {prompt_file}\n"
            "Pisahkan setiap prompt dengan satu baris kosong."
        )
        return

    logger.info(f"📚 {total} prompt dari {prompt_file}")
    manager = BrowserManager(count)
    queue = PromptQueue(prompt_file, config.MAX_PROMPT_ATTEMPTS)
    stop_event = threading.Event()

    def on_status(index, state, message):
        logger.info(f"[Browser {index}] {state}: {message}")

    def on_progress(stats):
        logger.info(
            f"📊 selesai={stats['done']} gagal={stats['failed']} "
            f"sisa={stats['pending']} proses={stats['running']}"
        )

    runner = BotRunner(
        profiles=manager.profiles,
        queue=queue,
        stop_event=stop_event,
        on_status=on_status,
        on_progress=on_progress,
        hide_browsers=not visible,
        allow_resume=False,
    )
    try:
        runner.run()
    except KeyboardInterrupt:
        logger.warning("Dihentikan oleh user.")
        stop_event.set()
    finally:
        stats = queue.stats()
        logger.info(
            f"🏁 Selesai — sukses {stats['done']}, gagal {stats['failed']}, "
            f"sisa {stats['pending']}"
        )
        logger.info(f"📂 Hasil: {config.DOWNLOADS_DIR}")
        manager.close_all()


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Flow Music Bot (CLI)")
    parser.add_argument(
        "--browsers", type=int, default=config.DEFAULT_NUM_BROWSERS,
        help="Jumlah browser (default: 5)",
    )
    parser.add_argument(
        "--prompt", default=str(config.DEFAULT_PROMPT_FILE),
        help="File .txt berisi prompt",
    )
    parser.add_argument(
        "--visible", action="store_true",
        help="Tampilkan jendela browser saat bot bekerja",
    )
    parser.add_argument(
        "--login", action="store_true",
        help="Buka semua browser untuk login manual lalu keluar",
    )
    parser.add_argument(
        "--browser", default=None,
        help="Pilih browser: brave, chrome, chromium, edge, atau path binary",
    )
    parser.add_argument(
        "--list-browsers", action="store_true",
        help="Tampilkan browser yang terdeteksi lalu keluar",
    )
    args = parser.parse_args()

    if args.list_browsers:
        options = config.available_browsers()
        if not options:
            print("Tidak ada browser yang terdeteksi.")
        else:
            print("Browser yang terdeteksi:")
            for option in options:
                print(
                    f"  {option['id']:<10} {option['label']:<16} "
                    f"{option['path']}"
                )
        return

    if args.browser:
        try:
            config.set_browser_choice(args.browser)
        except ValueError as e:
            parser.error(str(e))
        logger.info(f"🌐 Browser dipilih: {config.get_browser_label()}")

    count = max(1, min(args.browsers, config.MAX_NUM_BROWSERS))
    if args.login:
        cmd_login(count)
    else:
        cmd_run(count, args.prompt, args.visible)


if __name__ == "__main__":
    main()
