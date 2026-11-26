"""
Flow Music Bot — aplikasi desktop.

Alur pemakaian:
1. Pilih jumlah browser.
2. Klik kartu browser untuk membukanya, login manual, lalu klik "Sembunyikan".
3. Pilih file .txt berisi prompt (satu prompt = satu paragraf).
4. Klik JALANKAN. Browser yang belum terbuka akan dibuka tersembunyi,
   lalu bot bekerja di belakang layar.
"""
from __future__ import annotations

import logging
import os
import threading
import tkinter as tk
from collections import deque
from tkinter import filedialog, messagebox

import customtkinter as ctk

import config
from browser_manager import BrowserManager
from prompts import PromptQueue, count_prompts
from worker import BotRunner

APP_TITLE = "Flow Music Bot"
APP_VERSION = "3.0.2"

# Batas baris log yang disimpan di kotak log GUI.
LOG_MAX_LINES = 2000

# Warna status kartu (mode terang, mode gelap)
STATE_COLORS = {
    "closed": ("#6B7280", "#9CA3AF"),
    "launching": ("#B45309", "#FBBF24"),
    "connecting": ("#B45309", "#FBBF24"),
    "visible": ("#1D4ED8", "#60A5FA"),
    "hidden": ("#15803D", "#4ADE80"),
    "ready": ("#15803D", "#4ADE80"),
    "working": ("#0F766E", "#2DD4BF"),
    "generating": ("#0F766E", "#2DD4BF"),
    "downloading": ("#0F766E", "#2DD4BF"),
    "waiting": ("#B45309", "#FBBF24"),
    "recovering": ("#7C3AED", "#C4B5FD"),
    "login_required": ("#C2410C", "#FB923C"),
    "credits": ("#B91C1C", "#F87171"),
    "error": ("#B91C1C", "#F87171"),
    "done": ("#15803D", "#4ADE80"),
    "idle": ("#6B7280", "#9CA3AF"),
    "stopped": ("#6B7280", "#9CA3AF"),
}

STATE_TEXT = {
    "closed": "Belum dibuka",
    "launching": "Membuka...",
    "connecting": "Menghubungkan...",
    "visible": "Terbuka — silakan login",
    "hidden": "Siap (tersembunyi)",
    "ready": "Siap",
    "working": "Bekerja",
    "generating": "Generate...",
    "downloading": "Download...",
    "waiting": "Menunggu",
    "recovering": "Pulihkan sesi...",
    "login_required": "Perlu login",
    "credits": "Limit tercapai",
    "error": "Error",
    "done": "Selesai",
    "idle": "Idle — tanpa prompt",
    "stopped": "Dihentikan",
}


# ==============================================================================
# LOGGING
# ==============================================================================
def setup_logging() -> logging.Logger:
    root = logging.getLogger()
    if getattr(root, "_flow_bot_configured", False):
        return logging.getLogger("bot")

    root.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    file_handler = logging.FileHandler(config.LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    root.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.setLevel(logging.INFO)
    root.addHandler(console)

    root._flow_bot_configured = True  # type: ignore[attr-defined]
    return logging.getLogger("bot")


logger = setup_logging()


# ==============================================================================
# APLIKASI
# ==============================================================================
class BotApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title(f"{APP_TITLE} v{APP_VERSION}")
        self.geometry("1040x880")
        self.minsize(920, 760)

        # ---------------- state ----------------
        self.manager = BrowserManager(config.DEFAULT_NUM_BROWSERS)
        self.num_browsers = tk.IntVar(value=config.DEFAULT_NUM_BROWSERS)
        self.hide_on_start = tk.BooleanVar(value=config.HIDE_ON_START)
        self.prompt_file = tk.StringVar(value="")
        self.prompt_count = 0
        self.is_running = False
        self.stop_event = threading.Event()
        self.runner_thread: threading.Thread | None = None
        self.runner: BotRunner | None = None
        self.queue: PromptQueue | None = None
        self.card_state: dict[int, str] = {}
        self.card_message: dict[int, str] = {}
        self.card_visible: dict[int, bool] = {}
        self.card_widgets: dict[int, dict] = {}
        self._last_stats: dict[str, int] = {}

        # --- cache/optimasi UI (mencegah redraw & I/O berulang) --------------
        # Log dikumpulkan di deque, lalu ditulis sekali per tick (bukan satu
        # event Tk per baris log).
        self._log_buffer: deque[str] = deque(maxlen=4000)
        # Sidik jari tampilan tiap kartu; kalau sama, tidak di-configure ulang.
        self._card_render_cache: dict[int, tuple] = {}
        self._browser_label_cache: str | None = None
        # Hasil cek browser hidup (diisi thread pemantau, bukan thread UI).
        self._browser_alive: dict[int, bool] = {}
        self._poll_busy = False
        # Sidik jari file prompt supaya tidak dibaca ulang tiap event progres.
        self._prompt_file_sig: tuple[str, int, int] | None = None
        self._prompt_info_text = ""
        self._progress_text = ""
        self._active_hint: tuple[str, str] | None = None
        self._run_btn_on: bool | None = None
        # Opsi sembunyikan diambil saat start, supaya thread worker tidak
        # menyentuh variabel Tk (Tk hanya boleh diakses dari thread UI).
        self._run_hide_browsers = bool(self.hide_on_start.get())

        # ---------------- UI ----------------
        self._build_ui()
        self._detect_default_prompt_file()
        self._rebuild_cards()
        self._refresh_start_button()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(3000, self._poll_browsers)

    # ==========================================================================
    # UI
    # ==========================================================================
    def _build_ui(self) -> None:
        # ----- Header -----
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=18, pady=(14, 6))

        ctk.CTkLabel(
            header, text=f"🎵 {APP_TITLE}",
            font=ctk.CTkFont(size=26, weight="bold"),
        ).pack(side="left")
        ctk.CTkLabel(
            header, text=f"v{APP_VERSION}", text_color="gray",
            font=ctk.CTkFont(size=13),
        ).pack(side="left", padx=(8, 0), pady=(8, 0))

        self.lbl_browser_header = ctk.CTkLabel(
            header, text=f"Browser: {self._current_browser_label()}",
            text_color="gray", font=ctk.CTkFont(size=12),
        )
        self.lbl_browser_header.pack(side="right", pady=(8, 0))

        # ----- Browser -----
        browser_frame = ctk.CTkFrame(self)
        browser_frame.pack(fill="x", padx=18, pady=6)

        top = ctk.CTkFrame(browser_frame, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(
            top, text="1. Browser",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(side="left")
        ctk.CTkLabel(
            top, text="Klik kartu untuk membuka browser & login manual.",
            text_color="gray", font=ctk.CTkFont(size=12),
        ).pack(side="left", padx=(10, 0))

        # Pilih browser yang dipakai: Brave, Chrome, Chromium, atau Edge —
        # hanya browser yang benar-benar terpasang yang muncul.
        browser_options = config.available_browsers()
        self._browser_label_to_option: dict[str, dict] = {}
        labels: list[str] = []
        for option in browser_options:
            label = option["label"]
            if label in self._browser_label_to_option:
                label = f"{label} ({option['id']})"
            self._browser_label_to_option[label] = option
            labels.append(label)

        self.option_browser = ctk.CTkOptionMenu(
            top,
            values=labels or ["Tidak ditemukan"],
            width=150,
            command=self._on_browser_change,
            state="normal" if labels else "disabled",
        )
        self.option_browser.pack(side="right", padx=(0, 8))
        self._sync_browser_menu()

        ctk.CTkLabel(
            top, text="Browser:", font=ctk.CTkFont(size=12)
        ).pack(side="right", padx=(0, 6))

        self.option_browsers = ctk.CTkOptionMenu(
            top,
            values=[str(i) for i in range(1, config.MAX_NUM_BROWSERS + 1)],
            width=70,
            command=self._on_browser_count_change,
        )
        self.option_browsers.set(str(config.DEFAULT_NUM_BROWSERS))
        self.option_browsers.pack(side="right")

        ctk.CTkLabel(
            top, text="Jumlah:", font=ctk.CTkFont(size=12)
        ).pack(side="right", padx=(0, 6))

        self.cards_frame = ctk.CTkFrame(browser_frame, fg_color="transparent")
        self.cards_frame.pack(fill="x", padx=10, pady=(4, 10))

        # ----- Prompt -----
        prompt_frame = ctk.CTkFrame(self)
        prompt_frame.pack(fill="x", padx=18, pady=6)

        ctk.CTkLabel(
            prompt_frame, text="2. File Prompt",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(anchor="w", padx=14, pady=(10, 2))

        prompt_row = ctk.CTkFrame(prompt_frame, fg_color="transparent")
        prompt_row.pack(fill="x", padx=14, pady=(2, 4))

        self.lbl_prompt = ctk.CTkLabel(
            prompt_row, text="Belum ada file dipilih",
            fg_color=("#E5E7EB", "#2B2B2B"), corner_radius=8,
            height=34, anchor="w",
        )
        self.lbl_prompt.pack(side="left", fill="x", expand=True, padx=(0, 10))

        ctk.CTkButton(
            prompt_row, text="🔄", width=44,
            command=self._refresh_prompt_file,
        ).pack(side="right", padx=(0, 8))

        ctk.CTkButton(
            prompt_row, text="📁 Pilih File .txt",
            command=self._pick_prompt_file, width=160,
        ).pack(side="right", padx=(0, 8))

        self.lbl_prompt_info = ctk.CTkLabel(
            prompt_frame, text="", text_color="gray",
            font=ctk.CTkFont(size=12),
        )
        self.lbl_prompt_info.pack(anchor="w", padx=14, pady=(0, 10))

        # ----- Run -----
        run_frame = ctk.CTkFrame(self)
        run_frame.pack(fill="x", padx=18, pady=6)

        ctk.CTkLabel(
            run_frame, text="3. Jalankan",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(anchor="w", padx=14, pady=(10, 2))

        run_row = ctk.CTkFrame(run_frame, fg_color="transparent")
        run_row.pack(fill="x", padx=14, pady=(2, 6))

        self.btn_run = ctk.CTkButton(
            run_row, text="▶️  JALANKAN BOT",
            command=self._start_bot,
            fg_color="#16A34A", hover_color="#15803D",
            font=ctk.CTkFont(size=15, weight="bold"),
            height=46,
        )
        self.btn_run.pack(side="left", fill="x", expand=True, padx=(0, 10))

        self.btn_stop = ctk.CTkButton(
            run_row, text="⏹️  STOP",
            command=self._stop_bot,
            fg_color="#DC2626", hover_color="#B91C1C",
            font=ctk.CTkFont(size=15, weight="bold"),
            height=46, width=130, state="disabled",
        )
        self.btn_stop.pack(side="right")

        info_row = ctk.CTkFrame(run_frame, fg_color="transparent")
        info_row.pack(fill="x", padx=14, pady=(0, 10))

        self.chk_hide = ctk.CTkCheckBox(
            info_row,
            text="Sembunyikan browser saat bot berjalan",
            variable=self.hide_on_start,
            font=ctk.CTkFont(size=12),
        )
        self.chk_hide.pack(side="left")

        self.lbl_progress = ctk.CTkLabel(
            info_row, text="", text_color="gray",
            font=ctk.CTkFont(size=12),
        )
        self.lbl_progress.pack(side="right")

        self.lbl_active_hint = ctk.CTkLabel(
            run_frame,
            text="Hanya browser yang sudah dibuka (dan login) yang akan dipakai.",
            text_color="gray",
            font=ctk.CTkFont(size=11),
        )
        self.lbl_active_hint.pack(anchor="w", padx=14, pady=(0, 10))

        # ----- Log -----
        log_frame = ctk.CTkFrame(self)
        log_frame.pack(fill="both", expand=True, padx=18, pady=(6, 14))

        ctk.CTkLabel(
            log_frame, text="📋 Log Aktivitas",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(anchor="w", padx=14, pady=(10, 2))

        self.log_box = ctk.CTkTextbox(
            log_frame, font=ctk.CTkFont(family="Courier", size=11),
            wrap="word",
        )
        self.log_box.pack(fill="both", expand=True, padx=14, pady=(2, 12))
        self.log_box.configure(state="disabled")
        self._attach_gui_log_handler()

    # ==========================================================================
    # LOG KE GUI
    # ==========================================================================
    def _attach_gui_log_handler(self) -> None:
        buffer = self._log_buffer

        class GUIHandler(logging.Handler):
            def emit(self, record):
                # Cukup masukkan ke antrean; penulisan ke widget dilakukan
                # sekaligus oleh _drain_log (jauh lebih ringan untuk UI).
                try:
                    buffer.append(self.format(record))
                except Exception:
                    pass

        handler = GUIHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S")
        )
        handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(handler)
        self.after(200, self._drain_log)

    def _drain_log(self) -> None:
        """Tulis log yang terkumpul sekaligus, maksimal beberapa kali per detik."""
        try:
            if self._log_buffer:
                lines = []
                # Batasi per tick supaya Tk tidak kewalahan saat log membanjir.
                for _ in range(min(len(self._log_buffer), 200)):
                    try:
                        lines.append(self._log_buffer.popleft())
                    except IndexError:
                        break
                if lines:
                    self._append_log("\n".join(lines))
        except Exception:
            pass
        finally:
            try:
                self.after(200, self._drain_log)
            except Exception:
                pass

    def _append_log(self, message: str) -> None:
        try:
            # Kalau user sedang scroll ke atas, jangan paksa lompat ke bawah.
            at_bottom = self.log_box.yview()[1] >= 0.999
            self.log_box.configure(state="normal")
            self.log_box.insert("end", message + "\n")
            # Batasi supaya tidak tumbuh tanpa batas (dipotong sekali saja).
            total = int(self.log_box.index("end-1c").split(".")[0])
            if total > LOG_MAX_LINES:
                self.log_box.delete("1.0", f"{total - LOG_MAX_LINES + 1}.0")
                total = LOG_MAX_LINES
            self.log_box.configure(state="disabled")
            if at_bottom:
                self.log_box.see("end")
        except Exception:
            pass

    # ==========================================================================
    # KARTU BROWSER
    # ==========================================================================
    def _rebuild_cards(self) -> None:
        for widget in self.cards_frame.winfo_children():
            widget.destroy()
        self.card_widgets.clear()
        self._card_render_cache.clear()

        count = self.manager.count
        columns = min(count, 5)

        for index in range(1, count + 1):
            profile = self.manager.get(index)
            state = self.card_state.get(index, "closed")
            message = self.card_message.get(index, "")
            self._build_card(index, profile, state, message, columns)

        self._update_active_hint()

    def _build_card(self, index, profile, state, message, columns) -> None:
        row = (index - 1) // columns
        column = (index - 1) % columns

        card = ctk.CTkFrame(
            self.cards_frame, corner_radius=10, border_width=1,
            border_color=("#D1D5DB", "#3F3F46"),
            fg_color=("#F9FAFB", "#242424"),
        )
        card.grid(row=row, column=column, padx=5, pady=5, sticky="nsew")
        self.cards_frame.grid_columnconfigure(column, weight=1)

        title = ctk.CTkLabel(
            card, text=profile.name if profile else f"Browser {index}",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        title.pack(anchor="w", padx=10, pady=(8, 0))

        port = profile.port if profile else config.base_port(index)
        subtitle = ctk.CTkLabel(
            card, text=self._browser_subtitle(port),
            text_color="gray", font=ctk.CTkFont(size=11),
        )
        subtitle.pack(anchor="w", padx=10)

        status = ctk.CTkLabel(
            card, text=STATE_TEXT.get(state, state),
            text_color=STATE_COLORS.get(state, "gray"),
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        status.pack(anchor="w", padx=10, pady=(4, 0))

        detail = ctk.CTkLabel(
            card, text=(message[:42] + "…") if len(message) > 42 else message,
            text_color="gray", font=ctk.CTkFont(size=10),
            wraplength=185, justify="left",
        )
        detail.pack(anchor="w", padx=10)

        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.pack(fill="x", padx=8, pady=(6, 8))

        btn_open = ctk.CTkButton(
            buttons, text="Buka", width=1, height=26,
            font=ctk.CTkFont(size=11),
            command=lambda i=index: self._card_open(i),
        )
        btn_open.pack(side="left", expand=True, fill="x", padx=(0, 3))

        btn_hide = ctk.CTkButton(
            buttons, text="Sembunyi", width=1, height=26,
            font=ctk.CTkFont(size=11),
            fg_color=("#6B7280", "#4B5563"), hover_color=("#4B5563", "#374151"),
            command=lambda i=index: self._card_hide(i),
        )
        btn_hide.pack(side="left", expand=True, fill="x", padx=(0, 3))

        # "Cek" = cek ulang browser yang error/limit, lalu lanjutkan worker-nya
        # (prompt yang tadi ditahan akan dipaste ulang).
        btn_check = ctk.CTkButton(
            buttons, text="Cek", width=1, height=26,
            font=ctk.CTkFont(size=11),
            fg_color=("#0284C7", "#0369A1"), hover_color=("#0369A1", "#075985"),
            command=lambda i=index: self._card_check(i),
        )
        btn_check.pack(side="left", expand=True, fill="x")

        self.card_widgets[index] = {
            "card": card,
            "title": title,
            "subtitle": subtitle,
            "status": status,
            "detail": detail,
            "btn_open": btn_open,
            "btn_hide": btn_hide,
            "btn_check": btn_check,
        }

        # Klik di mana saja pada kartu = buka / tampilkan browser.
        for widget in (card, title, subtitle, status, detail):
            widget.bind("<Button-1>", lambda _event, i=index: self._card_open(i))

        self._render_card(index)

    def _set_card(self, index: int, state: str, message: str = "",
                  visible: bool | None = None) -> None:
        self.card_state[index] = state
        self.card_message[index] = message
        if visible is not None:
            self.card_visible[index] = visible
        self._render_card(index)

    def _render_card(self, index: int) -> None:
        widgets = self.card_widgets.get(index)
        if not widgets:
            return
        state = self.card_state.get(index, "closed")
        message = self.card_message.get(index, "")
        profile = self.manager.get(index)

        is_closed = state == "closed"
        open_text = "Buka" if is_closed else "Tampilkan"
        open_state = "disabled" if state in {"launching", "connecting"} else "normal"
        hide_state = "normal" if not is_closed else "disabled"
        # "Cek" hanya aktif bila bot sedang jalan dan worker browser ini
        # memang tertahan karena error/limit (bukan sedang bekerja).
        runner = self.runner
        can_check = bool(
            self.is_running and runner is not None and runner.can_resume(index)
        )
        check_state = "normal" if can_check else "disabled"
        detail_text = (message[:42] + "…") if len(message) > 42 else message
        title_text = profile.name if profile else None
        subtitle_text = self._browser_subtitle(profile.port) if profile else None

        # Kalau tidak ada yang berubah, jangan sentuh widget sama sekali:
        # configure() memicu redraw dan inilah sumber UI "berkedip".
        signature = (
            state, detail_text, open_text, open_state, hide_state,
            check_state, title_text, subtitle_text,
        )
        if self._card_render_cache.get(index) == signature:
            return
        self._card_render_cache[index] = signature

        widgets["status"].configure(
            text=STATE_TEXT.get(state, state),
            text_color=STATE_COLORS.get(state, "gray"),
        )
        widgets["detail"].configure(text=detail_text)
        widgets["btn_open"].configure(text=open_text, state=open_state)
        # Tombol sembunyi selalu bisa dipakai selama browser hidup — tidak
        # tergantung apakah jendela sedang terlihat atau sudah diminimalkan.
        widgets["btn_hide"].configure(state=hide_state)
        widgets["btn_check"].configure(state=check_state)
        if title_text is not None:
            widgets["title"].configure(text=title_text)
        if subtitle_text is not None:
            widgets["subtitle"].configure(text=subtitle_text)

    def _browser_label(self) -> str:
        """Label browser (di-cache; berubah hanya saat menu Browser diganti)."""
        if self._browser_label_cache is None:
            self._browser_label_cache = self._current_browser_label()
        return self._browser_label_cache

    def _browser_subtitle(self, port: int) -> str:
        text = f"Port {port}"
        label = self._browser_label()
        if label != "tidak ditemukan":
            text += f" • {label}"
        return text

    def _card_open(self, index: int) -> None:
        profile = self.manager.get(index)
        if profile is None:
            return
        self._set_card(index, "launching", "Membuka browser...", visible=True)
        self._run_async(lambda: self._do_open(profile))

    def _do_open(self, profile) -> None:
        try:
            ok = profile.toggle()
            if not ok:
                self._ui(self._set_card, profile.index, "error",
                         profile.last_error or "Gagal membuka browser", False)
                return
            self._ui(self._set_card, profile.index, "visible",
                     "Silakan login, lalu klik Sembunyi", True)
            # Cek login otomatis (best effort).
            state, message = profile.check_login()
            if state == "ready":
                self._ui(self._set_card, profile.index, "visible",
                         "Sudah login — klik Sembunyi bila selesai", True)
            elif state == "login_required":
                self._ui(self._set_card, profile.index, "visible",
                         "Silakan login manual", True)
        except Exception as e:
            logger.error(f"Gagal membuka {profile.name}: {e}")
            self._ui(self._set_card, profile.index, "error", str(e), False)

    def _card_hide(self, index: int) -> None:
        profile = self.manager.get(index)
        if profile is None:
            return
        self._run_async(lambda: self._do_hide(profile))

    def _do_hide(self, profile) -> None:
        try:
            profile.hide()
        except Exception as e:
            logger.warning(f"Gagal menyembunyikan {profile.name}: {e}")
        state, message = profile.check_login()
        if state == "ready":
            self._ui(self._set_card, profile.index, "hidden",
                     "Siap — sudah login", False)
        elif state == "login_required":
            self._ui(self._set_card, profile.index, "login_required",
                     "Belum login — klik Buka untuk login", False)
        elif state == "dead":
            self._ui(self._set_card, profile.index, "closed",
                     "Browser tertutup", False)
        else:
            self._ui(self._set_card, profile.index, "hidden",
                     message or "Siap (tersembunyi)", False)

    # --------------------------------------------------------------------------
    # CEK ULANG BROWSER YANG ERROR
    # --------------------------------------------------------------------------
    def _card_check(self, index: int) -> None:
        """Cek ulang browser yang error/limit, lalu lanjutkan dari prompt tadi."""
        profile = self.manager.get(index)
        if profile is None:
            return
        if not self.is_running or self.runner is None:
            messagebox.showinfo(
                "Bot belum berjalan",
                "Tombol Cek dipakai saat bot berjalan dan ada browser yang "
                "error.\n\nKalau bot sudah selesai, tekan ▶️ JALANKAN BOT lagi.",
            )
            return

        self._set_card(index, "connecting", "Mengecek browser...")
        self._run_async(lambda: self._do_check(profile))

    def _do_check(self, profile) -> None:
        try:
            # Browser mungkin ditutup / crash: JANGAN langsung dianggap siap.
            if not profile.cdp_ready():
                self._ui(
                    self._set_card, profile.index, "error",
                    "Tidak terdeteksi — klik Buka dulu",
                )
                return

            runner = self.runner
            if runner is None or not runner.resume(profile):
                if runner is not None and runner.is_active(profile.index):
                    # Sudah dilanjutkan (mis. tombol terklik dua kali).
                    self._ui(self._set_card, profile.index, "connecting",
                             "Mengecek browser...")
                else:
                    self._ui(
                        self._set_card, profile.index, "error",
                        "Belum bisa dilanjutkan — coba lagi",
                    )
                return
            # Status berikutnya (connecting → ready / login_required) dikirim
            # oleh worker yang baru dijalankan.
        except Exception as e:
            logger.error(f"Gagal cek {profile.name}: {e}")
            self._ui(self._set_card, profile.index, "error", str(e))

    def _on_worker_idle(self, index: int) -> None:
        """Worker browser ini berhenti: segarkan tombol (Cek jadi aktif)."""
        self._ui(self._render_card, index)

    def _poll_browsers(self) -> None:
        """Pantau browser yang ditutup manual.

        Pengecekan CDP (HTTP dengan timeout) dilakukan di thread terpisah —
        kalau dijalankan di thread UI, 4–5 browser membuat aplikasi tersendat.
        """
        if not self._poll_busy:
            self._poll_busy = True
            threading.Thread(target=self._poll_probe, daemon=True).start()
        # Saat bot berjalan cukup 5 detik sekali; pekerjaan utamanya sudah
        # dilaporkan worker lewat callback status.
        self.after(5000 if self.is_running else 3000, self._poll_browsers)

    def _poll_probe(self) -> None:
        """Cek semua browser di background thread, lalu kirim ke thread UI."""
        results: dict[int, bool] = {}
        try:
            for index in range(1, self.manager.count + 1):
                profile = self.manager.get(index)
                if profile is None:
                    continue
                try:
                    results[index] = bool(profile.cdp_ready())
                except Exception:
                    results[index] = False
        finally:
            self._ui(self._apply_poll_results, results)

    def _apply_poll_results(self, results: dict[int, bool]) -> None:
        """Terapkan hasil cek browser di thread UI."""
        self._poll_busy = False
        try:
            # Ganti seluruh isi cache supaya tidak ada index lama yang nyangkut
            # (mis. setelah jumlah/browser diganti).
            self._browser_alive = {
                index: alive for index, alive in results.items()
                if 1 <= index <= self.manager.count
            }
            active = sum(1 for alive in self._browser_alive.values() if alive)
            for index, alive in self._browser_alive.items():
                state = self.card_state.get(index, "closed")
                if state != "closed" and not alive and not self.is_running:
                    self._set_card(index, "closed", "Browser tertutup", False)
                elif state == "closed" and alive:
                    self._set_card(index, "hidden", "Browser terdeteksi", False)
            # Segarkan tombol "Cek" (aktif/nonaktif) selama bot berjalan.
            if self.is_running:
                for index in range(1, self.manager.count + 1):
                    self._render_card(index)
            self._update_active_hint(active)
        except Exception:
            pass

    def _update_active_hint(self, active: int | None = None) -> None:
        """Tampilkan berapa browser yang siap dipakai."""
        if active is None:
            # Pakai hasil pemantauan terakhir — jangan cek CDP dari thread UI.
            active = sum(1 for alive in self._browser_alive.values() if alive)
        if self.is_running:
            text = f"Sedang berjalan dengan {active} browser."
            color = "#16A34A"
        elif active:
            text = (
                f"Browser aktif: {active} dari {self.manager.count} — "
                "hanya yang sudah dibuka yang dipakai."
            )
            color = "gray"
        else:
            text = "Belum ada browser terbuka. Klik kartu Browser 1 untuk mulai."
            color = "#D97706"
        if self._active_hint == (text, color):
            return
        self._active_hint = (text, color)
        try:
            self.lbl_active_hint.configure(text=text, text_color=color)
        except Exception:
            pass

    # ==========================================================================
    # PILIH BROWSER
    # ==========================================================================
    def _current_browser_label(self) -> str:
        try:
            return config.get_browser_label()
        except Exception:
            return "tidak ditemukan"

    def _refresh_browser_header(self) -> None:
        self._browser_label_cache = None
        try:
            self.lbl_browser_header.configure(
                text=f"Browser: {self._current_browser_label()}"
            )
        except Exception:
            pass
        # Label di kartu ikut berubah bila browser diganti.
        for index in self.card_widgets:
            self._card_render_cache.pop(index, None)
            self._render_card(index)

    def _sync_browser_menu(self) -> None:
        """Tampilkan browser yang benar-benar dipakai di dropdown."""
        try:
            current = os.path.realpath(config.get_browser_path())
        except Exception:
            current = ""
        for label, option in self._browser_label_to_option.items():
            if current and os.path.realpath(option["path"]) == current:
                try:
                    self.option_browser.set(label)
                except Exception:
                    pass
                return

    def _on_browser_change(self, label: str) -> None:
        option = self._browser_label_to_option.get(label)
        if not option:
            return

        if self.is_running:
            messagebox.showwarning(
                "Sedang berjalan",
                "Browser tidak bisa diubah saat bot berjalan.",
            )
            self._sync_browser_menu()
            return

        if self.manager.any_running():
            messagebox.showwarning(
                "Tutup browser dulu",
                "Masih ada browser yang terbuka.\n\n"
                "Tutup semua jendela browser (atau tutup aplikasi lalu buka "
                "lagi) sebelum mengganti browser, supaya profil & login tidak "
                "tertukar.",
            )
            self._sync_browser_menu()
            return

        try:
            config.set_browser_choice(option["id"])
        except ValueError as e:
            messagebox.showerror("Browser tidak ditemukan", str(e))
            self._sync_browser_menu()
            return

        # Folder profil/login mengikuti browser yang dipilih, jadi buat ulang
        # manager supaya kartu memakai folder yang benar sejak awal.
        self.manager = BrowserManager(self.manager.count)
        self._browser_label_cache = None
        self._browser_alive.clear()
        self._active_hint = None
        self.card_state = {
            index: "closed" for index in range(1, self.manager.count + 1)
        }
        self.card_message.clear()
        self.card_visible.clear()
        self._rebuild_cards()
        self._refresh_browser_header()
        logger.info(f"🌐 Browser diatur ke {config.get_browser_label()}.")

    # ==========================================================================
    # JUMLAH BROWSER
    # ==========================================================================
    def _on_browser_count_change(self, value: str) -> None:
        if self.is_running:
            messagebox.showwarning(
                "Sedang berjalan",
                "Jumlah browser tidak bisa diubah saat bot berjalan.",
            )
            self.option_browsers.set(str(self.manager.count))
            return

        try:
            count = int(value)
        except ValueError:
            return

        # Tutup browser tambahan (hanya yang diluncurkan aplikasi).
        self.manager.set_count(count)
        self.num_browsers.set(count)
        self.card_state = {i: s for i, s in self.card_state.items() if i <= count}
        self._rebuild_cards()
        logger.info(f"Jumlah browser diatur ke {count}.")

    # ==========================================================================
    # FILE PROMPT
    # ==========================================================================
    def _detect_default_prompt_file(self) -> None:
        default = config.DEFAULT_PROMPT_FILE
        if default.exists() and count_prompts(default) > 0:
            self._set_prompt_file(str(default))

    def _pick_prompt_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Pilih file prompt (.txt)",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self._set_prompt_file(path)

    def _refresh_prompt_file(self) -> None:
        """Baca ulang file yang sedang dipilih (mis. setelah diedit)."""
        path = self.prompt_file.get()
        if path and os.path.exists(path):
            self._set_prompt_file(path)
            logger.info("File prompt dimuat ulang.")
        else:
            self._detect_default_prompt_file()
            self._refresh_start_button()

    def _set_prompt_file(self, path: str) -> None:
        self.prompt_file.set(path)
        self.prompt_count = count_prompts(path)
        self.lbl_prompt.configure(text=os.path.basename(path))
        if self.prompt_count > 0:
            self.lbl_prompt_info.configure(
                text=f"✅ {self.prompt_count} prompt terdeteksi "
                     f"(satu prompt = satu paragraf).",
                text_color=("#15803D", "#4ADE80"),
            )
            logger.info(f"📁 File prompt: {path} ({self.prompt_count} prompt)")
        else:
            self.lbl_prompt_info.configure(
                text="⚠️ File ini tidak berisi prompt. Pisahkan tiap prompt "
                     "dengan satu baris kosong.",
                text_color=("#B91C1C", "#F87171"),
            )
            logger.warning(f"File prompt kosong: {path}")
        self._refresh_start_button()

    def _refresh_start_button(self) -> None:
        # Jumlah prompt bisa berkurang selama bot berjalan (prompt yang sudah
        # dipakai dipangkas dari file), jadi hitung ulang dari file —
        # memakai cache supaya tidak membaca file tiap event progres.
        path = self.prompt_file.get()
        exists = bool(path) and os.path.exists(path)
        if exists:
            self.prompt_count = self._count_prompts_cached(path)
        elif path:
            self.prompt_count = 0
        enabled = (
            not self.is_running
            and bool(path)
            and self.prompt_count > 0
            and exists
        )
        if enabled == self._run_btn_on:
            return
        self._run_btn_on = enabled
        self.btn_run.configure(state="normal" if enabled else "disabled")

    def _set_run_controls_enabled(self, enabled: bool) -> None:
        """Kunci / buka kontrol yang tidak boleh diubah saat bot berjalan."""
        self._run_btn_on = None  # paksa _refresh_start_button menulis ulang
        self.btn_run.configure(state="normal" if enabled else "disabled")
        self.btn_stop.configure(state="disabled" if enabled else "normal")
        self.option_browsers.configure(
            state="normal" if enabled else "disabled"
        )
        self.option_browser.configure(
            state="normal"
            if enabled and self._browser_label_to_option
            else "disabled"
        )
        self.chk_hide.configure(state="normal" if enabled else "disabled")

    # ==========================================================================
    # JALANKAN / STOP
    # ==========================================================================
    def _start_bot(self) -> None:
        if self.is_running:
            return

        prompt_file = self.prompt_file.get()
        if not prompt_file or not os.path.exists(prompt_file):
            messagebox.showerror("Error", "File prompt belum dipilih!")
            return

        self.prompt_count = count_prompts(prompt_file)
        if self.prompt_count == 0:
            messagebox.showerror(
                "Error",
                "Tidak ada prompt di file tersebut.\n\n"
                "Pisahkan setiap prompt dengan satu baris kosong.",
            )
            return

        # Hanya browser yang SUDAH DIBUKA oleh user yang dipakai. Browser yang
        # masih tertutup tidak ikut diluncurkan, supaya bot tidak tiba-tiba
        # membuka semua browser.
        # Pakai hasil pemantauan terakhir bila ada, supaya klik JALANKAN tidak
        # memblokir UI dengan probe CDP berulang.
        profiles = []
        for profile in self.manager.profiles:
            if profile.proc is not None:
                alive = profile.proc.poll() is None
            else:
                alive = self._browser_alive.get(profile.index)
                if alive is None:
                    alive = profile.cdp_ready()
            if alive:
                profiles.append(profile)
        if not profiles:
            messagebox.showwarning(
                "Belum ada browser",
                "Buka minimal satu browser dulu:\n\n"
                "1. Klik kartu Browser 1 (atau browser lain).\n"
                "2. Login ke Flow Music di jendela itu.\n"
                "3. Klik Sembunyi kalau sudah selesai.\n\n"
                "Bot hanya memakai browser yang sudah terbuka.",
            )
            return

        # Dibaca sekali di thread UI; thread worker hanya memakai nilai ini.
        self._run_hide_browsers = bool(self.hide_on_start.get())
        confirmed = messagebox.askyesno(
            "Mulai bot",
            f"File prompt  : {os.path.basename(prompt_file)}\n"
            f"Jumlah prompt: {self.prompt_count}\n"
            f"Browser aktif: {len(profiles)} "
            f"(dari {self.manager.count} kartu)\n\n"
            "Hanya browser yang sudah kamu buka yang akan bekerja.\n"
            "Pastikan browser tersebut sudah login ke Flow Music.\n"
            f"Browser akan {'disembunyikan' if self._run_hide_browsers else 'ditampilkan'} "
            "saat bot berjalan.\n\n"
            "Mulai sekarang?",
        )
        if not confirmed:
            return

        # Browser yang kartunya sudah "Sembunyi" tetap tersembunyi, walaupun
        # opsi "Sembunyikan browser saat bot berjalan" tidak dicentang.
        for profile in profiles:
            profile.keep_hidden = self.card_state.get(profile.index) == "hidden"

        self.is_running = True
        self._update_active_hint(len(profiles))
        self.stop_event = threading.Event()
        self._set_run_controls_enabled(False)
        self.lbl_progress.configure(text="Menyiapkan...")

        try:
            self.queue = PromptQueue(prompt_file, config.MAX_PROMPT_ATTEMPTS)
        except Exception as e:
            self.is_running = False
            self._set_run_controls_enabled(True)
            messagebox.showerror("Error", f"Gagal membaca prompt: {e}")
            return

        if not self.queue.items:
            self.is_running = False
            self._set_run_controls_enabled(True)
            messagebox.showerror("Error", "Antrean prompt kosong.")
            return

        logger.info("=" * 70)
        logger.info(f"🚀 BOT DIMULAI — {len(profiles)} browser, "
                    f"{len(self.queue.items)} prompt")
        logger.info(f"📂 Hasil & state: {self.queue.run_dir}")
        logger.info("=" * 70)
        self._update_progress(self.queue.stats())

        runner = BotRunner(
            profiles=profiles,
            queue=self.queue,
            stop_event=self.stop_event,
            on_status=self._on_worker_status,
            on_progress=self._on_worker_progress,
            on_idle=self._on_worker_idle,
            hide_browsers=self._run_hide_browsers,
        )
        self.runner = runner
        self.runner_thread = threading.Thread(
            target=self._run_bot, args=(runner,), daemon=True
        )
        self.runner_thread.start()

    def _run_bot(self, runner: BotRunner) -> None:
        try:
            runner.run()
        except Exception as e:
            logger.error(f"Bot berhenti karena error: {e}", exc_info=True)
        finally:
            self._ui(self._finish_run)

    def _stop_bot(self) -> None:
        if not self.is_running:
            return
        logger.warning("⏹️  Stop diminta. Menyelesaikan proses yang sedang berjalan...")
        self.stop_event.set()
        self.btn_stop.configure(state="disabled")
        self.lbl_progress.configure(text="Menghentikan...")

    def _finish_run(self) -> None:
        runner = self.runner
        self.is_running = False
        self.runner = None
        self._set_run_controls_enabled(True)
        for index in range(1, self.manager.count + 1):
            self._render_card(index)

        if self.queue is not None:
            self.queue.release_running()
        stats = self.queue.stats() if self.queue else {}
        self._update_progress(stats)
        self._refresh_start_button()
        self._update_active_hint()
        logger.info(
            f"🏁 Selesai — {stats.get('done', 0)} sukses, "
            f"{stats.get('failed', 0)} gagal, {stats.get('pending', 0)} sisa."
        )

        cleanup = runner.last_cleanup if runner is not None else None
        summary = (
            f"Selesai!\n\n"
            f"✅ Sukses : {stats.get('done', 0)}\n"
            f"❌ Gagal  : {stats.get('failed', 0)}\n"
            f"⏳ Sisa   : {stats.get('pending', 0)}\n"
        )
        if cleanup:
            deleted = cleanup.get("deleted", 0)
            freed = cleanup.get("freed", 0) / (1024 * 1024)
            if deleted:
                summary += (
                    f"\n🧹 Salinan ganda dihapus: {deleted} file "
                    f"({freed:.1f} MB)\n"
                )
            else:
                summary += "\n🧹 Tidak ada salinan lagu ganda.\n"
        summary += f"\nFile musik ada di folder:\n{config.DOWNLOADS_DIR}"
        messagebox.showinfo("Bot selesai", summary)

    # ==========================================================================
    # CALLBACK DARI WORKER
    # ==========================================================================
    def _on_worker_status(self, index: int, state: str, message: str) -> None:
        visible = None
        if state in {"launching", "connecting"}:
            # Runner membuka browser tersembunyi (atau terlihat) sesuai checkbox
            # yang dibaca saat start — jangan sentuh variabel Tk dari thread ini.
            visible = not self._run_hide_browsers
        elif state == "ready":
            visible = self.card_visible.get(index, False)
        # _set_card sudah me-render kartu ini; ringkasan progres ikut disegarkan.
        self._ui(self._apply_worker_status, index, state, message, visible)

    def _apply_worker_status(self, index: int, state: str, message: str,
                             visible: bool | None) -> None:
        """Terapkan status worker di thread UI (satu event, bukan tiga)."""
        self._set_card(index, state, message, visible)
        self._refresh_progress_text()

    def _on_worker_progress(self, stats: dict) -> None:
        self._ui(self._update_progress, stats)

    def _update_progress(self, stats: dict) -> None:
        if not stats:
            self.lbl_progress.configure(text="")
            self._progress_text = ""
            return
        self._last_stats = stats
        self._refresh_progress_text()
        # Tampilkan sisa prompt yang masih ada di file sumber.
        path = self.prompt_file.get()
        if path and os.path.exists(path):
            self.prompt_count = self._count_prompts_cached(path)
            if self.prompt_count > 0:
                text = f"📝 Sisa {self.prompt_count} prompt di file."
                color = ("#15803D", "#4ADE80")
            else:
                text = "📝 Semua prompt di file sudah dipakai."
                color = ("#6B7280", "#9CA3AF")
            if text != self._prompt_info_text:
                self._prompt_info_text = text
                self.lbl_prompt_info.configure(text=text, text_color=color)
            self._refresh_start_button()

    def _count_prompts_cached(self, path: str) -> int:
        """Hitung prompt hanya bila file benar-benar berubah (mtime/ukuran)."""
        try:
            st = os.stat(path)
        except OSError:
            self._prompt_file_sig = None
            self.prompt_count = 0
            return 0
        sig = (path, st.st_mtime_ns, st.st_size)
        if sig != self._prompt_file_sig:
            self._prompt_file_sig = sig
            self.prompt_count = count_prompts(path)
        return self.prompt_count

    def _refresh_progress_text(self) -> None:
        """Perbarui label progres termasuk jumlah browser bekerja / idle."""
        stats = self._last_stats
        if not stats:
            return
        working_states = {"working", "generating", "downloading", "recovering"}
        working = sum(1 for s in self.card_state.values() if s in working_states)
        idle = sum(1 for s in self.card_state.values() if s == "idle")
        text = (
            f"Total {stats.get('total', 0)} • "
            f"Selesai {stats.get('done', 0)} • "
            f"Gagal {stats.get('failed', 0)} • "
            f"Sisa {stats.get('pending', 0)} • "
            f"Proses {stats.get('running', 0)}"
        )
        if working or idle:
            text += f" • Bekerja {working} • Idle {idle}"
        held = stats.get("held", 0)
        if held:
            text += f" • Tertahan {held} (klik Cek)"
        if text != self._progress_text:
            self._progress_text = text
            self.lbl_progress.configure(text=text)

    # ==========================================================================
    # THREADING & CLOSE
    # ==========================================================================
    def _ui(self, func, *args, **kwargs) -> None:
        """Jalankan fungsi di thread UI."""
        try:
            self.after(0, lambda: func(*args, **kwargs))
        except Exception:
            pass

    def _run_async(self, func) -> None:
        threading.Thread(target=func, daemon=True).start()

    def _on_close(self) -> None:
        if self.is_running:
            if not messagebox.askyesno(
                "Keluar",
                "Bot sedang berjalan. Hentikan bot dan tutup aplikasi?",
            ):
                return
            self.stop_event.set()
            # Beri worker waktu berhenti + membersihkan antrean (prompt yang
            # tertahan/berjalan dikembalikan ke file .txt).
            if self.runner_thread is not None:
                try:
                    self.runner_thread.join(timeout=5)
                except Exception:
                    pass
        if self.queue is not None:
            try:
                self.queue.release_running()
            except Exception as e:
                logger.warning(f"Gagal mengembalikan prompt ke antrean: {e}")

        try:
            self.manager.close_all()
        except Exception as e:
            logger.warning(f"Gagal menutup browser: {e}")
        self.destroy()


# ==============================================================================
# MAIN
# ==============================================================================
if __name__ == "__main__":
    app = BotApp()
    app.mainloop()
