"""
Konfigurasi global Flow Music Bot.

Semua path, port, timing, dan lokasi browser disatukan di sini supaya
mudah diubah tanpa menyentuh logika bot.
"""
from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path

# ==============================================================================
# PATH
# ==============================================================================
APP_DIR = Path(__file__).resolve().parent
PROFILES_DIR = APP_DIR / "profiles"
DOWNLOADS_DIR = APP_DIR / "downloads"
STATE_DIR = APP_DIR / "state"
LOG_FILE = APP_DIR / "bot.log"
DEFAULT_PROMPT_FILE = APP_DIR / "prompts.txt"

for _d in (PROFILES_DIR, DOWNLOADS_DIR, STATE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ==============================================================================
# TARGET & BROWSER
# ==============================================================================
WEBSITE_URL = "https://www.flowmusic.app/"

DEFAULT_NUM_BROWSERS = 5
MAX_NUM_BROWSERS = 10
BASE_DEBUG_PORT = 9222          # profile 1 -> 9222, profile 2 -> 9223, dst.

# Ukuran & posisi jendela saat browser ditampilkan untuk login.
# Ukuran cukup lega supaya tata letak Flow Music tidak berubah jadi versi sempit.
WINDOW_SIZE = (1400, 900)
WINDOW_POS = (100, 60)

# Browser otomatis diminimalkan (disembunyikan) saat bot mulai bekerja.
HIDE_ON_START = True

# ==============================================================================
# TIMING
# ==============================================================================
CONNECT_RETRIES = 20            # percobaan connect ke satu browser
CONNECT_RETRY_DELAY = 1.0       # detik
PORT_READY_TIMEOUT = 30         # detik menunggu CDP siap setelah launch

GENERATE_MAX_WAIT_SECONDS = 300 # batas total menunggu hasil generate
GENERATE_STABLE_SECONDS = 20    # dianggap selesai bila tidak berubah selama ini
POLL_INTERVAL_SECONDS = 3       # interval polling DOM

DOWNLOAD_TIMEOUT_SECONDS = 120  # batas menunggu file hasil download
DOWNLOAD_STABLE_SECONDS = 2     # ukuran file harus stabil selama ini

MAX_PROMPT_ATTEMPTS = 3         # percobaan per prompt sebelum dianggap gagal
PAUSE_BETWEEN_PROMPTS = 2       # jeda santai antar prompt (detik)
PAUSE_AFTER_DOWNLOAD = 1.5
IDLE_POLL_SECONDS = 2           # browser tanpa prompt cek antrean tiap ini (detik)
DOWNLOAD_STALL_WARN_SECONDS = 8 # peringatan bila belum ada file setelah ini

# ==============================================================================
# FLAG CHROME/BRAVE
# ==============================================================================
def _launch_flags(port: int, profile_dir: Path) -> list[str]:
    flags = [
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-fre",
        "--hide-crash-restore-bubble",
        "--mute-audio",  # jangan sampai audio browser tersembunyi berbunyi
        # ---- tetap hidup saat jendela diminimalkan / tertutup aplikasi lain ----
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        # Jangan biarkan Chrome/Brave "menidurkan" tab yang lama tidak dilihat.
        "--disable-features=CalculateNativeWinOcclusion,IntensiveWakeUpThrottling,"
        "HighEfficiencyModeAvailable,BatterySaverModeAvailable,MemorySaverMode,"
        "AutomaticTabDiscarding,TabDiscarding,BraveMemorySaver",
        # ---- stabilitas ----
        "--disable-dev-shm-usage",
        f"--window-size={WINDOW_SIZE[0]},{WINDOW_SIZE[1]}",
        f"--window-position={WINDOW_POS[0]},{WINDOW_POS[1]}",
    ]
    # Di Linux Wayland, Chrome harus dipaksa ke XWayland supaya jendela
    # bisa dikontrol (minimize/show) lewat CDP.
    if platform.system() == "Linux":
        flags.append("--ozone-platform=x11")
    return flags


# ==============================================================================
# DETEKSI BROWSER
# ==============================================================================
_BROWSER_CANDIDATES: dict[str, list[str]] = {
    "Linux": [
        "brave-browser",
        "brave-browser-stable",
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "microsoft-edge",
        "microsoft-edge-stable",
    ],
    "Darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ],
    "Windows": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ],
}

_browser_path_cache: str | None = None


def get_browser_path() -> str:
    """Cari binary Chrome/Chromium/Brave. Bisa dioverride lewat env FLOW_BOT_BROWSER."""
    global _browser_path_cache
    if _browser_path_cache:
        return _browser_path_cache

    override = os.environ.get("FLOW_BOT_BROWSER", "").strip()
    if override and Path(override).exists():
        _browser_path_cache = override
        return override

    system = platform.system()
    for candidate in _BROWSER_CANDIDATES.get(system, []):
        if os.path.isabs(candidate):
            if Path(candidate).exists():
                _browser_path_cache = candidate
                return candidate
        else:
            found = shutil.which(candidate)
            if found:
                _browser_path_cache = found
                return found

    # Fallback terakhir: Chromium bawaan Playwright (kalau ada).
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            path = pw.chromium.executable_path
            if path and Path(path).exists():
                _browser_path_cache = path
                return path
    except Exception:
        pass

    raise RuntimeError(
        "Browser Chrome/Chromium/Brave tidak ditemukan. "
        "Install salah satunya atau set environment variable FLOW_BOT_BROWSER."
    )


def get_browser_label() -> str:
    path = get_browser_path()
    name = Path(path).name.lower()
    if "brave" in name:
        return "Brave"
    if "edge" in name:
        return "Microsoft Edge"
    if "chromium" in name:
        return "Chromium"
    return "Google Chrome"


def profile_dir(index: int) -> Path:
    """index dimulai dari 1."""
    path = PROFILES_DIR / f"profile_{index}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def base_port(index: int) -> int:
    """Port debugging untuk profile ke-index (1-based)."""
    return BASE_DEBUG_PORT + (index - 1)


def browser_download_dir(index: int) -> Path:
    """Folder download sementara per browser (biar tidak saling rebutan nama)."""
    path = DOWNLOADS_DIR / f".browser_{index}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def launch_flags(index: int, port: int) -> list[str]:
    return _launch_flags(port, profile_dir(index))
