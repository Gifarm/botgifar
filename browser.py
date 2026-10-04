import time
import logging
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

class BrowserManager:
    def __init__(self, debugging_port: int, website_url: str):
        self.debugging_port = debugging_port
        self.website_url = website_url
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def connect(self):
        """Menyambung ke browser yang sudah terbuka dengan mode debugging."""
        logger.info(f"Menghubungkan ke browser di port {self.debugging_port}...")
        self.playwright = sync_playwright().start()
        try:
            self.browser = self.playwright.chromium.connect_over_cdp(
                f"http://localhost:{self.debugging_port}"
            )
            self.context = self.browser.contexts[0]
            
            # Cari tab yang sudah membuka website, atau buat baru
            for pg in self.context.pages:
                if "flowmusic.app" in pg.url:
                    self.page = pg
                    break
            
            if self.page is None:
                self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
                logger.info(f"Membuka {self.website_url}...")
                self.page.goto(self.website_url, wait_until="domcontentloaded")
                time.sleep(3)
            
            logger.info("Berhasil terhubung ke browser.")
            return self.page
        except Exception as e:
            logger.error(f"Gagal terhubung ke browser: {e}")
            raise

    def close(self):
        """Menutup koneksi Playwright (browser tetap terbuka)."""
        if self.playwright:
            self.playwright.stop()
            logger.info("Koneksi Playwright ditutup.")