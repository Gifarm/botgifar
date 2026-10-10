"""
Flow Music Bot - GUI Application
"""
import os
import sys
import time
import threading
import logging
import subprocess
from tkinter import filedialog, messagebox

import customtkinter as ctk

# ==============================================================================
# LOGGING SETUP (ke file + ke GUI)
# ==============================================================================
logger = logging.getLogger("bot")
logger.setLevel(logging.INFO)

file_handler = logging.FileHandler("bot.log", encoding="utf-8")
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)


# ==============================================================================
# GUI APP
# ==============================================================================
class BotApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Flow Music Bot v1.0")
        self.geometry("900x700")
        self.resizable(True, True)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # State
        self.is_running = False
        self.stop_event = threading.Event()
        self.bot_thread = None
        self.selected_prompt_file = ctk.StringVar(value="prompts.txt")
        self.num_browsers = ctk.IntVar(value=5)

        # Bangun UI
        self._build_ui()

    # ==========================================================================
    # UI BUILDER
    # ==========================================================================
    def _build_ui(self):
        # ===== HEADER =====
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(20, 10))

        ctk.CTkLabel(
            header, text="🎵 Flow Music Bot",
            font=ctk.CTkFont(size=28, weight="bold")
        ).pack(side="left")

        ctk.CTkLabel(
            header, text="v1.0",
            font=ctk.CTkFont(size=14), text_color="gray"
        ).pack(side="left", padx=(10, 0), pady=(8, 0))

        # ===== STEP 1: JUMLAH BROWSER =====
        step1 = ctk.CTkFrame(self)
        step1.pack(fill="x", padx=20, pady=10)

        ctk.CTkLabel(
            step1, text="1. Jumlah Browser",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(anchor="w", padx=15, pady=(10, 5))

        row1 = ctk.CTkFrame(step1, fg_color="transparent")
        row1.pack(fill="x", padx=15, pady=(0, 15))

        ctk.CTkLabel(row1, text="Berapa browser yang mau dipakai?").pack(side="left")

        self.spin_browsers = ctk.CTkOptionMenu(
            row1, values=["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
            width=100, command=self._on_browser_count_change
        )
        self.spin_browsers.set("5")
        self.spin_browsers.pack(side="left", padx=10)

        self.btn_launch = ctk.CTkButton(
            row1, text="🌐 Buka Browser",
            command=self._launch_browsers, width=180
        )
        self.btn_launch.pack(side="left", padx=10)

        # ===== STEP 2: LOGIN =====
        step2 = ctk.CTkFrame(self)
        step2.pack(fill="x", padx=20, pady=10)

        ctk.CTkLabel(
            step2, text="2. Login Akun",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(anchor="w", padx=15, pady=(10, 5))

        ctk.CTkLabel(
            step2,
            text="Login manual di setiap browser yang terbuka ke flowmusic.app",
            text_color="gray"
        ).pack(anchor="w", padx=15, pady=(0, 15))

        # ===== STEP 3: FILE PROMPT =====
        step3 = ctk.CTkFrame(self)
        step3.pack(fill="x", padx=20, pady=10)

        ctk.CTkLabel(
            step3, text="3. File Prompt",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(anchor="w", padx=15, pady=(10, 5))

        row3 = ctk.CTkFrame(step3, fg_color="transparent")
        row3.pack(fill="x", padx=15, pady=(0, 15))

        self.lbl_file = ctk.CTkLabel(
            row3, text="Belum ada file dipilih",
            fg_color=("#E0E0E0", "#3A3A3A"), corner_radius=8,
            height=35, anchor="w"
        )
        self.lbl_file.pack(side="left", fill="x", expand=True, padx=(0, 10))

        ctk.CTkButton(
            row3, text="📁 Pilih File .txt",
            command=self._pick_file, width=150
        ).pack(side="left")

        # ===== STEP 4: RUN =====
        step4 = ctk.CTkFrame(self)
        step4.pack(fill="x", padx=20, pady=10)

        ctk.CTkLabel(
            step4, text="4. Jalankan",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(anchor="w", padx=15, pady=(10, 5))

        row4 = ctk.CTkFrame(step4, fg_color="transparent")
        row4.pack(fill="x", padx=15, pady=(0, 15))

        self.btn_run = ctk.CTkButton(
            row4, text="▶️ JALANKAN BOT",
            command=self._start_bot,
            fg_color="#16A34A", hover_color="#15803D",
            font=ctk.CTkFont(size=16, weight="bold"),
            height=50
        )
        self.btn_run.pack(side="left", fill="x", expand=True, padx=(0, 10))

        self.btn_stop = ctk.CTkButton(
            row4, text="⏹️ STOP",
            command=self._stop_bot,
            fg_color="#DC2626", hover_color="#B91C1C",
            font=ctk.CTkFont(size=16, weight="bold"),
            height=50, width=120,
            state="disabled"
        )
        self.btn_stop.pack(side="left")

        # ===== LOG VIEWER =====
        log_frame = ctk.CTkFrame(self)
        log_frame.pack(fill="both", expand=True, padx=20, pady=(10, 20))

        ctk.CTkLabel(
            log_frame, text="📋 Log Aktivitas",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(anchor="w", padx=15, pady=(10, 5))

        self.log_box = ctk.CTkTextbox(
            log_frame, font=ctk.CTkFont(family="Courier", size=12),
            wrap="word"
        )
        self.log_box.pack(fill="both", expand=True, padx=15, pady=(0, 15))
        self.log_box.configure(state="disabled")

        # Pasang log handler ke GUI
        self._setup_gui_log_handler()

    # ==========================================================================
    # LOG HANDLER
    # ==========================================================================
    def _setup_gui_log_handler(self):
        class GUIHandler(logging.Handler):
            def __init__(self, textbox, app):
                super().__init__()
                self.textbox = textbox
                self.app = app

            def emit(self, record):
                msg = self.format(record)
                self.app.after(0, self._append, msg)

            def _append(self, msg):
                self.textbox.configure(state="normal")
                self.textbox.insert("end", msg + "\n")
                self.textbox.see("end")
                self.textbox.configure(state="disabled")

        handler = GUIHandler(self.log_box, self)
        handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', '%H:%M:%S'))
        logger.addHandler(handler)

    # ==========================================================================
    # EVENT HANDLERS
    # ==========================================================================
    def _on_browser_count_change(self, val):
        try:
            self.num_browsers.set(int(val))
        except:
            pass

    def _launch_browsers(self):
        """Buka browser sesuai jumlah."""
        n = self.num_browsers.get()
        logger.info(f"🌐 Membuka {n} browser...")

        script = "launch_profiles.bat" if os.name == "nt" else "launch_profiles.sh"

        if not os.path.exists(script):
            messagebox.showerror("Error", f"File {script} tidak ditemukan.")
            return

        try:
            if os.name == "nt":
                subprocess.Popen([script, str(n)], shell=True)
            else:
                os.chmod(script, 0o755)
                subprocess.Popen(["./" + script, str(n)], shell=False)
            logger.info(f"✅ {script} dijalankan dengan {n} browser.")
        except Exception as e:
            logger.error(f"❌ Gagal jalankan {script}: {e}")
            messagebox.showerror("Error", f"Gagal jalankan: {e}")
            
    def _pick_file(self):
        """Pilih file prompt .txt."""
        path = filedialog.askopenfilename(
            title="Pilih file prompt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )
        if path:
            self.selected_prompt_file.set(path)
            self.lbl_file.configure(text=os.path.basename(path))
            logger.info(f"📁 File prompt dipilih: {path}")

    def _start_bot(self):
        """Jalankan bot di thread terpisah."""
        if self.is_running:
            return

        prompt_file = self.selected_prompt_file.get()
        if not prompt_file or not os.path.exists(prompt_file):
            messagebox.showerror("Error", "File prompt belum dipilih atau tidak ada!")
            return

        # Konfirmasi
        ok = messagebox.askyesno(
            "Konfirmasi",
            f"Jalankan bot dengan:\n\n"
            f"- File prompt: {os.path.basename(prompt_file)}\n"
            f"- Jumlah browser: {self.num_browsers.get()}\n\n"
            f"Pastikan semua browser sudah login ke flowmusic.app.\n\nLanjutkan?"
        )
        if not ok:
            return

        self.is_running = True
        self.stop_event.clear()
        self.btn_run.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.btn_launch.configure(state="disabled")

        logger.info("=" * 60)
        logger.info("🚀 BOT DIMULAI")
        logger.info("=" * 60)

        # Jalankan bot di thread
        self.bot_thread = threading.Thread(
            target=self._bot_worker,
            args=(prompt_file, self.num_browsers.get()),
            daemon=True
        )
        self.bot_thread.start()

    def _stop_bot(self):
        """Hentikan bot."""
        if not self.is_running:
            return
        logger.warning("⏹️ User menekan STOP. Menghentikan...")
        self.stop_event.set()

    # ==========================================================================
    # BOT WORKER
    # ==========================================================================
    def _bot_worker(self, prompt_file: str, num_browsers: int):
        """Worker bot yang jalan di thread terpisah."""
        try:
            # Import di dalam worker supaya GUI bisa load cepat
            from playwright.sync_api import sync_playwright
            import importlib
            import config
            importlib.reload(config)

            from prompts import get_next_prompt, count_prompts, return_prompt
            from flow_actions import (
                input_prompt, generate_and_wait_all_songs,
                download_song_by_uid, close_menus, show_alert,
            )

            # Override config
            BROWSER_PORTS = [9222 + i for i in range(num_browsers)]
            DOWNLOAD_DIR = config.DOWNLOAD_DIR
            WEBSITE_URL = config.WEBSITE_URL

            total = count_prompts(prompt_file)
            if total == 0:
                logger.error(f"❌ File prompt kosong: {prompt_file}")
                self._finish_bot()
                return
            logger.info(f"📚 Total prompt: {total}")
            logger.info(f"🎯 Browser: {num_browsers} port {BROWSER_PORTS}")

            # Jalankan paralel pakai threading
            stop_event = self.stop_event
            threads = []

            def worker(port, worker_id):
                pw = None
                try:
                    logger.info(f"[{worker_id}] 🟢 Connect port {port}...")
                    pw = sync_playwright().start()
                    browser = pw.chromium.connect_over_cdp(f"http://localhost:{port}")
                    context = browser.contexts[0]
                    page = None
                    for pg in context.pages:
                        if "flowmusic.app" in pg.url:
                            page = pg
                            break
                    if page is None:
                        page = context.pages[0] if context.pages else context.new_page()
                        page.goto(WEBSITE_URL, wait_until="domcontentloaded")
                        time.sleep(3)
                    logger.info(f"[{worker_id}] ✅ Terhubung.")

                    downloads = []
                    page.on("download", lambda d: downloads.append(d))
                    time.sleep(2)

                    while not stop_event.is_set():
                        prompt = get_next_prompt(prompt_file)
                        if not prompt:
                            logger.info(f"[{worker_id}] 🏁 Prompt habis.")
                            break

                        sisa = count_prompts(prompt_file)
                        logger.info(f"\n{'='*60}")
                        logger.info(f"[{worker_id}] 📝 Prompt: {prompt[:60]}...")
                        logger.info(f"[{worker_id}] 📊 Sisa: {sisa}")
                        logger.info(f"{'='*60}")

                        # Cek stop sebelum proses
                        if stop_event.is_set():
                            logger.warning(f"[{worker_id}] ⏹️ Stop detected.")
                            return_prompt(prompt_file, prompt)
                            break

                        try:
                            page.bring_to_front()
                            if not input_prompt(page, prompt):
                                logger.error(f"[{worker_id}] ❌ Gagal input.")
                                return_prompt(prompt_file, prompt)
                                continue

                            new_songs = generate_and_wait_all_songs(
                                page, max_wait_seconds=GENERATE_MAX_WAIT_SECONDS,
                                stop_event=stop_event,
                            )

                            # Kalau stop di tengah generate
                            if stop_event.is_set():
                                logger.warning(f"[{worker_id}] ⏹️ Stop di tengah generate.")
                                if not new_songs:
                                    return_prompt(prompt_file, prompt)
                                break

                            # Case 2: Tidak ada lagu baru (kemungkinan credits habis)
                            if not new_songs:
                                logger.warning(f"[{worker_id}] ⚠️ Tidak ada lagu baru.")
                                close_menus(page)

                                # Cek error credits
                                from flow_actions import check_credits_or_rate_limit
                                if check_credits_or_rate_limit(page):
                                    logger.error(f"[{worker_id}] 🚫 Credits habis. Prompt dikembalikan.")
                                    return_prompt(prompt_file, prompt)
                                    # Berhenti saja worker ini (biar browser lain ambil prompt)
                                    break
                                else:
                                    logger.warning(f"[{worker_id}] Prompt dikembalikan karena tidak jelas.")
                                    return_prompt(prompt_file, prompt)
                                    time.sleep(2)
                                    continue

                            logger.info(f"[{worker_id}] 🎼 {len(new_songs)} lagu.")
                            success = 0
                            for idx, song in enumerate(new_songs):
                                if stop_event.is_set():
                                    break
                                uid = song["uid"]
                                label = f"(lagu {idx+1}/{len(new_songs)})"
                                if download_song_by_uid(page, uid, DOWNLOAD_DIR,
                                                         downloads, label,
                                                         stop_event=stop_event):
                                    success += 1
                                    time.sleep(2)
                                    close_menus(page)
                                    time.sleep(1)
                                else:
                                    close_menus(page)
                                    time.sleep(1)
                            logger.info(f"[{worker_id}] ✅ {success}/{len(new_songs)} lagu.\n")
                            time.sleep(2)

                        except Exception as e:
                            logger.error(f"[{worker_id}] ❌ Error: {e}")
                            try:
                                close_menus(page)
                            except:
                                pass
                            # Kalau error, kembalikan prompt biar dicoba browser lain
                            try:
                                return_prompt(prompt_file, prompt)
                            except:
                                pass
                            continue

                except Exception as e:
                    logger.error(f"[{worker_id}] ❌ Fatal: {e}")
                finally:
                    try:
                        if pw:
                            pw.stop()
                    except:
                        pass

            # Import konstanta
            from config import GENERATE_MAX_WAIT_SECONDS

            for idx, port in enumerate(BROWSER_PORTS):
                worker_id = f"W{idx+1}-P{port}"
                t = threading.Thread(target=worker, args=(port, worker_id), daemon=True)
                t.start()
                threads.append(t)
                time.sleep(0.3)

            for t in threads:
                t.join()

            logger.info("\n" + "=" * 60)
            logger.info("🏁 BOT SELESAI - Semua prompt diproses!")
            logger.info("=" * 60)

        except Exception as e:
            logger.error(f"❌ Error fatal di bot worker: {e}", exc_info=True)
        finally:
            self._finish_bot()

    def _finish_bot(self):
        """Reset state setelah bot selesai."""
        self.is_running = False
        self.after(0, lambda: self.btn_run.configure(state="normal"))
        self.after(0, lambda: self.btn_stop.configure(state="disabled"))
        self.after(0, lambda: self.btn_launch.configure(state="normal"))
        self.after(0, lambda: messagebox.showinfo("Selesai", "Bot telah selesai dijalankan!"))


# ==============================================================================
# RUN
# ==============================================================================
if __name__ == "__main__":
    app = BotApp()
    app.mainloop()