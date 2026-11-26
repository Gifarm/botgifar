"""
Manajemen proses browser per profil.

Tugas modul ini:
- meluncurkan Chrome/Chromium/Brave dengan profil terpisah + port debugging,
- menyambung lewat CDP untuk menampilkan / menyembunyikan jendela,
- memeriksa status login Flow Music,
- menutup browser yang diluncurkan aplikasi.

Catatan penting soal "background":
- Jendela browser TIDAK dipindah ke luar layar (GNOME/Wayland mengabaikannya),
  melainkan diminimalkan lewat CDP.
- Flag anti-throttle di config membuat halaman tetap aktif walau diminimalkan,
  jadi bot tetap bekerja meskipun user sedang Zoom / memakai aplikasi lain.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

from config import (
    CONNECT_RETRIES,
    CONNECT_RETRY_DELAY,
    PORT_READY_TIMEOUT,
    WEBSITE_URL,
    WINDOW_POS,
    WINDOW_SIZE,
    base_port,
    browser_download_dir,
    get_browser_path,
    launch_flags,
    profile_dir,
)

logger = logging.getLogger(__name__)


class BrowserError(RuntimeError):
    pass


# ==============================================================================
# PREFERENSI DOWNLOAD (ditulis SEBELUM browser diluncurkan)
# ==============================================================================
def _ensure_download_prefs(profile_path: Path, download_dir: Path) -> None:
    """
    Set folder download & matikan dialog "Save as" lewat Preferences profil.

    Harus dipanggil saat browser BELUM berjalan: Chromium menimpa file
    Preferences ketika exit, jadi tulisan kita bisa hilang kalau browser
    masih hidup. Kalau browser sudah berjalan, cukup andalkan CDP
    `Browser.setDownloadBehavior` di worker.

    Merge, jangan timpa: file Preferences berisi banyak pengaturan lain
    (cookie, login, dsb.) yang tidak boleh hilang.
    """
    prefs_path = profile_path / "Default" / "Preferences"
    try:
        prefs_path.parent.mkdir(parents=True, exist_ok=True)
        prefs: dict = {}
        if prefs_path.exists():
            try:
                loaded = json.loads(prefs_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    prefs = loaded
            except Exception as e:
                logger.warning(f"Preferences tidak terbaca, dibuat ulang: {e}")

        target = str(Path(download_dir).resolve())

        download = prefs.get("download")
        if not isinstance(download, dict):
            download = {}
        download["default_directory"] = target
        download["prompt_for_download"] = False
        download["directory_upgrade"] = True
        bubble = download.get("bubble")
        if not isinstance(bubble, dict):
            bubble = {}
        bubble["enabled"] = False
        download["bubble"] = bubble

        savefile = prefs.get("savefile")
        if not isinstance(savefile, dict):
            savefile = {}
        savefile["default_directory"] = target

        prefs["download"] = download
        prefs["savefile"] = savefile

        payload = json.dumps(prefs, ensure_ascii=False)
        if prefs_path.exists():
            try:
                if prefs_path.read_text(encoding="utf-8") == payload:
                    return  # sudah sesuai, tidak perlu menulis
            except Exception:
                pass

        # Tulis atomik: file sementara di folder yang sama, lalu ganti.
        tmp_path = prefs_path.with_name(prefs_path.name + ".tmp")
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, prefs_path)
        logger.info(f"⚙️  Preferensi download disiapkan: {target}")
    except Exception as e:
        # Jangan gagalkan launch hanya karena prefs; CDP masih jadi jaring
        # pengaman kedua.
        logger.warning(f"Gagal menulis preferensi download: {e}")


# ==============================================================================
# UTILITAS CDP RINGAN (tanpa Playwright) — untuk cek port
# ==============================================================================
def _cdp_version(port: int, timeout: float = 1.0) -> dict | None:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=timeout
        ) as response:
            return json.load(response)
    except Exception:
        return None


def _port_is_free(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def wait_for_cdp(port: int, timeout: float = PORT_READY_TIMEOUT) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _cdp_version(port):
            return True
        time.sleep(0.4)
    return False


# ==============================================================================
# KONTROL JENDELA VIA PLAYWRIGHT + CDP
# ==============================================================================
def _pick_page(browser):
    """Pilih halaman Flow Music, kalau tidak ada pakai halaman pertama."""
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


def _window_id(browser_cdp, page) -> int | None:
    """Cari windowId dari target halaman."""
    targets = browser_cdp.send("Target.getTargets").get("targetInfos", [])
    wanted = None
    try:
        page_url = page.url
    except Exception:
        page_url = ""
    for target in targets:
        if target.get("type") != "page":
            continue
        if "flowmusic.app" in target.get("url", ""):
            wanted = target["targetId"]
            break
    if wanted is None:
        for target in targets:
            if target.get("type") == "page" and target.get("url", "") == page_url:
                wanted = target["targetId"]
                break
    if wanted is None:
        for target in targets:
            if target.get("type") == "page":
                wanted = target["targetId"]
                break
    if wanted is None:
        return None
    try:
        result = browser_cdp.send("Browser.getWindowForTarget", {"targetId": wanted})
        return result.get("windowId")
    except Exception:
        return None


def _window_action(port: int, action: str, bring_to_front: bool = False) -> bool:
    """
    Jalankan aksi jendela: show | hide.
    Membuat koneksi Playwright sementara supaya tidak bentrok dengan worker
    (worker selalu membuat koneksi sendiri di thread masing-masing).
    """
    from playwright.sync_api import sync_playwright

    pw = None
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.connect_over_cdp(
            f"http://127.0.0.1:{port}", timeout=10_000
        )
        page = _pick_page(browser)
        if page is None:
            return False

        cdp = browser.new_browser_cdp_session()
        window_id = _window_id(cdp, page)
        if window_id is None:
            return False

        if action == "hide":
            cdp.send(
                "Browser.setWindowBounds",
                {"windowId": window_id, "bounds": {"windowState": "minimized"}},
            )
            logger.info(f"[P{port}] 🙈 Browser disembunyikan.")
        elif action == "show":
            # Kembalikan ke posisi & ukuran normal.
            cdp.send(
                "Browser.setWindowBounds",
                {
                    "windowId": window_id,
                    "bounds": {
                        "left": WINDOW_POS[0],
                        "top": WINDOW_POS[1],
                        "width": WINDOW_SIZE[0],
                        "height": WINDOW_SIZE[1],
                        "windowState": "normal",
                    },
                },
            )
            if bring_to_front:
                try:
                    page.bring_to_front()
                except Exception:
                    pass
            logger.info(f"[P{port}] 👀 Browser ditampilkan.")
        else:
            return False
        return True
    except Exception as e:
        logger.warning(f"[P{port}] Gagal {action} jendela: {e}")
        return False
    finally:
        # Jangan browser.close() — itu bisa menutup browser milik user.
        try:
            if pw:
                pw.stop()
        except Exception:
            pass


# ==============================================================================
# DETEKSI LOGIN
# ==============================================================================
def inspect_login(port: int) -> tuple[str, str]:
    """
    Cek apakah profil sudah login ke Flow Music.

    Return:
      ("ready", "...")          -> siap dipakai
      ("login_required", "...") -> masih perlu login manual
      ("unknown", "...")        -> tidak bisa dipastikan
      ("dead", "...")           -> browser tidak bisa dihubungi
    """
    from playwright.sync_api import sync_playwright

    from flow_actions import wait_for_login_state

    pw = None
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.connect_over_cdp(
            f"http://127.0.0.1:{port}", timeout=8_000
        )
        page = _pick_page(browser)
        if page is None:
            return "unknown", "Tidak ada tab terbuka"

        # Beri waktu singkat kalau halaman baru saja dibuka/dimuat.
        logged_in, message = wait_for_login_state(page, timeout=6)
        if logged_in is True:
            return "ready", message
        if logged_in is False:
            return "login_required", message
        return "unknown", message
    except Exception as e:
        return "dead", f"Tidak bisa terhubung: {e}"
    finally:
        try:
            if pw:
                pw.stop()
        except Exception:
            pass


# ==============================================================================
# BROWSER PROFILE
# ==============================================================================
class BrowserProfile:
    """Satu profil browser (satu akun Flow Music)."""

    def __init__(self, index: int, port: int | None = None):
        self.index = index                       # 1-based
        self.port = port or base_port(index)
        self.proc: subprocess.Popen | None = None
        self.owns_process = False
        self.profile_path: Path = profile_dir(index)
        self.download_dir: Path = browser_download_dir(index)
        self.last_error: str = ""

    # --------------------------------------------------------------------------
    # Status
    # --------------------------------------------------------------------------
    @property
    def name(self) -> str:
        return f"Browser {self.index}"

    def cdp_ready(self) -> bool:
        return _cdp_version(self.port, timeout=0.6) is not None

    def is_running(self) -> bool:
        if self.proc is not None:
            return self.proc.poll() is None
        return self.cdp_ready()

    # --------------------------------------------------------------------------
    # Launch
    # --------------------------------------------------------------------------
    def _choose_port(self) -> None:
        """Pastikan port tidak dipakai program lain. Kalau CDP aktif, pakai itu."""
        preferred = base_port(self.index)
        if _port_is_free(preferred):
            self.port = preferred
            return
        if _cdp_version(preferred, timeout=0.6):
            # Sudah ada browser dengan port ini — kemungkinan profil yang sama.
            self.port = preferred
            return
        # Port dipakai program lain: cari port bebas berikutnya.
        offset = 1000 + self.index
        for extra in range(50):
            candidate = preferred + offset + extra
            if _port_is_free(candidate):
                self.port = candidate
                logger.warning(
                    f"Port {preferred} sedang dipakai program lain; "
                    f"{self.name} memakai port {candidate}."
                )
                return
        raise BrowserError(f"Tidak ada port debugging yang bebas untuk {self.name}")

    def launch(self, visible: bool = False, url: str = WEBSITE_URL) -> None:
        """Luncurkan browser bila belum jalan. Bila `visible=False`, sembunyikan."""
        self.last_error = ""

        if self.cdp_ready():
            logger.info(f"[P{self.port}] Browser sudah berjalan, menyambung...")
            if not visible:
                _window_action(self.port, "hide")
            else:
                _window_action(self.port, "show", bring_to_front=True)
            return

        if self.proc is not None and self.proc.poll() is None:
            if wait_for_cdp(self.port):
                if not visible:
                    _window_action(self.port, "hide")
                else:
                    _window_action(self.port, "show", bring_to_front=True)
                return
            self._terminate()

        self._choose_port()

        # Tulis preferensi download SEBELUM proses browser hidup.
        self.profile_path.mkdir(parents=True, exist_ok=True)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        _ensure_download_prefs(self.profile_path, self.download_dir)

        binary = get_browser_path()
        flags = launch_flags(self.index, self.port)
        flags.append(url)

        logger.info(
            f"[P{self.port}] 🚀 Menjalankan {Path(binary).name} "
            f"untuk {self.name} ({self.profile_path.name})"
        )
        creationflags = 0
        start_new_session = False
        if platform.system() == "Windows":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            start_new_session = True

        try:
            self.proc = subprocess.Popen(
                [binary, *flags],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
                start_new_session=start_new_session,
            )
            self.owns_process = True
        except Exception as e:
            self.last_error = str(e)
            raise BrowserError(f"Gagal menjalankan browser: {e}") from e

        if not wait_for_cdp(self.port):
            self.last_error = (
                "CDP tidak aktif. Kemungkinan profil sedang dipakai browser lain."
            )
            self._terminate()
            raise BrowserError(self.last_error)

        # Beri waktu window target terbentuk.
        time.sleep(1.0)
        if not visible:
            for _ in range(5):
                if _window_action(self.port, "hide"):
                    break
                time.sleep(0.6)
        else:
            _window_action(self.port, "show", bring_to_front=True)

    # --------------------------------------------------------------------------
    # Show / hide / close
    # --------------------------------------------------------------------------
    def show(self) -> bool:
        if not self.cdp_ready():
            try:
                self.launch(visible=True)
                return True
            except BrowserError as e:
                logger.error(f"{self.name}: {e}")
                return False
        return _window_action(self.port, "show", bring_to_front=True)

    def hide(self) -> bool:
        if not self.cdp_ready():
            return False
        return _window_action(self.port, "hide")

    def toggle(self) -> bool:
        """Klik kartu: kalau belum jalan -> buka; kalau sudah -> tampilkan."""
        if not self.is_running():
            try:
                self.launch(visible=True)
                return True
            except BrowserError as e:
                logger.error(f"{self.name}: {e}")
                return False
        return self.show()

    def check_login(self) -> tuple[str, str]:
        if not self.cdp_ready():
            return "dead", "Browser belum dibuka"
        return inspect_login(self.port)

    def _terminate(self) -> None:
        proc = self.proc
        self.proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
        except Exception as e:
            logger.debug(f"Error menutup proses browser: {e}")

    def close(self) -> None:
        """Tutup browser HANYA bila diluncurkan oleh aplikasi ini."""
        if not self.owns_process:
            return
        # Coba tutup dengan rapi lewat CDP supaya cookie tersimpan.
        if self.cdp_ready():
            try:
                from playwright.sync_api import sync_playwright

                pw = sync_playwright().start()
                try:
                    browser = pw.chromium.connect_over_cdp(
                        f"http://127.0.0.1:{self.port}", timeout=5_000
                    )
                    cdp = browser.new_browser_cdp_session()
                    cdp.send("Browser.close")
                    time.sleep(1.0)
                finally:
                    try:
                        pw.stop()
                    except Exception:
                        pass
            except Exception:
                pass
        self._terminate()
        self.owns_process = False
        logger.info(f"[P{self.port}] Browser ditutup.")


# ==============================================================================
# PENGELOLA SEMUA BROWSER
# ==============================================================================
class BrowserManager:
    def __init__(self, count: int):
        self.count = max(1, min(count, 10))
        self.profiles: list[BrowserProfile] = [
            BrowserProfile(i + 1) for i in range(self.count)
        ]

    def set_count(self, count: int) -> None:
        """Ubah jumlah browser. Profil yang dibuang akan ditutup bila milik aplikasi."""
        count = max(1, min(count, 10))
        if count == self.count:
            return
        if count < self.count:
            for profile in self.profiles[count:]:
                profile.close()
        self.profiles = self.profiles[:count] + [
            BrowserProfile(i) for i in range(len(self.profiles) + 1, count + 1)
        ]
        self.count = count

    def get(self, index: int) -> BrowserProfile | None:
        for profile in self.profiles:
            if profile.index == index:
                return profile
        return None

    def close_all(self) -> None:
        for profile in self.profiles:
            profile.close()
