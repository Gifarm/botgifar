"""
Konfigurasi global Flow Music Bot.

Semua path, port, timing, dan lokasi browser disatukan di sini supaya
mudah diubah tanpa menyentuh logika bot.
"""
from __future__ import annotations

import json
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
# Folder kerja lama (versi sebelumnya) — hanya dipakai untuk menyelamatkan
# file yang mungkin tertinggal di sana. Versi sekarang mengunduh langsung ke
# `downloads/` yang sama untuk semua browser.
DOWNLOAD_TEMP_DIR = STATE_DIR / "download_tmp"
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
# Tombol saran AI menandakan generate utama selesai, tetapi baris lagu ke-2/3
# bisa telat muncul. Sebelum menyerah, tunggu daftar lagu baru tetap selama ini
# supaya semua lagu ikut terunduh (bukan cuma lagu pertama).
GENERATE_SUGGESTION_GRACE_SECONDS = 6
POLL_INTERVAL_SECONDS = 3       # interval polling DOM

DOWNLOAD_TIMEOUT_SECONDS = 120  # batas menunggu file hasil download
DOWNLOAD_STABLE_SECONDS = 2     # ukuran file harus stabil selama ini

MAX_PROMPT_ATTEMPTS = 3         # percobaan per prompt sebelum dianggap gagal
PAUSE_BETWEEN_PROMPTS = 2       # jeda santai antar prompt (detik)
PAUSE_AFTER_DOWNLOAD = 1.5
IDLE_POLL_SECONDS = 2           # browser tanpa prompt cek antrean tiap ini (detik)
DOWNLOAD_STALL_WARN_SECONDS = 8 # peringatan bila belum ada file setelah ini

# ---- Deteksi & pemulihan sesi (limit Google Flow) ----
# Kalau prompt masih tertinggal di kolom input dan tidak ada reaksi generate
# selama ini, sesi dianggap macet lalu bot mencoba membuat session baru.
GENERATE_STUCK_SECONDS = 150
# Berapa lama menunggu halaman siap setelah klik tombol session baru.
NEW_SESSION_READY_TIMEOUT = 15
# Jumlah percobaan pemulihan sesi sebelum browser itu berhenti.
SESSION_RECOVERY_ATTEMPTS = 2
# Jeda antar percobaan pemulihan (detik).
SESSION_RECOVERY_COOLDOWN_SECONDS = 45
# Batas berapa kali berturut-turut bot boleh memulihkan sesi tanpa satu pun
# lagu berhasil. Setelah ini browser berhenti agar tidak berputar terus.
SESSION_RECOVERY_MAX_STREAK = 10

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
# DETEKSI BROWSER (Brave / Chrome / Chromium / Edge)
# ==============================================================================
# Satu "family" = satu browser yang bisa dipilih user. Urutan kandidat di
# dalam family dipakai saat mencari binary; urutan antar-family diatur oleh
# _PLATFORM_BROWSER_ORDER (dipertahankan dari versi sebelumnya supaya browser
# bawaan tidak berubah hanya karena fitur pilihan browser ditambahkan).
_BROWSER_FAMILIES: dict[str, dict] = {
    "brave": {
        "label": "Brave",
        "candidates": {
            "Linux": ["brave-browser", "brave-browser-stable"],
            "Darwin": [
                "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
            ],
            "Windows": [
                r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
                r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
            ],
        },
    },
    "chrome": {
        "label": "Google Chrome",
        "candidates": {
            "Linux": ["google-chrome", "google-chrome-stable"],
            "Darwin": [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            ],
            "Windows": [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            ],
        },
    },
    "chromium": {
        "label": "Chromium",
        "candidates": {
            "Linux": ["chromium", "chromium-browser"],
            "Darwin": ["/Applications/Chromium.app/Contents/MacOS/Chromium"],
            "Windows": [],
        },
    },
    "edge": {
        "label": "Microsoft Edge",
        "candidates": {
            "Linux": ["microsoft-edge", "microsoft-edge-stable"],
            "Darwin": [
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
            ],
            "Windows": [
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            ],
        },
    },
}

_PLATFORM_BROWSER_ORDER: dict[str, list[str]] = {
    "Linux": ["brave", "chrome", "chromium", "edge"],
    "Darwin": ["chrome", "brave", "edge", "chromium"],
    "Windows": ["chrome", "brave", "edge", "chromium"],
}

# Penanda browser pemilik folder profiles/profile_N (dipakai saat user
# berganti Brave <-> Chrome supaya profil lama tidak tertukar).
_BROWSER_MARKER = ".flowbot_browser"
SETTINGS_FILE = STATE_DIR / "settings.json"

_browser_options_cache: list[dict] | None = None
_browser_path_cache: str | None = None
_browser_choice: str | None = None  # id family, atau path absolut (kustom)


# ------------------------------------------------------------------------------
# Pengaturan tersimpan (state/settings.json)
# ------------------------------------------------------------------------------
def _read_settings() -> dict:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_settings(patch: dict) -> None:
    data = _read_settings()
    data.update(patch)
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_FILE.with_name(SETTINGS_FILE.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, SETTINGS_FILE)
    except OSError:
        pass


# ------------------------------------------------------------------------------
# Deteksi binary
# ------------------------------------------------------------------------------
def _is_executable(path: str | Path) -> bool:
    try:
        p = Path(path)
        if not p.is_file():
            return False
        if platform.system() == "Windows":
            return True
        return os.access(p, os.X_OK)
    except OSError:
        return False


def _candidate_path(candidate: str) -> str | None:
    if os.path.isabs(candidate):
        return candidate if _is_executable(candidate) else None
    found = shutil.which(candidate)
    return found if found and _is_executable(found) else None


def _detect_family_path(browser_id: str) -> str | None:
    family = _BROWSER_FAMILIES.get(browser_id)
    if not family:
        return None
    for candidate in family["candidates"].get(platform.system(), []):
        path = _candidate_path(candidate)
        if path:
            return path
    return None


def _browser_slug(path: str | Path) -> str | None:
    """Tebak family browser dari nama file binary."""
    name = Path(path).name.lower()
    if "brave" in name:
        return "brave"
    if "edge" in name or "msedge" in name:
        return "edge"
    if "chromium" in name:
        return "chromium"
    if "chrome" in name:
        return "chrome"
    return None


def _playwright_chromium_path() -> str | None:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            path = pw.chromium.executable_path
            if path and _is_executable(path):
                return path
    except Exception:
        pass
    return None


def available_browsers(refresh: bool = False) -> list[dict]:
    """
    Browser yang terpasang di komputer ini.

    Return list of dict: ``{"id", "label", "path"}``. Hanya browser yang
    benar-benar ada yang dikembalikan, jadi UI bisa menampilkan pilihan
    Brave dan/atau Chrome sesuai yang terpasang.
    """
    global _browser_options_cache
    if _browser_options_cache is not None and not refresh:
        return [dict(option) for option in _browser_options_cache]

    options: list[dict] = []
    seen: set[str] = set()

    def add(browser_id: str, label: str, path: str) -> None:
        real = os.path.realpath(path)
        if real in seen or any(opt["id"] == browser_id for opt in options):
            return
        seen.add(real)
        options.append({"id": browser_id, "label": label, "path": path})

    system = platform.system()
    preferred = {
        browser_id: pos
        for pos, browser_id in enumerate(_PLATFORM_BROWSER_ORDER.get(system, []))
    }
    ordered = sorted(_BROWSER_FAMILIES, key=lambda bid: preferred.get(bid, 99))
    for browser_id in ordered:
        path = _detect_family_path(browser_id)
        if path:
            add(browser_id, _BROWSER_FAMILIES[browser_id]["label"], path)

    # Binary khusus lewat env (instalasi tidak standar). Kalau family-nya
    # sudah terdeteksi, entri sistem yang dipakai supaya dropdown tidak dobel.
    override = os.environ.get("FLOW_BOT_BROWSER", "").strip()
    if override and _is_executable(override):
        slug = _browser_slug(override)
        if slug and slug in _BROWSER_FAMILIES:
            add(slug, _BROWSER_FAMILIES[slug]["label"], override)
        else:
            add(str(Path(override)), f"{Path(override).name} (kustom)", override)

    # Pilihan tersimpan berupa path (mis. hasil `main.py --browser /path`)
    # yang tidak terdeteksi otomatis — tetap tampilkan supaya dropdown jujur.
    if (
        _browser_choice
        and os.path.isabs(_browser_choice)
        and _is_executable(_browser_choice)
    ):
        slug = _browser_slug(_browser_choice)
        if slug in _BROWSER_FAMILIES:
            base_label = _BROWSER_FAMILIES[slug]["label"]
        else:
            base_label = Path(_browser_choice).name
        add(
            _browser_choice,
            f"{base_label} (kustom)",
            _browser_choice,
        )

    # Cadangan terakhir: Chromium bawaan Playwright (kalau ada).
    if not options:
        pw_path = _playwright_chromium_path()
        if pw_path:
            add("chromium", "Chromium (Playwright)", pw_path)

    _browser_options_cache = options
    return [dict(option) for option in options]


def _choice_path(choice: str) -> str | None:
    if os.path.isabs(choice):
        return choice if _is_executable(choice) else None
    for option in available_browsers():
        if option["id"] == choice:
            return option["path"]
    return None


def set_browser_choice(choice: str | None) -> None:
    """
    Pilih browser yang dipakai (id family seperti ``"brave"`` / ``"chrome"``,
    path absolut untuk binary kustom, atau ``None``/``"auto"``).

    Pilihan disimpan ke ``state/settings.json`` supaya dipakai lagi saat
    aplikasi dibuka berikutnya.
    """
    global _browser_choice, _browser_path_cache

    value = (choice or "").strip()
    if value.lower() in {"", "auto", "otomatis"}:
        _browser_choice = None
    else:
        # Terima path relatif / "~/..." supaya CLI mudah dipakai.
        has_sep = os.sep in value or bool(os.altsep and os.altsep in value)
        if not os.path.isabs(value) and (has_sep or value.startswith("~")):
            candidate = Path(value).expanduser()
            if candidate.exists():
                value = str(candidate.resolve())
        if not os.path.isabs(value) and value.lower() in _BROWSER_FAMILIES:
            value = value.lower()  # "Chrome" / "CHROME" tetap diterima
        if not _choice_path(value):
            labels = ", ".join(opt["label"] for opt in available_browsers())
            raise ValueError(
                f"Browser '{choice}' tidak ditemukan. "
                f"Pilihan yang terpasang: {labels or 'tidak ada'}."
            )
        _browser_choice = value

    _browser_path_cache = None
    _write_settings({"browser": _browser_choice})


def get_browser_choice() -> str | None:
    return _browser_choice


def get_browser_path() -> str:
    """
    Binary browser yang akan dipakai.

    Urutan: pilihan user (menu Browser) -> ``FLOW_BOT_BROWSER`` -> browser
    pertama yang terdeteksi. Hasil di-cache sampai pilihan diubah.
    """
    global _browser_path_cache
    if _browser_path_cache and _is_executable(_browser_path_cache):
        return _browser_path_cache

    if _browser_choice:
        chosen = _choice_path(_browser_choice)
        if chosen:
            _browser_path_cache = chosen
            return chosen

    override = os.environ.get("FLOW_BOT_BROWSER", "").strip()
    if override and _is_executable(override):
        _browser_path_cache = override
        return override

    options = available_browsers()
    if options:
        _browser_path_cache = options[0]["path"]
        return options[0]["path"]

    raise RuntimeError(
        "Browser Chrome/Chromium/Brave/Edge tidak ditemukan. "
        "Install salah satunya atau set environment variable FLOW_BOT_BROWSER."
    )


def get_browser_label(path: str | None = None) -> str:
    """Nama ramah untuk browser (dipakai di header & kartu)."""
    if path is None:
        path = get_browser_path()
    slug = _browser_slug(path)
    if slug in _BROWSER_FAMILIES:
        return _BROWSER_FAMILIES[slug]["label"]
    return Path(path).name


def get_browser_id(path: str | None = None) -> str | None:
    """Id family browser yang sedang dipakai (untuk nama folder profil)."""
    if path is None and _browser_choice and not os.path.isabs(_browser_choice):
        # Hanya percaya pilihan tersimpan kalau browsernya masih terpasang;
        # kalau sudah di-uninstall, ikuti browser yang benar-benar dipakai.
        if _choice_path(_browser_choice):
            return _browser_choice
    try:
        path = path or get_browser_path()
    except RuntimeError:
        return None
    slug = _browser_slug(path)
    return slug if slug in _BROWSER_FAMILIES else "custom"


# ------------------------------------------------------------------------------
# Folder profil per browser
# ------------------------------------------------------------------------------
def _marker_owner(profile_path: Path) -> str | None:
    try:
        value = (profile_path / _BROWSER_MARKER).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _local_state_owner(profile_path: Path) -> str | None:
    """Baca penanda browser dari file Local State milik profil Chromium."""
    try:
        data = json.loads(
            (profile_path / "Local State").read_text(encoding="utf-8")
        )
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    keys = {str(key).lower() for key in data}
    if "brave" in keys or "brave_shields" in keys:
        return "brave"
    if "edge" in keys:
        return "edge"
    if "chromium" in keys:
        return "chromium"
    return None


def legacy_profile_owner() -> str | None:
    """
    Browser pemilik folder lama ``profiles/profile_N``.

    Profil lama dibuat sebelum fitur pilihan browser ada, jadi pemiliknya
    ditebak dari penanda yang ditulis bot atau dari ``Local State`` Brave.
    ``None`` berarti belum ada profil lama / pemiliknya tidak diketahui;
    ``profile_dir()`` yang memutuskan folder mana yang dipakai.
    """
    for index in range(1, MAX_NUM_BROWSERS + 1):
        profile_path = PROFILES_DIR / f"profile_{index}"
        if not profile_path.exists():
            continue
        owner = _marker_owner(profile_path)
        if owner:
            return owner

    for index in range(1, MAX_NUM_BROWSERS + 1):
        profile_path = PROFILES_DIR / f"profile_{index}"
        if not profile_path.exists():
            continue
        owner = _local_state_owner(profile_path)
        if owner:
            return owner

    return None


def mark_profile_browser(profile_path: Path, browser_id: str | None) -> None:
    """Tandai folder profil dengan browser yang memakainya (saat launch)."""
    if not browser_id:
        return
    try:
        Path(profile_path).mkdir(parents=True, exist_ok=True)
        (Path(profile_path) / _BROWSER_MARKER).write_text(
            browser_id + "\n", encoding="utf-8"
        )
    except OSError:
        pass


def profile_dir(index: int) -> Path:
    """
    Folder profil untuk slot ke-``index`` (mulai dari 1).

    Profil lama tetap di ``profiles/profile_N``. Browser lain (mis. Chrome
    setelah sebelumnya Brave) memakai folder sendiri
    ``profiles/<browser_id>/profile_N`` supaya cookie/login tidak tertukar.
    """
    browser_id = get_browser_id()
    owner = legacy_profile_owner()

    if not browser_id:
        root = PROFILES_DIR
    elif browser_id == owner:
        # Browser ini pemilik profil lama -> tetap di tempat lama (login aman).
        root = PROFILES_DIR
    elif owner is None:
        # Belum ada profil lama. Kalau browser ini sudah punya folder sendiri
        # (mis. folder lama dihapus user), jangan tinggalkan login lama itu.
        own_root = PROFILES_DIR / browser_id
        has_own = (own_root / f"profile_{index}").exists()
        root = own_root if has_own else PROFILES_DIR
    else:
        # Browser lain: folder terpisah supaya cookie/login tidak tertukar.
        root = PROFILES_DIR / browser_id

    path = root / f"profile_{index}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def base_port(index: int) -> int:
    """Port debugging untuk profile ke-index (1-based)."""
    return BASE_DEBUG_PORT + (index - 1)


def browser_download_dir(index: int) -> Path:
    """
    Folder download untuk SEMUA browser — satu folder yang sama.

    Semua browser (1–10) mengunduh langsung ke `downloads/`, jadi hasilnya
    otomatis terkumpul di satu tempat tanpa subfolder per browser. Supaya
    beberapa browser tidak saling menimpa file saat mengunduh bersamaan,
    proses download diantrekan oleh bot (lihat `download_song_row`).

    Parameter `index` tetap diterima agar pemanggil lama tidak perlu diubah.
    """
    del index  # semua browser memakai folder yang sama
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    return DOWNLOADS_DIR


def launch_flags(index: int, port: int) -> list[str]:
    return _launch_flags(port, profile_dir(index))


# ------------------------------------------------------------------------------
# Terapkan pilihan browser yang tersimpan (kalau browsernya masih terpasang).
# Tidak memakai available_browsers() supaya import config tidak memulai
# Playwright hanya untuk membaca file pengaturan.
# ------------------------------------------------------------------------------
def _apply_saved_browser_choice() -> None:
    global _browser_choice
    saved = _read_settings().get("browser")
    if not isinstance(saved, str):
        return
    value = saved.strip()
    if not value:
        return
    if os.path.isabs(value):
        if _is_executable(value):
            _browser_choice = value
        return
    if _detect_family_path(value):
        _browser_choice = value


_apply_saved_browser_choice()
