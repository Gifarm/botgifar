import time
import logging
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)


class BrowserManager:
    """Manager untuk 1 profile browser (single)."""
    def __init__(self, debugging_port: int, website_url: str):
        self.debugging_port = debugging_port
        self.website_url = website_url
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def connect(self):
        logger.info(f"🔌 Menghubungkan ke browser di port {self.debugging_port}...")
        self.playwright = sync_playwright().start()
        try:
            self.browser = self.playwright.chromium.connect_over_cdp(
                f"http://localhost:{self.debugging_port}"
            )
            self.context = self.browser.contexts[0]

            for pg in self.context.pages:
                if "flowmusic.app" in pg.url:
                    self.page = pg
                    break

            if self.page is None:
                self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
                logger.info(f"Membuka {self.website_url}...")
                self.page.goto(self.website_url, wait_until="domcontentloaded")
                time.sleep(3)

            logger.info(f"✅ Port {self.debugging_port} terhubung.")
            return self.page
        except Exception as e:
            logger.error(f"❌ Gagal connect port {self.debugging_port}: {e}")
            raise

    def close(self):
        if self.playwright:
            self.playwright.stop()
            logger.info(f"🔌 Koneksi port {self.debugging_port} ditutup.")


class MultiBrowserManager:
    """
    Manager untuk beberapa profile browser sekaligus.
    Setiap profile terhubung ke port debugging berbeda.
    """
    def __init__(self, ports: list, website_url: str):
        self.ports = ports
        self.website_url = website_url
        self.playwright = None
        self.workers = {}  # port -> {"browser": ..., "context": ..., "page": ...}
        self.failed_ports = []

    def connect_all(self):
        """Connect ke semua port, return dict {port: page}."""
        logger.info(f"🚀 Menghubungkan ke {len(self.ports)} profile browser...")
        self.playwright = sync_playwright().start()

        for port in self.ports:
            try:
                browser = self.playwright.chromium.connect_over_cdp(
                    f"http://localhost:{port}"
                )
                context = browser.contexts[0]

                page = None
                for pg in context.pages:
                    if "flowmusic.app" in pg.url:
                        page = pg
                        break

                if page is None:
                    page = context.pages[0] if context.pages else context.new_page()
                    logger.info(f"   Port {port}: membuka {self.website_url}...")
                    page.goto(self.website_url, wait_until="domcontentloaded")
                    time.sleep(3)

                self.workers[port] = {
                    "browser": browser,
                    "context": context,
                    "page": page,
                }
                logger.info(f"   ✅ Port {port} terhubung.")
            except Exception as e:
                logger.error(f"   ❌ Port {port} GAGAL: {e}")
                self.failed_ports.append(port)

        logger.info(f"📊 Total terhubung: {len(self.workers)}/{len(self.ports)} profile")
        if self.failed_ports:
            logger.warning(f"⚠️ Port gagal: {self.failed_ports}")
        return self.workers

    def get_page(self, port: int):
        """Ambil page object untuk port tertentu."""
        if port in self.workers:
            return self.workers[port]["page"]
        return None

    def get_ports(self):
        """Return list port yang berhasil connect."""
        return list(self.workers.keys())

    def close_all(self):
        if self.playwright:
            self.playwright.stop()
            logger.info("🔌 Semua koneksi Playwright ditutup.")