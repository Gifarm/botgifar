import os

# ==============================================================================
# KONFIGURASI BOT
# ==============================================================================
PROMPT_FILE = "prompts.txt"
DOWNLOAD_DIR = os.path.abspath("./downloads")
MAX_LOOPS = 100
WEBSITE_URL = "https://www.flowmusic.app/"

# ==============================================================================
# MULTI-PROFILE: Daftar port debugging tiap profile Brave
# ==============================================================================
BROWSER_PORTS = [9222, 9223, 9224, 9225, 9226]

# ==============================================================================
# TIMING
# ==============================================================================
# Jeda setelah paste prompt & Enter, sebelum pindah ke browser berikutnya
PAUSE_AFTER_PASTE_SECONDS = 5

# Berapa lama tunggu generate maksimal per browser (saat fase download)
GENERATE_MAX_WAIT_SECONDS = 300

# Timeout (ms) — untuk selector Playwright
TIMEOUT_INPUT = 5000
TIMEOUT_DOWNLOAD = 60000

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(os.path.abspath("./profiles"), exist_ok=True)