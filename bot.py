import os
import time
import re
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ==============================================================================
# KONFIGURASI BOT
# ==============================================================================
PROMPT_FILE = "prompts.txt"
DOWNLOAD_DIR = os.path.abspath("./downloads")
MAX_LOOPS = 10
WEBSITE_URL = "https://www.flowmusic.app/"
DEBUGGING_PORT = 9228

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def show_alert(page, message):
    """Menampilkan alert/popup di browser"""
    try:
        page.evaluate(f'alert("{message}");')
    except:
        print(f"⚠️ ALERT: {message}")

def get_next_prompt() -> str | None:
    """Mengambil baris pertama dari file prompts.txt"""
    if not os.path.exists(PROMPT_FILE):
        with open(PROMPT_FILE, "w", encoding="utf-8") as f:
            pass
        return None
        
    with open(PROMPT_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    if not lines:
        return None
        
    prompt = lines[0].strip()
    
    with open(PROMPT_FILE, "w", encoding="utf-8") as f:
        f.writelines(lines[1:])
        
    return prompt

def run_automation():
    print("🚀 Memulai Bot - Membuka Browser dengan Mode Debugging...")
    
    with sync_playwright() as p:
        try:
            print(f"🌐 Menyambung ke Brave di port {DEBUGGING_PORT}...")
            browser = p.chromium.connect_over_cdp(f"http://localhost:{DEBUGGING_PORT}")
            context = browser.contexts[0]

            page = None
            for pg in context.pages:
                if "flowmusic.app" in pg.url:
                    page = pg
                    break
            if page is None:
                page = context.pages[0] if context.pages else context.new_page()
                print(f"🔗 Membuka {WEBSITE_URL}...")
                page.goto(WEBSITE_URL, wait_until="domcontentloaded")
                time.sleep(3)
            
            print("⏳ Mengecek status login...")
            time.sleep(2)
            
            show_alert(page, "👋 Jika belum login, silakan login manual sekarang. Setelah login, klik OK untuk lanjut.")
            input("⏸️ Tekan Enter di terminal setelah kamu login di browser...")
            
            print("✅ Browser siap! Memulai automasi...\n")
            downloads = []
            page.on("download", lambda d: downloads.append(d))
            for i in range(MAX_LOOPS):
                prompt = get_next_prompt()
                if not prompt:
                    show_alert(page, "✅ Selesai! Tidak ada prompt tersisa.")
                    print("✅ Tidak ada prompt tersisa. Proses selesai!")
                    break
                    
                print(f"\n{'='*70}")
                print(f"📊 [LOOP {i+1}/{MAX_LOOPS}]")
                print(f"{'='*70}")
                print(f"📝 Prompt: {prompt[:80]}{'...' if len(prompt) > 80 else ''}")
                print(f"{'='*70}\n")
                
                try:
                    page.bring_to_front()
                    
                    # 1. INPUT PROMPT
                    print("🔹 Langkah 1: Mengisi prompt...")
                    input_selectors = [
                        "textarea[placeholder*='prompt' i]",
                        "textarea[placeholder*='Describe' i]",
                        "textarea[placeholder*='Ask' i]",
                        "textarea"
                    ]
                    
                    input_found = False
                    for selector in input_selectors:
                        try:
                            page.wait_for_selector(selector, timeout=5000)
                            page.fill(selector, prompt)
                            print("   ✅ Prompt berhasil diisi")
                            input_found = True
                            break
                        except:
                            continue
                    
                    if not input_found:
                        show_alert(page, f"⚠️ Loop {i+1}: Kolom input tidak ditemukan.")
                        input("⏸️ Tekan Enter setelah kamu isi manual di browser...")
                        continue
                    
                    # 2. TEKAN ENTER UNTUK GENERATE
                    print("\n🔹 Langkah 2: Menekan Enter untuk generate...")
                    page.keyboard.press("Enter")
                    print("   ⏳ Sedang generate lagu (maksimal 2 menit)...")
                    
                    # 3. TUNGGU HASIL GENERATE
                    try:
                        page.wait_for_selector("button:has-text('Download'), .download-btn, [aria-label*='download' i], div:has-text('WAV'), div:has-text('MP3')", timeout=120000)
                        print("   🎵 Lagu berhasil digenerate dan muncul di layar!")
                    except PlaywrightTimeoutError:
                        show_alert(page, f"⚠️ Loop {i+1}: Generate terlalu lama.")
                        input("⏸️ Tekan Enter setelah generate selesai di browser...")
                        continue

                    # ==========================================================
                    # 4. PAUSE SEBELUM DOWNLOAD (Konfirmasi Manual)
                    # ==========================================================
                    print("\n" + "="*70)
                    print("⏸️  STATUS: Lagu sudah selesai digenerate")
                    print("👀  Silakan cek hasil generate di browser")
                    print("👉  TEKAN ENTER DI TERMINAL untuk memulai proses download...")
                    print("="*70)
                    input()
                    print("\n✅ Memulai proses download...\n")

                    # Handle popup rekomendasi AI jika ada
                    try:
                        ai_buttons = page.locator("button:has-text('Continue'), button:has-text('Gunakan'), button:has-text('Accept'), button:has-text('Yes'), button:has-text('Lanjutkan')")
                        if ai_buttons.count() > 0:
                            print("💡 Rekomendasi AI terdeteksi. Menutup/Menerima...")
                            ai_buttons.first.click()
                            time.sleep(1.5)
                    except:
                        pass

                    # ==========================================================
                    # 5. PROSES DOWNLOAD 1 LAGU (Semi-Manual & Aman)
                    # ==========================================================
                    print("🔹 Langkah 3: Proses Download (Fokus 1 Lagu)")
                    print("-"*70)

                    # A. KLIK TITIK 3 (tombol More di baris lagu pertama yang terlihat)
                    print("👉 Bot mencoba klik tombol titik 3 (More Options)...")
                    try:
                        win = page.evaluate("({w: window.innerWidth, h: window.innerHeight})")
                        cands = page.locator(
                            "button[aria-label*='more' i], button[title*='more' i], button[aria-haspopup='menu']"
                        )
                        target = None
                        best_y = None
                        for idx in range(cands.count()):
                            btn = cands.nth(idx)
                            box = btn.bounding_box()
                            if not box:
                                continue
                            # Abaikan sidebar kiri & elemen di luar layar
                            if box["x"] < win["w"] * 0.4:
                                continue
                            if box["y"] < 0 or box["y"] > win["h"]:
                                continue
                            # Ambil yang paling atas = baris lagu (Take 1)
                            if best_y is None or box["y"] < best_y:
                                best_y = box["y"]
                                target = btn

                        if target is None:
                            raise Exception("Tombol titik 3 tidak ditemukan")

                        target.scroll_into_view_if_needed(timeout=5000)
                        # Hover dulu karena tombol baris lagu kadang baru muncul saat di-hover
                        target.hover(timeout=5000)
                        time.sleep(0.5)
                        target.click(timeout=5000)
                        print("   ✅ Berhasil klik titik 3")
                    except Exception as e:
                        print(f"   ⚠️ Bot gagal: {str(e)[:80]}")
                        print("   🖱️  SILAKAN KLIK TITIK 3 SECARA MANUAL DI BROWSER SEKARANG.")
                        input("   ⏸️ Tekan ENTER di terminal setelah kamu klik titik 3...")

                    time.sleep(1)  # Jeda agar dropdown muncul sempurna

                    # B. BUKA SUBMENU DOWNLOAD (hover lewat jalur aman)
                    print("👉 Bot mencoba membuka menu 'Download'...")
                    wav_item = page.locator("[role='menuitem']:visible").filter(
                        has_text=re.compile(r"^\s*WAV\s*$")
                    ).first
                    dl_item = page.locator("[role='menuitem']:visible").filter(
                        has_text=re.compile(r"^\s*Download\s*$")
                    ).first
                    try:
                        dl_item.wait_for(state="visible", timeout=5000)
                        dbox = dl_item.bounding_box()
                        dcx = dbox["x"] + dbox["width"] / 2
                        dcy = dbox["y"] + dbox["height"] / 2

                        # Geser dulu di LUAR menu (sejajar baris Download) supaya
                        # tidak melewati Remix/Details, lalu masuk ke Download
                        page.mouse.move(dbox["x"] - 8, dcy, steps=8)
                        time.sleep(0.2)
                        page.mouse.move(dcx, dcy, steps=10)
                        time.sleep(0.9)

                        # Fallback: fokus langsung ke Download + panah kanan
                        if not wav_item.is_visible():
                            print("   ↪️ Hover belum cukup, pakai keyboard (panah kanan)...")
                            dl_item.focus()
                            page.keyboard.press("ArrowRight")
                            time.sleep(0.9)

                        wav_item.wait_for(state="visible", timeout=4000)
                        print("   ✅ Submenu Download terbuka")
                    except Exception as e:
                        print(f"   ⚠️ Bot gagal: {str(e)[:80]}")
                        print("   🖱️  SILAKAN ARAHKAN KURSOR KE 'Download' SECARA MANUAL.")
                        input("   ⏸️ Tekan ENTER di terminal setelah submenu WAV muncul...")

                    # C. KLIK WAV (jalur mouse aman + verifikasi)
                    print("👉 Bot mencoba klik format 'WAV'...")
                    try:
                        wav_item.wait_for(state="visible", timeout=4000)
                        wbox = wav_item.bounding_box()
                        wcx = wbox["x"] + wbox["width"] / 2
                        wcy = wbox["y"] + wbox["height"] / 2

                        # Masuk submenu mendatar di baris Download, lalu turun ke WAV
                        try:
                            dbox = dl_item.bounding_box()
                            row_y = dbox["y"] + dbox["height"] / 2
                            page.mouse.move(wcx, row_y, steps=10)
                            time.sleep(0.2)
                        except Exception:
                            pass
                        page.mouse.move(wcx, wcy, steps=8)
                        time.sleep(0.4)
                        page.mouse.click(wcx, wcy)
                        time.sleep(1)

                        # Verifikasi: kalau submenu WAV masih terbuka, klik gagal
                        if wav_item.is_visible():
                            print("   ↪️ Klik belum kena, pakai fokus langsung ke WAV + Enter...")
                            wav_item.focus()
                            page.keyboard.press("Enter")
                            time.sleep(1)

                        if wav_item.is_visible():
                            raise Exception("Menu WAV masih terbuka, klik belum berhasil")
                        print("   ✅ Berhasil klik WAV")
                    except Exception as e:
                        print(f"   ⚠️ Bot gagal: {str(e)[:80]}")
                        print("   🖱️  SILAKAN KLIK 'WAV' SECARA MANUAL DI SUB-MENU.")
                        input("   ⏸️ Tekan ENTER di terminal setelah kamu klik WAV...")

                    # ==========================================================
                    # 6. SIMPAN FILE HASIL DOWNLOAD (otomatis lewat Playwright)
                    # ==========================================================
                    print("\n" + "="*70)
                    print("💾 Menunggu download dari Brave...")
                    waited = 0
                    while not downloads and waited < 60000:
                        page.wait_for_timeout(500)  # harus ini, bukan time.sleep
                        waited += 500

                    if downloads:
                        dl = downloads.pop(0)
                        name = dl.suggested_filename or f"lagu_{i+1}.wav"
                        if not os.path.splitext(name)[1]:
                            name += ".wav"
                        base, ext = os.path.splitext(name)
                        target = os.path.join(DOWNLOAD_DIR, name)
                        n = 1
                        while os.path.exists(target):  # jangan timpa file lama
                            target = os.path.join(DOWNLOAD_DIR, f"{base}_{n}{ext}")
                            n += 1
                        dl.save_as(target)
                        print(f"✅ File tersimpan: {target}")
                    else:
                        print("⚠️ Tidak ada download terdeteksi dalam 60 detik.")
                        input("👉 Tekan ENTER untuk lanjut...")
                    print("="*70)
                    print("-"*70)
                    
                    # Bersihkan state (tutup menu yang mungkin masih terbuka)
                    page.keyboard.press("Escape")
                    time.sleep(1)
                    
                    print(f"✅ Loop {i+1} selesai dengan aman!\n")
                    time.sleep(2)
                    
                except Exception as e:
                    show_alert(page, f"❌ Error di loop {i+1}: {str(e)[:100]}")
                    print(f"\n❌ Error: {e}\n")
                    continue

            print("\n" + "="*70)
            show_alert(page, "🎉 Semua tugas selesai!")
            print("🏁 BOT SELESAI - Semua prompt telah diproses!")
            print("="*70)
            print("ℹ️ Brave dibiarkan tetap terbuka.")            
        except Exception as e:
            print(f"\n❌ Error fatal: {e}")
            input("\nTekan Enter untuk keluar...")

if __name__ == "__main__":
    run_automation()