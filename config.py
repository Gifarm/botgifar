import os

# ==============================================================================
# KONFIGURASI BOT
# ==============================================================================
PROMPT_FILE = "prompts.txt" # Path default, nanti bisa diubah dari UI
DOWNLOAD_DIR = os.path.abspath("./downloads")
MAX_LOOPS = 10
WEBSITE_URL = "https://www.flowmusic.app/"
DEBUGGING_PORT = 9228

# Timeout (dalam milidetik)
TIMEOUT_INPUT = 5000
TIMEOUT_GENERATE = 120000  # 2 menit
TIMEOUT_DOWNLOAD = 60000   # 60 detik
TIMEOUT_MENU = 5000

# Pastikan folder download ada
os.makedirs(DOWNLOAD_DIR, exist_ok=True)