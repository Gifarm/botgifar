"""
Mesin bot: menjalankan satu worker per browser, berbagi satu antrean prompt.

Setiap worker membuat koneksi Playwright sendiri di thread-nya, karena
Playwright sync API tidak boleh dipakai lintas thread.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from config import (
    DOWNLOADS_DIR,
    GENERATE_MAX_WAIT_SECONDS,
    IDLE_POLL_SECONDS,
    PAUSE_AFTER_DOWNLOAD,
    PAUSE_BETWEEN_PROMPTS,
    WEBSITE_URL,
)
from flow_actions import (
    check_credits_or_rate_limit,
    close_menus,
    configure_downloads,
    download_song_row,
    generate_and_wait_all_songs,
    input_prompt,
    wait_for_login_state,
)
from prompts import PromptQueue

logger = logging.getLogger(__name__)

StatusCallback = Callable[[int, str, str], None]
ProgressCallback = Callable[[dict], None]


# ==============================================================================
# HELPER
# ==============================================================================
def _pick_page(browser):
    pages = []
    for context in browser.contexts:
        pages.extend(context.pages)
    for page in pages:
        try:
            if "flowmusic.app" in page.url:
                return page
        except Exception:
            continue
    return pages[0] if pages else None


class BotRunner:
    """Menjalankan bot untuk sekumpulan BrowserProfile."""

    def __init__(
        self,
        profiles: list,
        queue: PromptQueue,
        stop_event: threading.Event,
        on_status: StatusCallback | None = None,
        on_progress: ProgressCallback | None = None,
        hide_browsers: bool = True,
    ):
        self.profiles = profiles
        self.queue = queue
        self.stop_event = stop_event
        self.on_status = on_status or (lambda *_: None)
        self.on_progress = on_progress or (lambda *_: None)
        self.hide_browsers = hide_browsers

    # --------------------------------------------------------------------------
    # Utilitas
    # --------------------------------------------------------------------------
    def _status(self, profile, state: str, message: str) -> None:
        try:
            self.on_status(profile.index, state, message)
        except Exception:
            pass

    def _progress(self) -> None:
        try:
            self.on_progress(self.queue.stats())
        except Exception:
            pass

    def _wait_or_stop(self, seconds: float) -> bool:
        """Tidur sambil memantau stop. Return True bila harus berhenti."""
        return self.stop_event.wait(timeout=seconds)

    # --------------------------------------------------------------------------
    # Launch semua browser
    # --------------------------------------------------------------------------
    def _launch_all(self) -> list:
        """Luncurkan semua browser yang belum jalan. Return profil yang siap."""
        ready: list = []
        lock = threading.Lock()

        def launch_one(profile):
            if self.stop_event.is_set():
                return
            self._status(profile, "launching", "Membuka browser...")
            try:
                # Browser yang kartunya sudah "Sembunyi" jangan dimunculkan
                # lagi saat bot mulai — biarkan bekerja di belakang layar.
                keep_hidden = getattr(profile, "keep_hidden", False)
                profile.launch(visible=not self.hide_browsers and not keep_hidden)
                self._status(profile, "connecting", "Menghubungkan...")
                with lock:
                    ready.append(profile)
            except Exception as e:
                logger.error(f"{profile.name} gagal dibuka: {e}")
                self._status(profile, "error", f"Gagal membuka: {e}")

        threads = [
            threading.Thread(target=launch_one, args=(profile,), daemon=True)
            for profile in self.profiles
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return ready

    # --------------------------------------------------------------------------
    # Worker per browser
    # --------------------------------------------------------------------------
    def _worker(self, profile) -> None:
        from playwright.sync_api import sync_playwright

        pw = None
        download_session = None  # jaga referensi CDP tetap hidup
        try:
            self._status(profile, "connecting", "Menghubungkan ke browser...")
            pw = sync_playwright().start()

            browser = None
            last_error = ""
            for attempt in range(20):
                if self.stop_event.is_set():
                    return
                try:
                    browser = pw.chromium.connect_over_cdp(
                        f"http://127.0.0.1:{profile.port}", timeout=10_000
                    )
                    break
                except Exception as e:
                    last_error = str(e)
                    if attempt == 0:
                        logger.warning(
                            f"[{profile.name}] Connect gagal, mencoba ulang..."
                        )
                    if self._wait_or_stop(1.0):
                        return

            if browser is None:
                self._status(profile, "error", f"Tidak bisa connect: {last_error}")
                return

            # Arahkan download ke folder khusus browser ini SEDINI MUNGKIN —
            # sebelum navigasi/login — supaya tab yang sudah terbuka pun ikut
            # memakai folder project (bukan ~/Downloads).
            try:
                download_session = configure_downloads(browser, profile.download_dir)
            except Exception as e:
                logger.warning(f"[{profile.name}] Download behavior gagal diset: {e}")

            page = _pick_page(browser)
            if page is None:
                context = browser.contexts[0] if browser.contexts else None
                page = context.new_page() if context else None
            if page is None:
                self._status(profile, "error", "Tidak ada tab untuk dipakai.")
                return

            # Pastikan berada di Flow Music.
            try:
                if "flowmusic.app" not in page.url:
                    page.goto(WEBSITE_URL, wait_until="domcontentloaded")
                    page.wait_for_timeout(3_000)
            except Exception as e:
                logger.warning(f"[{profile.name}] Gagal membuka situs: {e}")

            # Pastikan sudah login. Kalau status tidak pasti, JANGAN lanjut:
            # lebih baik prompt tetap di antrean daripada dipakai di halaman
            # marketing yang kebetulan punya kolom demo.
            logged_in, message = wait_for_login_state(
                page, timeout=30, stop_event=self.stop_event
            )
            if logged_in is not True:
                logger.error(f"[{profile.name}] Perlu login: {message}")
                self._status(profile, "login_required", message)
                return
            self._status(profile, "ready", "Siap")

            # ------------------------------------------------------------------
            # Loop utama
            # ------------------------------------------------------------------
            final_state, final_message = "done", "Selesai"
            worked_any = False
            while not self.stop_event.is_set():
                item = self.queue.take(profile.index)
                if item is None:
                    if self.queue.has_work():
                        # Semua prompt sudah dipegang browser lain. Tetap
                        # hidup sebentar supaya bisa mengambil prompt yang
                        # di-requeue kalau browser lain gagal.
                        self._status(
                            profile, "idle", "Tidak ada prompt — menunggu"
                        )
                        if self._wait_or_stop(IDLE_POLL_SECONDS):
                            break
                        continue
                    if worked_any:
                        # Browser ini sempat bekerja, lalu antrean habis.
                        self._status(profile, "done", "Selesai — prompt habis")
                        final_state, final_message = "done", "Selesai — prompt habis"
                    else:
                        # Tidak pernah dapat prompt (mis. 5 browser, 4 prompt).
                        self._status(profile, "idle", "Tidak ada prompt — idle")
                        final_state, final_message = "idle", "Tidak ada prompt — idle"
                    break

                self._progress()
                worked_any = True
                logger.info(
                    f"\n{'='*66}\n"
                    f"[{profile.name}] 📝 Prompt #{item.index + 1} "
                    f"(percobaan {item.attempts}/{self.queue.max_attempts})\n"
                    f"{'='*66}"
                )
                self._status(
                    profile, "working", f"Prompt #{item.index + 1}"
                )

                try:
                    # Pastikan tidak ada sisa menu download/overlay yang
                    # menutupi kolom prompt sebelum prompt baru diisi.
                    close_menus(page)
                    if not input_prompt(page, item.text):
                        self.queue.requeue(item, "gagal mengisi prompt")
                        self._progress()
                        close_menus(page)
                        if self._wait_or_stop(3):
                            break
                        continue

                    self._status(
                        profile, "generating", f"Prompt #{item.index + 1} • generate"
                    )
                    songs = generate_and_wait_all_songs(
                        page,
                        max_wait_seconds=GENERATE_MAX_WAIT_SECONDS,
                        stop_event=self.stop_event,
                    )

                    if self.stop_event.is_set():
                        self.queue.requeue(item, "bot dihentikan")
                        self._progress()
                        break

                    if not songs:
                        close_menus(page)
                        if check_credits_or_rate_limit(page):
                            self.queue.requeue(item, "credits habis / limit")
                            self._progress()
                            self._status(
                                profile, "credits", "Credits habis — browser berhenti"
                            )
                            final_state, final_message = (
                                "credits",
                                "Credits habis — browser berhenti",
                            )
                            break
                        self.queue.requeue(item, "tidak ada lagu yang dihasilkan")
                        self._progress()
                        self._status(profile, "waiting", "Menunggu percobaan berikutnya")
                        if self._wait_or_stop(5):
                            break
                        continue

                    logger.info(f"[{profile.name}] 🎼 {len(songs)} lagu untuk diunduh.")
                    pending_downloads = list(songs)
                    success = 0

                    # Semua lagu HARUS selesai diunduh sebelum prompt baru
                    # diambil. Kalau gagal, ulangi dulu (bukan langsung
                    # mengirim prompt berikutnya).
                    for round_no in range(1, 4):
                        if self.stop_event.is_set():
                            break
                        still_pending = []
                        for song in pending_downloads:
                            if self.stop_event.is_set():
                                break
                            self._status(
                                profile,
                                "downloading",
                                f"Download {success + 1}/{len(songs)}"
                                + ("" if round_no == 1 else f" (ulang {round_no - 1})"),
                            )
                            if download_song_row(
                                page,
                                song,
                                profile.download_dir,
                                DOWNLOADS_DIR,
                                stop_event=self.stop_event,
                            ):
                                success += 1
                                self._wait_or_stop(PAUSE_AFTER_DOWNLOAD)
                            else:
                                logger.warning(
                                    f"[{profile.name}] Gagal download "
                                    f"'{song.get('title', '')[:40]}' "
                                    f"(percobaan {round_no}/3)"
                                )
                                still_pending.append(song)
                                close_menus(page)
                                self._wait_or_stop(1)

                        pending_downloads = still_pending
                        if not pending_downloads or self.stop_event.is_set():
                            break
                        if round_no < 3:
                            logger.warning(
                                f"[{profile.name}] {len(pending_downloads)} lagu "
                                f"belum terunduh; mencoba ulang setelah jeda."
                            )
                            if self._wait_or_stop(5):
                                break

                    if self.stop_event.is_set():
                        self.queue.requeue(item, "bot dihentikan saat download")
                        self._progress()
                        break

                    if not pending_downloads:
                        self.queue.complete(item)
                        self._status(
                            profile,
                            "ready",
                            f"Selesai {success}/{len(songs)} lagu",
                        )
                    else:
                        # Jangan lanjut ke prompt baru: lagu sudah terlanjur
                        # dibuat dan belum tersimpan.
                        logger.error(
                            f"[{profile.name}] {len(pending_downloads)} lagu gagal "
                            f"diunduh. Worker dihentikan agar tidak mengirim "
                            f"prompt baru."
                        )
                        self.queue.fail(
                            item,
                            f"{len(pending_downloads)}/{len(songs)} lagu gagal diunduh",
                        )
                        self._progress()
                        self._status(
                            profile,
                            "error",
                            f"Download gagal ({len(pending_downloads)} lagu)",
                        )
                        final_state, final_message = (
                            "error",
                            f"Download gagal ({len(pending_downloads)} lagu)",
                        )
                        break

                    self._progress()
                    if self._wait_or_stop(PAUSE_BETWEEN_PROMPTS):
                        break

                except Exception as e:
                    logger.error(f"[{profile.name}] Error saat memproses prompt: {e}")
                    # Browser/tab ditutup user di tengah jalan? Jangan lanjut
                    # memakai koneksi yang sudah mati.
                    try:
                        alive = browser.is_connected() and not page.is_closed()
                    except Exception:
                        alive = False

                    if not alive:
                        self.queue.requeue(item, "browser ditutup")
                        self._progress()
                        self._status(profile, "error", "Browser ditutup")
                        logger.error(f"[{profile.name}] Browser ditutup, worker berhenti.")
                        final_state, final_message = "error", "Browser ditutup"
                        break

                    try:
                        close_menus(page)
                    except Exception:
                        pass
                    self.queue.requeue(item, f"error: {e}")
                    self._progress()
                    self._status(profile, "waiting", f"Error: {e}")
                    if self._wait_or_stop(3):
                        break

            if self.stop_event.is_set():
                self._status(profile, "stopped", "Dihentikan")
            else:
                self._status(profile, final_state, final_message)

        except Exception as e:
            logger.error(f"[{profile.name}] Worker error: {e}")
            self._status(profile, "error", str(e))
        finally:
            try:
                if pw:
                    pw.stop()
            except Exception:
                pass

    # --------------------------------------------------------------------------
    # Run
    # --------------------------------------------------------------------------
    def run(self) -> None:
        logger.info("=" * 66)
        logger.info("🚀 BOT DIMULAI")
        logger.info("=" * 66)

        ready = self._launch_all()
        if not ready:
            logger.error("❌ Tidak ada browser yang siap.")
            self.queue.release_running()
            return

        logger.info(f"🎯 {len(ready)} browser siap bekerja.")
        self._progress()

        threads = []
        for profile in ready:
            thread = threading.Thread(
                target=self._worker,
                args=(profile,),
                name=f"worker-{profile.index}",
                daemon=True,
            )
            thread.start()
            threads.append(thread)
            time.sleep(0.2)

        for thread in threads:
            thread.join()

        if self.stop_event.is_set():
            self.queue.release_running()

        self._progress()
        logger.info("=" * 66)
        logger.info("🏁 BOT SELESAI")
        logger.info("=" * 66)
