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

from cleanup import consolidate_downloads, remove_duplicate_downloads
from config import (
    DOWNLOADS_DIR,
    DOWNLOAD_TEMP_DIR,
    GENERATE_MAX_WAIT_SECONDS,
    IDLE_POLL_SECONDS,
    PAUSE_AFTER_DOWNLOAD,
    PAUSE_BETWEEN_PROMPTS,
    SESSION_RECOVERY_ATTEMPTS,
    SESSION_RECOVERY_COOLDOWN_SECONDS,
    SESSION_RECOVERY_MAX_STREAK,
    WEBSITE_URL,
)
from flow_actions import (
    check_credits_or_rate_limit,
    close_menus,
    configure_downloads,
    detect_session_block,
    download_song_row,
    generate_and_wait_all_songs,
    input_prompt,
    prompt_is_pending,
    start_new_session,
    wait_for_login_state,
)
from prompts import PromptItem, PromptQueue

logger = logging.getLogger(__name__)

StatusCallback = Callable[[int, str, str], None]
ProgressCallback = Callable[[dict], None]


class _OrStop:
    """
    Objek stop gabungan yang punya antarmuka sama dengan threading.Event.

    Dipakai supaya satu worker bisa berhenti karena dua sebab:
    - tombol STOP global (semua browser), atau
    - tombol Stop pada kartu browser itu saja.

    Semua fungsi di `flow_actions` hanya memanggil `.is_set()`, sedangkan
    worker memakai `.wait()`, jadi keduanya disediakan di sini.
    """

    def __init__(self, *events):
        self._events = [event for event in events if event is not None]

    def is_set(self) -> bool:
        return any(event.is_set() for event in self._events)

    def wait(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.time() + timeout
        while True:
            if self.is_set():
                return True
            if deadline is not None and time.time() >= deadline:
                return False
            step = 0.1
            if deadline is not None:
                step = min(step, max(0.01, deadline - time.time()))
            time.sleep(step)


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
        on_idle: Callable[[int], None] | None = None,
        hide_browsers: bool = True,
        allow_resume: bool = True,
    ):
        self.profiles = profiles
        self.queue = queue
        self.stop_event = stop_event
        self.on_status = on_status or (lambda *_: None)
        self.on_progress = on_progress or (lambda *_: None)
        self.on_idle = on_idle
        self.hide_browsers = hide_browsers
        # Mode CLI tidak punya tombol Cek: browser yang error langsung
        # dihentikan seperti sebelumnya (prompt dikembalikan ke antrean).
        self.allow_resume = allow_resume

        # Kendali worker per browser. Browser yang error/limit TIDAK mematikan
        # bot: worker-nya "tertahan" (paused) dan prompt yang sedang dikerjakan
        # disimpan, supaya user bisa memperbaiki browser lalu menekan "Cek"
        # untuk melanjutkan + paste ulang prompt yang sama.
        self._cv = threading.Condition()
        self._active: dict[int, threading.Thread] = {}
        self._paused: set[int] = set()
        self._held: dict[int, PromptItem] = {}
        # Event Stop per browser. Diisi oleh _start_worker() dan dibaca worker.
        self._stop_requests: dict[int, threading.Event] = {}
        # Lagu yang sudah digenerate tetapi belum terunduh, disimpan bersama
        # prompt yang ditahan supaya "Cek" melanjutkan unduhan (bukan
        # menggenerate ulang dan membuang credits).
        self._held_songs: dict[int, list[dict]] = {}
        self._running = False

        # Ringkasan pemeriksaan file ganda di folder downloads saat bot
        # selesai (dipakai GUI untuk ringkasan akhir).
        self.last_cleanup: dict[str, int] | None = None

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

    def _wait_or_stop(self, seconds: float, stop=None) -> bool:
        """Tidur sambil memantau stop. Return True bila harus berhenti."""
        return (stop or self.stop_event).wait(timeout=seconds)

    def _cleanup_duplicate_downloads(self) -> None:
        """
        Pemeriksaan akhir: satukan hasil lalu buang salinan lagu ganda.

        Dijalankan setelah semua prompt habis dan bot selesai normal (bukan
        saat di-STOP), karena saat STOP masih ada file yang mungkin sedang
        ditulis. Pemeriksaan ini TIDAK membuka browser: semua browser memang
        mengunduh ke satu folder `downloads/`; sisa file dari folder kerja
        versi lama (kalau ada) diselamatkan dulu, lalu folder itu dibaca
        menjadi satu daftar, duplikatnya disusun, dan dihapus. Sisanya adalah
        lagu yang tidak duplikat.

        Lagu yang sama terunduh dua kali muncul sebagai "Judul (1).mp3"
        (penamaan Chrome) atau "Judul_1.mp3" (penamaan bot).
        """
        logger.info("🧹 Menyatukan hasil semua browser ke folder downloads...")
        moved = 0
        try:
            moved = consolidate_downloads([DOWNLOAD_TEMP_DIR], DOWNLOADS_DIR)
        except Exception as e:
            logger.warning(f"Gagal menyatukan folder download sementara: {e}")
        if moved:
            logger.info(f"📦 {moved} lagu dipindahkan ke {DOWNLOADS_DIR}.")

        logger.info("🧹 Memeriksa file lagu ganda (tanpa browser)...")
        try:
            result = remove_duplicate_downloads(DOWNLOADS_DIR)
        except Exception as e:
            logger.warning(f"Pemeriksaan file ganda gagal: {e}")
            return

        result["moved"] = int(result.get("moved", 0)) + moved
        self.last_cleanup = result
        scanned = result.get("scanned", 0)
        deleted = result.get("deleted", 0)
        freed = result.get("freed", 0)
        logger.info(f"🧹 {scanned} file audio dipindai.")
        if deleted:
            logger.info(
                f"🧹 {deleted} salinan lagu ganda dihapus "
                f"({freed / (1024 * 1024):.1f} MB dibebaskan)."
            )
        else:
            logger.info("🧹 Tidak ada salinan lagu ganda yang perlu dihapus.")
        different = result.get("kept_different", 0)
        if different:
            logger.info(
                f"🧹 {different} file mirip namanya tetapi isinya berbeda — "
                "dibiarkan (bukan duplikat)."
            )

    # --------------------------------------------------------------------------
    # Kendali worker per browser (tombol "Cek")
    # --------------------------------------------------------------------------
    def is_active(self, index: int) -> bool:
        """Worker browser ini masih hidup (bekerja / menunggu prompt)?"""
        with self._cv:
            return index in self._active

    def can_check(self, index: int) -> bool:
        """
        Tombol Cek boleh dipakai untuk browser ini?

        Cek tidak lagi hanya untuk browser yang error. Bisa juga dipakai untuk:
        - melanjutkan browser yang dihentikan dengan tombol Stop;
        - menjalankan browser yang baru dibuka di tengah bot berjalan;
        - menghidupkan lagi browser yang tadi selesai/idle padahal masih ada
          prompt tersisa.
        """
        with self._cv:
            if not self._running or self.stop_event.is_set():
                return False
            if index in self._active:
                return False
            if index in self._paused:
                return True
            return self.queue.remaining_prompts() > 0

    def request_stop(self, index: int) -> bool:
        """
        Minta worker satu browser berhenti; browser lain tetap bekerja.

        Return False bila browser itu memang tidak sedang bekerja.
        """
        with self._cv:
            if not self._running or self.stop_event.is_set():
                return False
            if index not in self._active:
                return False
            event = self._stop_requests.get(index)
            if event is None:
                event = threading.Event()
                self._stop_requests[index] = event
            if event.is_set():
                return False
            event.set()
            return True

    def resume(self, profile) -> bool:
        """
        Lanjutkan worker satu browser lewat tombol Cek.

        - Browser yang tertahan (error / di-Stop) memakai lagi prompt yang
          disimpan, termasuk daftar lagu yang belum sempat terunduh.
        - Browser yang belum punya worker (mis. baru dibuka di tengah jalan)
          ikut bekerja selama masih ada prompt tersisa.
        """
        with self._cv:
            if not self._running or self.stop_event.is_set():
                return False
            index = profile.index
            if index in self._active:
                return False

            if index in self._paused:
                item = self._held.pop(index, None)
                songs = self._held_songs.pop(index, None)
                self._paused.discard(index)
                self._stop_requests.pop(index, None)
                if profile not in self.profiles:
                    self.profiles.append(profile)
                logger.info(
                    f"[{profile.name}] 🔎 Cek diminta — melanjutkan browser."
                )
                # Dijalankan di dalam lock supaya run() tidak sempat
                # menyimpulkan "semua worker selesai" di antara discard()
                # dan start worker.
                self._start_worker(profile, item, songs)
                self._cv.notify_all()
                return True

            # Browser yang belum/tidak lagi punya worker.
            if self.queue.remaining_prompts() > 0:
                self._stop_requests.pop(index, None)
                self._held_songs.pop(index, None)
                if profile not in self.profiles:
                    self.profiles.append(profile)
                logger.info(
                    f"[{profile.name}] 🔎 Cek diminta — menjalankan browser."
                )
                self._start_worker(profile)
                self._cv.notify_all()
                return True
            return False

    def _hold(
        self,
        profile,
        item: PromptItem | None,
        pending_songs: list[dict] | None = None,
        reason: str = "",
    ) -> None:
        """Tandai browser tertahan; prompt-nya disimpan untuk dilanjutkan Cek."""
        if not self.allow_resume:
            # Tanpa GUI/Cek: jangan menggantung. Kembalikan prompt ke antrean
            # supaya browser lain bisa mengambilnya.
            if item is not None:
                try:
                    self.queue.requeue(
                        item, "browser error (mode CLI)", penalize=False
                    )
                except Exception as e:
                    logger.warning(f"[{profile.name}] Gagal requeue prompt: {e}")
            return

        if item is not None:
            try:
                self.queue.hold(item, reason or "browser tertahan — menunggu Cek")
            except Exception as e:
                logger.warning(f"[{profile.name}] Gagal menahan prompt: {e}")
        with self._cv:
            self._paused.add(profile.index)
            if item is not None:
                self._held[profile.index] = item
                if pending_songs:
                    self._held_songs[profile.index] = list(pending_songs)
                else:
                    self._held_songs.pop(profile.index, None)
            self._cv.notify_all()

    def _start_worker(
        self,
        profile,
        item: PromptItem | None = None,
        pending_songs: list[dict] | None = None,
    ) -> None:
        thread = threading.Thread(
            target=self._worker_entry,
            args=(profile, item, pending_songs),
            name=f"worker-{profile.index}",
            daemon=True,
        )
        with self._cv:
            # Event Stop baru untuk setiap worker. Kalau event lama masih
            # terpasang, event itu sudah kita buang sebelum start.
            self._stop_requests[profile.index] = threading.Event()
            self._active[profile.index] = thread
        thread.start()

    def _worker_entry(
        self,
        profile,
        item: PromptItem | None = None,
        pending_songs: list[dict] | None = None,
    ) -> None:
        try:
            self._worker(profile, item, pending_songs)
        except Exception as e:
            logger.error(f"[{profile.name}] Worker error: {e}")
            with self._cv:
                # Kalau user sudah menjalankan worker baru untuk index yang
                # sama, jangan tandai browser ini tertahan: itu akan membuat
                # run() menunggu Cek yang tidak diperlukan.
                superseded = (
                    self._active.get(profile.index) is not None
                    and self._active.get(profile.index)
                    is not threading.current_thread()
                )
            if not superseded:
                self._status(profile, "error", "Error — klik Cek")
                self._hold(profile, None)
        finally:
            with self._cv:
                # Hanya hapus catatan milik thread ini. Kalau user sudah
                # menekan Cek cepat-cepat, worker BARU mungkin sudah terdaftar
                # untuk index yang sama — jangan sampai catatannya terhapus.
                if self._active.get(profile.index) is threading.current_thread():
                    self._active.pop(profile.index, None)
                    self._stop_requests.pop(profile.index, None)
                self._cv.notify_all()
            if self.on_idle is not None:
                try:
                    self.on_idle(profile.index)
                except Exception:
                    pass

    def _recover_session(self, profile, page, reason: str, stop=None) -> bool:
        """
        Sesi terindikasi limit/macet: beri tahu user lewat log + status kartu,
        lalu coba buat session baru (klik sidebar / reload). Berhenti hanya
        kalau semua percobaan gagal.
        """
        stop = stop or self.stop_event
        if stop.is_set():
            return False

        logger.warning(
            f"[{profile.name}] 🔁 Sesi macet ({reason}) — mencoba session baru..."
        )
        attempts = max(1, SESSION_RECOVERY_ATTEMPTS)
        for attempt in range(1, attempts + 1):
            self._status(
                profile,
                "recovering",
                f"Sesi macet — session baru ({attempt}/{attempts})",
            )
            try:
                ok = start_new_session(page, stop_event=stop)
            except Exception as e:
                logger.error(f"[{profile.name}] Pemulihan sesi gagal: {e}")
                ok = False

            if ok:
                logger.info(
                    f"[{profile.name}] ✅ Session baru siap — bot lanjut bekerja."
                )
                self._status(profile, "ready", "Session baru — lanjut")
                return True

            if attempt < attempts:
                logger.warning(
                    f"[{profile.name}] ⚠️ Pemulihan sesi belum berhasil "
                    f"({attempt}/{attempts}); coba lagi dalam "
                    f"{SESSION_RECOVERY_COOLDOWN_SECONDS} detik."
                )
                self._status(
                    profile,
                    "waiting",
                    f"Session macet — coba lagi ({attempt}/{attempts})",
                )
                if self._wait_or_stop(SESSION_RECOVERY_COOLDOWN_SECONDS, stop):
                    return False

        logger.error(
            f"[{profile.name}] ❌ Gagal membuat session baru otomatis. "
            f"Buka browser ini, klik tombol 'session baru' di sidebar secara "
            f"manual, lalu tekan Cek."
        )
        self._status(
            profile, "waiting", "Sesi macet — klik session baru manual"
        )
        return False

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
    def _download_songs(self, profile, page, item, songs, stop) -> list[dict]:
        """
        Unduh semua lagu satu prompt (maks. 3 putaran).

        Return daftar lagu yang MASIH gagal. Kalau user menekan Stop di
        tengah proses, lagu yang belum sempat dicoba tetap ikut dikembalikan
        supaya tidak hilang dan bisa dilanjutkan lewat Cek.
        """
        pending = list(songs)
        success = 0
        total = len(songs)

        for round_no in range(1, 4):
            if stop.is_set():
                break
            still_pending: list[dict] = []
            stop_seen = False
            for pos, song in enumerate(pending):
                if stop.is_set():
                    # Lagu ini dan sisanya belum dicoba — jangan hilang.
                    still_pending.extend(pending[pos:])
                    stop_seen = True
                    break
                self._status(
                    profile,
                    "downloading",
                    f"Download {success + 1}/{total}"
                    + ("" if round_no == 1 else f" (ulang {round_no - 1})"),
                )
                if download_song_row(
                    page,
                    song,
                    profile.download_dir,
                    DOWNLOADS_DIR,
                    stop_event=stop,
                ):
                    success += 1
                    self._wait_or_stop(PAUSE_AFTER_DOWNLOAD, stop)
                else:
                    logger.warning(
                        f"[{profile.name}] Gagal download "
                        f"'{song.get('title', '')[:40]}' "
                        f"(percobaan {round_no}/3)"
                    )
                    still_pending.append(song)
                    close_menus(page)
                    self._wait_or_stop(1, stop)

            pending = still_pending
            if stop_seen or not pending:
                break
            if round_no < 3:
                logger.warning(
                    f"[{profile.name}] {len(pending)} lagu belum terunduh; "
                    f"mencoba ulang setelah jeda."
                )
                if self._wait_or_stop(5, stop):
                    break

        return pending

    def _worker(
        self,
        profile,
        resume_item: PromptItem | None = None,
        resume_songs: list[dict] | None = None,
    ) -> None:
        from playwright.sync_api import sync_playwright

        pw = None
        download_session = None  # jaga referensi CDP tetap hidup
        # Prompt yang sedang dipegang worker ini (dipakai bila worker error
        # di luar loop, supaya prompt tidak hilang dan bisa dilanjutkan Cek).
        current_item: PromptItem | None = resume_item

        # Stop per browser: event ini hanya milik worker ini. `stop` menggabung
        # Stop kartu + STOP global, supaya tunggu login, generate, dan download
        # langsung berhenti dari tombol mana pun yang ditekan.
        with self._cv:
            local_stop = self._stop_requests.get(profile.index)
            if local_stop is None:
                local_stop = threading.Event()
                self._stop_requests[profile.index] = local_stop
        stop = _OrStop(self.stop_event, local_stop)

        def stopped_here() -> bool:
            """User menekan Stop di kartu browser ini (bukan STOP global)."""
            return local_stop.is_set() and not self.stop_event.is_set()

        def hold_for_check(
            item: PromptItem | None,
            pending_songs: list[dict] | None = None,
        ) -> None:
            """Simpan prompt + lagu sisa supaya bisa dilanjutkan lewat Cek."""
            if stopped_here():
                logger.warning(
                    f"[{profile.name}] ⏹️ Stop per browser — prompt "
                    f"{('#' + str(item.index + 1)) if item else '(tidak ada)'} "
                    f"ditahan; klik Cek untuk melanjutkan."
                )
            self._hold(
                profile,
                item,
                pending_songs=pending_songs,
                reason="dihentikan user",
            )

        try:
            self._status(profile, "connecting", "Menghubungkan ke browser...")
            pw = sync_playwright().start()

            browser = None
            last_error = ""
            for attempt in range(20):
                if stop.is_set():
                    if stopped_here():
                        hold_for_check(current_item, resume_songs)
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
                    if self._wait_or_stop(1.0, stop):
                        if stopped_here():
                            hold_for_check(current_item, resume_songs)
                        return

            if browser is None:
                logger.error(
                    f"[{profile.name}] Tidak bisa connect: {last_error}"
                )
                self._status(profile, "error", "Tidak bisa connect — klik Cek")
                self._hold(profile, current_item, pending_songs=resume_songs)
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
                self._status(profile, "error", "Tidak ada tab — klik Cek")
                self._hold(profile, current_item, pending_songs=resume_songs)
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
                page, timeout=30, stop_event=stop
            )
            if stopped_here():
                hold_for_check(current_item, resume_songs)
                return
            if logged_in is not True:
                logger.error(f"[{profile.name}] Perlu login: {message}")
                self._status(profile, "login_required", "Perlu login — klik Cek")
                self._hold(profile, current_item, pending_songs=resume_songs)
                return
            self._status(profile, "ready", "Siap")

            # ------------------------------------------------------------------
            # Loop utama
            # ------------------------------------------------------------------
            final_state, final_message = "done", "Selesai"
            worked_any = False
            recovery_streak = 0
            error_streak = 0

            # Prompt yang sebelumnya tertahan (browser error lalu diperbaiki
            # user) langsung dipakai lagi — isinya dipaste ulang. Prompt baru
            # di-"resume" setelah login OK, supaya kalau login gagal prompt
            # tetap tertahan dan bisa dicoba lagi lewat Cek.
            pending_item = resume_item
            # Lagu yang sudah selesai digenerate sebelum tombol Stop ditekan.
            # Saat Cek, unduhannya dilanjutkan tanpa meminta generate ulang
            # (credits tidak terbuang).
            pending_songs = list(resume_songs) if resume_songs else None

            def recover(reason: str) -> bool:
                """Pemulihan sesi + pengaman agar tidak berputar tanpa henti."""
                nonlocal recovery_streak
                recovery_streak += 1
                if recovery_streak > SESSION_RECOVERY_MAX_STREAK:
                    logger.error(
                        f"[{profile.name}] Sudah {recovery_streak - 1}× "
                        f"berturut-turut kena limit sesi. Browser akan "
                        f"ditahan supaya tidak berputar terus; perbaiki "
                        f"(session baru / akun) lalu tekan Cek."
                    )
                    return False
                return self._recover_session(profile, page, reason, stop)

            while not stop.is_set():
                item: PromptItem | None = None
                resume_downloads: list[dict] | None = None
                if pending_item is not None:
                    if pending_item.status == "running":
                        item = pending_item
                        resume_downloads = pending_songs
                        try:
                            self.queue.resume(item)
                            logger.info(
                                f"[{profile.name}] 🔁 Lanjut prompt "
                                f"#{item.index + 1} — paste ulang."
                            )
                        except Exception as e:
                            logger.warning(
                                f"[{profile.name}] Prompt tertahan tidak bisa "
                                f"dilanjutkan: {e}"
                            )
                    pending_item = None
                    pending_songs = None
                if item is None:
                    item = self.queue.take(profile.index)
                if item is None:
                    current_item = None
                    if self.queue.has_work():
                        # Semua prompt sudah dipegang browser lain. Tetap
                        # hidup sebentar supaya bisa mengambil prompt yang
                        # di-requeue kalau browser lain gagal.
                        self._status(
                            profile, "idle", "Tidak ada prompt — menunggu"
                        )
                        if self._wait_or_stop(IDLE_POLL_SECONDS, stop):
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

                current_item = item
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
                    if resume_downloads is not None:
                        # Lagu sudah digenerate sebelum tombol Stop ditekan:
                        # lanjutkan unduhannya tanpa generate ulang.
                        logger.info(
                            f"[{profile.name}] 🔁 Lanjut unduh "
                            f"{len(resume_downloads)} lagu prompt "
                            f"#{item.index + 1} — tanpa generate ulang."
                        )
                        self._status(
                            profile,
                            "downloading",
                            f"Lanjut unduh {len(resume_downloads)} lagu "
                            f"(#{item.index + 1})",
                        )
                        close_menus(page)
                        remaining = self._download_songs(
                            profile, page, item, resume_downloads, stop
                        )
                        if stop.is_set():
                            if remaining:
                                if stopped_here():
                                    hold_for_check(item, remaining)
                                else:
                                    self.queue.requeue(
                                        item, "bot dihentikan saat download"
                                    )
                            else:
                                self.queue.complete(item)
                            self._progress()
                            break
                        if remaining:
                            logger.error(
                                f"[{profile.name}] {len(remaining)} lagu gagal "
                                f"diunduh. Prompt #{item.index + 1} ditahan."
                            )
                            hold_for_check(item, remaining)
                            self._progress()
                            final_state, final_message = (
                                "error",
                                f"Download gagal — klik Cek "
                                f"({len(remaining)} lagu)",
                            )
                            break
                        self.queue.complete(item)
                        recovery_streak = 0
                        error_streak = 0
                        self._progress()
                        self._status(
                            profile,
                            "ready",
                            f"Selesai {len(resume_downloads)} lagu",
                        )
                        if self._wait_or_stop(PAUSE_BETWEEN_PROMPTS, stop):
                            break
                        continue

                    # Pastikan tidak ada sisa menu download/overlay yang
                    # menutupi kolom prompt sebelum prompt baru diisi.
                    close_menus(page)
                    paste_ok = input_prompt(page, item.text)
                    if not paste_ok:
                        # Satu kesempatan lagi: kadang halaman belum siap.
                        self._wait_or_stop(1.5, stop)
                        paste_ok = input_prompt(page, item.text)

                    if not paste_ok:
                        # Gejala limit yang dikhawatirkan user: prompt tidak
                        # bisa di-paste. Coba buat session baru lalu isi lagi.
                        reason = (
                            detect_session_block(page) or "prompt tidak bisa diisi"
                        )
                        if recover(reason):
                            close_menus(page)
                            paste_ok = input_prompt(page, item.text)

                    if not paste_ok:
                        if stopped_here():
                            # Stop ditekan saat pemulihan sesi/mengetik.
                            hold_for_check(item)
                            self._progress()
                            final_state, final_message = (
                                "stopped",
                                f"Dihentikan — klik Cek (#{item.index + 1})",
                            )
                            break
                        # Kegagalan karena sesi/lingkungan, bukan salah prompt:
                        # prompt DITAHAN untuk browser ini (bukan dikembalikan
                        # ke antrean umum), supaya setelah user memperbaiki
                        # browser dan menekan Cek, prompt yang sama dipaste lagi.
                        self._hold(profile, item)
                        self._progress()
                        close_menus(page)
                        logger.error(
                            f"[{profile.name}] Sesi masih macet setelah "
                            f"pemulihan. Browser tertahan (prompt "
                            f"#{item.index + 1}); perbaiki session baru secara "
                            f"manual, lalu tekan Cek."
                        )
                        final_state, final_message = (
                            "error",
                            f"Macet — klik Cek (#{item.index + 1})",
                        )
                        break

                    self._status(
                        profile, "generating", f"Prompt #{item.index + 1} • generate"
                    )
                    songs = generate_and_wait_all_songs(
                        page,
                        max_wait_seconds=GENERATE_MAX_WAIT_SECONDS,
                        stop_event=stop,
                        prompt_text=item.text,
                    )

                    if stop.is_set():
                        if stopped_here():
                            # Lagu sudah jadi — simpan supaya Cek tinggal
                            # mengunduhnya, bukan generate ulang.
                            hold_for_check(item, songs)
                            final_state, final_message = (
                                "stopped",
                                f"Dihentikan — klik Cek (#{item.index + 1})",
                            )
                        else:
                            self.queue.requeue(item, "bot dihentikan")
                        self._progress()
                        break

                    if not songs:
                        close_menus(page)

                        # Bedakan "credits habis" dari "sesi macet/limit".
                        # Pesan sesi dicoba dipulihkan dulu dengan session baru.
                        credits_exhausted = check_credits_or_rate_limit(page)
                        session_reason = detect_session_block(page)
                        if not session_reason:
                            try:
                                if prompt_is_pending(page, item.text):
                                    session_reason = (
                                        "prompt masih di kolom input "
                                        "(tidak terkirim)"
                                    )
                            except Exception:
                                pass

                        if session_reason and not credits_exhausted:
                            if recover(session_reason):
                                self.queue.requeue(
                                    item,
                                    "sesi macet — session baru",
                                    penalize=False,
                                )
                                self._progress()
                                self._status(
                                    profile,
                                    "waiting",
                                    "Session baru — ulangi prompt",
                                )
                                if self._wait_or_stop(3, stop):
                                    break
                                continue
                            if stopped_here():
                                hold_for_check(item)
                                self._progress()
                                final_state, final_message = (
                                    "stopped",
                                    f"Dihentikan — klik Cek (#{item.index + 1})",
                                )
                                break
                            self._hold(profile, item)
                            self._progress()
                            logger.error(
                                f"[{profile.name}] Browser tertahan karena "
                                f"sesi tidak bisa dipulihkan otomatis "
                                f"(prompt #{item.index + 1}). Perbaiki secara "
                                f"manual, lalu tekan Cek."
                            )
                            final_state, final_message = (
                                "error",
                                f"Macet — klik Cek (#{item.index + 1})",
                            )
                            break

                        if credits_exhausted:
                            # Limit/credits habis: tetap tandai error dan tahan
                            # prompt, supaya setelah akun/sesi diganti user
                            # bisa menekan Cek untuk melanjutkan prompt ini.
                            self._hold(profile, item)
                            self._progress()
                            logger.error(
                                f"[{profile.name}] Limit/credits habis "
                                f"(prompt #{item.index + 1} ditahan). Ganti "
                                f"akun/sesi lalu tekan Cek."
                            )
                            final_state, final_message = (
                                "credits",
                                f"Limit — klik Cek (#{item.index + 1})",
                            )
                            break
                        self.queue.requeue(item, "tidak ada lagu yang dihasilkan")
                        self._progress()
                        self._status(profile, "waiting", "Menunggu percobaan berikutnya")
                        if self._wait_or_stop(5, stop):
                            break
                        continue

                    logger.info(f"[{profile.name}] 🎼 {len(songs)} lagu untuk diunduh.")
                    pending_downloads = self._download_songs(
                        profile, page, item, songs, stop
                    )

                    if stop.is_set():
                        if stopped_here():
                            if pending_downloads:
                                hold_for_check(item, pending_downloads)
                            else:
                                self.queue.complete(item)
                        else:
                            self.queue.requeue(item, "bot dihentikan saat download")
                        self._progress()
                        break

                    if not pending_downloads:
                        self.queue.complete(item)
                        recovery_streak = 0
                        error_streak = 0
                        self._status(
                            profile,
                            "ready",
                            f"Selesai {len(songs)} lagu",
                        )
                    else:
                        # Jangan lanjut ke prompt baru: lagu sudah terlanjur
                        # dibuat dan belum tersimpan. Prompt DITAHAN (bukan
                        # ditandai gagal) supaya setelah browser diperbaiki
                        # dan Cek ditekan, lagu yang sama tinggal diunduh —
                        # tanpa generate ulang.
                        logger.error(
                            f"[{profile.name}] {len(pending_downloads)} lagu gagal "
                            f"diunduh. Prompt ditahan agar tidak mengirim "
                            f"prompt baru dan tidak membuang credits."
                        )
                        hold_for_check(item, pending_downloads)
                        self._progress()
                        self._status(
                            profile,
                            "error",
                            f"Download gagal — klik Cek ({len(pending_downloads)} lagu)",
                        )
                        final_state, final_message = (
                            "error",
                            f"Download gagal — klik Cek ({len(pending_downloads)} lagu)",
                        )
                        break

                    self._progress()
                    if self._wait_or_stop(PAUSE_BETWEEN_PROMPTS, stop):
                        break

                except Exception as e:
                    if stopped_here():
                        # Stop per browser bisa membuat operasi panjang
                        # melempar error; itu bukan kegagalan asli.
                        hold_for_check(item)
                        self._progress()
                        final_state, final_message = (
                            "stopped",
                            f"Dihentikan — klik Cek (#{item.index + 1})",
                        )
                        break
                    logger.error(f"[{profile.name}] Error saat memproses prompt: {e}")
                    # Browser/tab ditutup user di tengah jalan? Jangan lanjut
                    # memakai koneksi yang sudah mati.
                    try:
                        alive = browser.is_connected() and not page.is_closed()
                    except Exception:
                        alive = False

                    if not alive:
                        # Browser/tab ditutup user (atau kepencet): tahan
                        # prompt-nya, lalu setelah browser dibuka lagi dan
                        # Cek ditekan, prompt yang sama dipaste ulang.
                        self._hold(profile, item)
                        self._progress()
                        logger.error(
                            f"[{profile.name}] Browser ditutup. Prompt "
                            f"#{item.index + 1} ditahan; buka lagi lalu klik Cek."
                        )
                        final_state, final_message = (
                            "error",
                            f"Tertutup — klik Cek (#{item.index + 1})",
                        )
                        break

                    try:
                        close_menus(page)
                    except Exception:
                        pass
                    error_streak += 1
                    if error_streak >= 3:
                        # Error berulang (mis. alur "titik 3" kepencet dan
                        # halaman jadi ngaco): berhenti dari browser ini dan
                        # tunggu user memperbaikinya lalu klik Cek.
                        self._hold(profile, item)
                        self._progress()
                        logger.error(
                            f"[{profile.name}] Error berulang ({error_streak}×): "
                            f"{e}. Browser tertahan; perbaiki lalu tekan Cek."
                        )
                        final_state, final_message = (
                            "error",
                            f"Error — klik Cek (#{item.index + 1})",
                        )
                        break
                    self.queue.requeue(item, f"error: {e}")
                    self._progress()
                    self._status(profile, "waiting", f"Error: {e}")
                    if self._wait_or_stop(3, stop):
                        break

            if self.stop_event.is_set():
                self._status(profile, "stopped", "Dihentikan")
            elif stopped_here():
                self._status(
                    profile,
                    "stopped",
                    final_message
                    if final_message.startswith("Dihentikan")
                    else "Dihentikan — klik Cek",
                )
            else:
                self._status(profile, final_state, final_message)

        except Exception as e:
            if stopped_here():
                # Lagu sisa hanya milik prompt yang tadi dilanjutkan; jangan
                # dipasang ke prompt lain yang kebetulan sedang dipegang.
                held_songs = resume_songs if current_item is resume_item else None
                hold_for_check(current_item, held_songs)
                self._status(profile, "stopped", "Dihentikan — klik Cek")
            else:
                logger.error(f"[{profile.name}] Worker error: {e}")
                self._status(profile, "error", "Error — klik Cek")
                self._hold(profile, current_item)
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

        with self._cv:
            self._running = True

        for profile in ready:
            self._start_worker(profile)
            time.sleep(0.2)

        # Tunggu sampai semua worker selesai. Bot sengaja tetap hidup bila:
        # - ada browser tertahan (menunggu tombol Cek), atau
        # - semua browser di-Stop padahal prompt masih tersisa (supaya Cek /
        #   pemeriksaan file ganda bisa dipakai, lalu bot dilanjutkan lagi).
        with self._cv:
            while True:
                if not self._active:
                    if self.stop_event.is_set():
                        break
                    if not self.allow_resume:
                        # Mode CLI: tidak ada tombol Cek, jangan menggantung.
                        break
                    if not self._paused and not self.queue.remaining_prompts():
                        break
                self._cv.wait(timeout=0.5)
            self._running = False

        if self.stop_event.is_set():
            self.queue.release_running()
        else:
            self._cleanup_duplicate_downloads()

        self._progress()
        logger.info("=" * 66)
        logger.info("🏁 BOT SELESAI")
        logger.info("=" * 66)
