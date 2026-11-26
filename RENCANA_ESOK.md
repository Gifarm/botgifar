# 🎵 Skema Bot Flow Music — Rencana Lanjutan (27 Nov 2026)

> Catatan kerja untuk sesi besok. Status hari ini: **MP3 sudah berhasil diklik**
> (hover cepat + JS dispatch), prompt sudah terhapus dari `.txt` saat dipakai,
> mode Sembunyi sudah stabil.

> **Status 26 Nov 2026:** Tugas 1–3 sudah diimplementasikan di kode
> (`browser_manager.py`, `worker.py`, `prompts.py`, `app.py`, `flow_actions.py`,
> `config.py`, `README.md`). Unit test prefs & antrean lulus. **Uji end-to-end
> dengan profil sementara belum dijalankan** (lihat bagian "Rencana uji").
> Catatan bug baru dari `bot.log`: file mp3 masuk `~/Downloads`, bukan
> `downloads/.browser_N` — akar masalah yang sama dengan Tugas 1, sudah
> ditangani lewat preferensi profil + CDP sedini mungkin.

---

## ✅ Yang sudah beres (jangan diutak-atik lagi)

| Fitur | Status |
|---|---|
| Klik titik tiga → hover `Download` → klik `MP3` tanpa menggeser mouse | ✅ jalan |
| Verifikasi klik lewat file baru di folder download (bukan asumsi) | ✅ |
| Prompt yang dipakai langsung dipangkas dari file `.txt` sumber | ✅ |
| Arsip `state/run_<tanggal>/queue.json`, `done.txt`, `failed.txt` | ✅ |
| Sembunyi → browser tetap tersembunyi selama bot bekerja | ✅ |
| Lagu terdeteksi → langsung diunduh (tidak kirim prompt baru) | ✅ |
| Error tidak lagi tertimpa status "Selesai" | ✅ |

---

## 🎯 Tiga tugas utama besok

### TUGAS 1 — Unduhan langsung masuk `downloads/`, TANPA dialog Save ✅

**Masalah:** setelah klik MP3, Brave memunculkan dialog simpan / file manager
laptop, jadi harus klik **Save** manual. Bot tidak bisa mengklik dialog native
itu (bukan bagian dari halaman, tidak terlihat oleh CDP).

**Akar masalah (dugaan):**
1. Preferensi profil Brave masih `download.prompt_for_download = true`
   (opsi "Ask where to save each file before downloading").
2. `Browser.setDownloadBehavior` di `configure_downloads()` baru dipasang
   **setelah** worker connect + login — terlambat untuk klik manual user dan
   untuk tab yang sudah terbuka.
3. Kemungkinan situs memakai File System Access API (`showSaveFilePicker`),
   yang memunculkan dialog simpan meski CDP download behavior sudah `allow`.

**Rencana perbaikan:**

1. **Tulis preferensi SEBELUM browser diluncurkan** — di
   `browser_manager.BrowserProfile.launch()`, sebelum `subprocess.Popen`,
   panggil helper baru `_ensure_download_prefs(profile_path, download_dir)`:
   - baca `Default/Preferences` (JSON) kalau ada, **merge** (jangan timpa),
   - set:
     ```json
     "download": {
       "default_directory": "<APP_DIR>/downloads/.browser_N",
       "prompt_for_download": false,
       "directory_upgrade": true,
       "bubble": { "enabled": false }
     },
     "savefile": { "default_directory": "<APP_DIR>/downloads/.browser_N" }
     ```
   - tulis atomik (`.tmp` + `os.replace`), aman kalau browser belum jalan.
   - Kalau browser **sudah** jalan (CDP aktif), jangan tulis file — cukup
     andalkan langkah 2.
2. **Pasang CDP download behavior sedini mungkin**: di worker, segera setelah
   `connect_over_cdp` (sebelum cek login), supaya tab yang sudah terbuka pun
   langsung diarahkan ke folder project.
3. **Tambah flag launch** di `config._launch_flags()`:
   `--disable-features=...` yang sudah ada tetap, tambah
   `--disable-prompt-on-repost` tidak perlu. Fokus ke prefs + CDP.
4. **Cegah dialog File System Access** (kalau ternyata situs memakainya):
   `page.add_init_script` yang menimpa `window.showSaveFilePicker` dan
   `window.showDirectoryPicker` supaya melempar `AbortError` — situs biasanya
   punya fallback ke download biasa. Uji dulu; hanya aktifkan kalau perlu.
5. **Jaring pengaman**: kalau setelah 8 detik tidak ada file baru sama sekali
   padahal menu MP3 sudah diklik, log peringatan
   `⚠️ Kemungkinan dialog Save muncul — periksa preferensi download` beserta
   dump `_menu_inventory(page)`, lalu coba tekan `Enter` sekali (beberapa
   dialog GTK bisa diselesaikan dengan Enter default "Save").
6. Semua file tetap dikumpulkan dulu di `downloads/.browser_N/` lalu dipindah
   ke `downloads/` oleh `download_song_row()` (perilaku sekarang, dipertahankan
   supaya dua browser tidak rebutan nama file).

**Kriteria selesai:**
- Klik MP3 → **tidak ada** dialog/file manager; file langsung muncul di
  `downloads/` dalam beberapa detik.
- Uji dengan profil Brave sementara di port tinggi (9433+), **bukan** browser
  milik user.

---

### TUGAS 2 — Setelah unduhan selesai, cek sisa prompt → lanjut sampai habis ✅

**Masalah:** bot harus terus bekerja sampai prompt di file habis. Setelah
semua lagu satu prompt selesai diunduh, bot harus **langsung mengambil prompt
berikutnya** (kalau masih ada) dan mengulangi siklus: isi prompt → generate →
unduh semua lagu. Baru berhenti kalau tidak ada prompt tersisa.

**Rencana perbaikan (`worker._worker`):**
1. Tambah helper di `prompts.py`:
   ```python
   def remaining_prompts(self) -> int:
       with self._lock:
           return sum(1 for i in self.items if i.status == "pending")
   ```
   (Sumber kebenaran = antrean di memori, karena file `.txt` sudah dipangkas
   saat prompt diambil.)
2. Di akhir siklus sukses (setelah `queue.complete(item)`):
   - kalau `queue.remaining_prompts() > 0` → jeda pendek
     `PAUSE_BETWEEN_PROMPTS` (1.5 dtk) lalu `continue` ke prompt berikutnya,
   - kalau `0` → `_status(profile, "done", "Selesai — prompt habis")` lalu
     `break` untuk browser itu saja.
3. Pastikan kondisi halaman bersih sebelum prompt baru: `close_menus(page)`,
   scroll ke kolom input, tunggu input siap. Jangan sampai sisa menu download
   menutupi kolom prompt.
4. Kalau sebuah prompt gagal unduh 3 putaran, **jangan** ambil prompt baru
   (perilaku sekarang: `queue.fail` + browser berhenti) — dipertahankan, karena
   lagu sudah terlanjur dibuat dan belum tersimpan.
5. Opsional: tambah `PAUSE_AFTER_PROMPT_DONE` di `config.py` kalau 1.5 detik
   ternyata terlalu cepat untuk situs.

**Kriteria selesai:**
- 3 prompt di file + 1 browser → 3 lagu/set lagu berturut-turut terunduh,
  status browser berakhir `Selesai — prompt habis`.
- File `.txt` kosong di akhir, `done.txt` berisi ketiganya.

---

### TUGAS 3 — Skema pembagian prompt ke browser (1 prompt = 1 browser) ✅

**Aturan yang diminta user:**

> Kalau 5 browser dan 4 prompt → prompt 1–4 jalan di Browser 1–4,
> Browser 5 tidak jalan.

**Desain:**
- Setiap prompt dikerjakan oleh **tepat satu browser**; satu browser
  mengerjakan banyak prompt secara berurutan sampai antrean habis.
- Urutan pengambilan dibuat deterministik: Browser 1 selalu mengambil prompt
  paling awal yang masih `pending`, lalu Browser 2, dst. (bukan siapa cepat
  dia dapat). Implementasi: `take()` dipanggil dengan prioritas indeks browser
  (antrean FCFS tetap, tapi worker Browser 1 diberi start lebih dulu dan
  `take` memilih pending dengan `index` terkecil — sudah begitu, cukup
  dipastikan tidak ada race yang membalik urutan).
- **Browser tanpa prompt tidak ikut bekerja**: setelah `take()` mengembalikan
  `None`, kartu menampilkan `Tidak ada prompt — idle` (bukan error), browser
  tetap terbuka/tersembunyi sesuai pilihan user.
- **Browser yang tidak dibuka user tidak dihitung**: `ready` = hanya browser
  yang berhasil di-launch (`_launch_all`). Jadi 5 slot, 3 dibuka, 4 prompt →
  Browser 1–3 jalan, prompt ke-4 menunggu (atau tidak terpakai).
- **Kegagalan**: kalau Browser N error/credits habis, prompt-nya dikembalikan
  ke antrean (`requeue`) dan boleh diambil browser lain yang masih idle —
  pengecualian sadar dari aturan 1:1, supaya prompt tidak hangus.
- Contoh tabel untuk dokumentasi UI/README:

  | Slot | Prompt | Status akhir |
  |---|---|---|
  | Browser 1 | #1, #4 | Selesai |
  | Browser 2 | #2 | Selesai |
  | Browser 3 | #3 | Selesai |
  | Browser 4 | — | Idle |
  | Browser 5 | — | Idle |

**Rencana perbaikan:**
1. `worker.py`: ganti pesan "Antrean kosong" → "Tidak ada prompt — idle";
   set state `idle` (bukan `done`) supaya UI membedakan.
2. `app.py`: warna/label kartu untuk state `idle`; ringkasan atas
   "📝 Sisa N prompt — M browser bekerja, K idle".
3. `prompts.py`: tambah `remaining_prompts()` dan `assignments()` untuk
   kebutuhan tampilan (opsional).

**Kriteria selesai:**
- 5 browser, 4 prompt → hanya 4 browser bekerja, 1 idle, semua 4 prompt
  terpakai tepat sekali, file `.txt` kosong.

---

## 🧪 Rencana uji (wajib, pakai profil sementara)

> Status 26 Nov 2026: unit test prefs download & simulasi antrean (5 browser /
> 4 prompt + requeue) **lulus**. Uji browser sungguhan di port 9433+ belum
> dijalankan — perlu izin membuka listener CDP lokal.

1. **Profil uji baru** di `/tmp/flowtest_*`, port 9433–9436,
   `--user-data-dir` terpisah — jangan sentuh browser user (port 9222).
2. Uji 1: unduh MP3 → cek tidak ada dialog, file ada di `downloads/`.
3. Uji 2: 3 prompt berurutan di 1 browser → 3 siklus, berhenti "prompt habis".
4. Uji 3: 4 prompt + 5 "browser" (mock/port berbeda) → 4 bekerja, 1 idle.
5. Uji 4: satu browser error → prompt-nya diambil browser idle.
6. Setelah selesai: hapus semua profil & skrip uji, pastikan tidak ada proses
   Brave sisa (`pgrep -af "user-data-dir=/tmp/flowtest"`).

---

## 📁 File yang akan disentuh

| File | Perubahan |
|---|---|
| `browser_manager.py` | tulis preferensi download sebelum launch; helper `_ensure_download_prefs` |
| `config.py` | path prefs/download per profil; flag tambahan bila perlu |
| `flow_actions.py` | pasang CDP download sedini mungkin; jaring pengaman dialog Save; init script FSA |
| `worker.py` | loop sampai prompt habis; state `idle`; urutan ambil prompt |
| `prompts.py` | `remaining_prompts()`, `assignments()` |
| `app.py` | label kartu `idle`, ringkasan sisa prompt |
| `README.md` | perbarui alur & tabel pembagian prompt |

---

## ⚠️ Catatan / risiko

- Dialog simpan **native** tidak bisa diklik lewat CDP. Satu-satunya cara
  adalah **mencegahnya muncul** (prefs + CDP + init script). Kalau masih
  muncul, perlu opsi CLI Brave/Chrome tambahan atau autohotkey/xdotool —
  didokumentasikan sebagai fallback terakhir.
- Jangan mengubah `Default/Preferences` saat browser sedang berjalan: Brave
  akan menimpa file itu saat exit.
- Prompt dihapus dari `.txt` saat diambil; kalau user ingin mengulang dari
  awal, harus menyiapkan file prompt baru (arsip ada di `state/run_*`).
- Port 9222 = browser asli user. **Jangan connect/inspect tanpa izin eksplisit.**

---

## 📝 Urutan pengerjaan besok

1. Tugas 1 (download tanpa dialog) — paling berdampak.
2. Tugas 2 (loop sampai prompt habis).
3. Tugas 3 (pembagian prompt & state idle + label UI).
4. Uji end-to-end dengan profil sementara, lalu bersih-bersih.
5. Perbarui README + `RENCANA_ESOK.md` (centang yang selesai).

---

## 🛡️ Fitur baru — pemulihan sesi otomatis (26 Nov 2026)

Permintaan user: kalau Google Flow membatasi sesi (gejala: **prompt tidak bisa
di-paste** / **Enter tidak mengirim**), bot harus mendeteksi, memberi tahu user,
lalu **klik tombol session baru di sidebar** dan lanjut bekerja sampai prompt
habis.

**Deteksi (`flow_actions.py`):**

| Tanda | Fungsi |
|---|---|
| Prompt gagal diisi 2× | `input_prompt()` di worker |
| Kolom prompt `disabled`/`readonly`/tidak editable | `detect_session_block()` |
| Pesan batas sesi (session limit/expired, start a new session, sesi berakhir, dll.) | `session_limit_message()` + `SESSION_LIMIT_SELECTOR` |
| Prompt masih tertinggal di kolom input & tidak ada lagu baru ≥150 dtk | `generate_and_wait_all_songs(prompt_text=...)` |

> Ambang 150 detik dipilih dari log: lagu pertama normalnya muncul 25–90 detik
> setelah Enter, sedangkan log “Generate belum terlihat” di detik ~26 masih
> normal. Jadi bot **tidak** membatalkan hanya karena belum ada reaksi di 20 dtk.

**Pemulihan:**

1. Status kartu `recovering` (ungu, “Pulihkan sesi...”) + log
   `🔁 Sesi macet (...) — mencoba session baru...`.
2. `start_new_session()` memindai **sidebar kiri** (x < 38% viewport) untuk
   label `New session / New chat / Start a new chat / Sesi baru / Obrolan baru`
   lewat JS (`_SIDEBAR_SCAN_JS`), menandai elemen terbaik, lalu mengkliknya
   (locator → JS click).
3. Verifikasi kolom prompt kembali editable (`_wait_for_editable_prompt_box`).
4. Fallback: muat ulang `WEBSITE_URL`, lalu verifikasi lagi.
5. `BotRunner._recover_session()` mencoba **2×** dengan jeda 45 detik. Kalau
   gagal, browser itu berhenti dengan status “Sesi macet — klik session baru
   manual”; prompt dikembalikan ke antrean **tanpa mengurangi jatah percobaan**
   (`PromptQueue.requeue(..., penalize=False)`) supaya tidak hangus, dan browser
   lain tetap bekerja.
6. Pengaman anti-loop: setelah **10× pemulihan berturut-turut tanpa satu lagu
   berhasil**, browser itu berhenti (`SESSION_RECOVERY_MAX_STREAK`) agar tidak
   berputar terus saat limit akun benar-benar panjang.

**Berkas yang disentuh:** `flow_actions.py`, `worker.py`, `prompts.py`,
`config.py`, `app.py`, `README.md`.

**Uji:** 62 assertion lulus (fake page + DOM tiruan via Node): deteksi,
pemilihan tombol sidebar, fallback reload, retry/cooldown, status UI, filter
sidebar untuk pesan limit, dan `requeue(penalize=False)`. **Uji browser
sungguhan belum dijalankan.**

---

## 🌐 Fitur baru — pilih Brave / Chrome (26 Nov 2026)

Permintaan user: aplikasi harus bisa dijalankan dengan **Brave atau Google
Chrome** — “jalanin di Brave pakai ini, di Chrome pakai itu”.

**Desain:**

1. `config.available_browsers()` mendeteksi family browser yang terpasang
   (Brave, Google Chrome, Chromium, Microsoft Edge) untuk Linux/macOS/Windows.
   Hanya binary yang ada yang dikembalikan; env `FLOW_BOT_BROWSER` tetap
   didukung sebagai opsi kustom.
2. Menu **Browser:** baru di bagian *1. Browser* (sebelah “Jumlah”). Pilihan
   disimpan ke `state/settings.json` (`{"browser": "chrome"}`) dan dipakai lagi
   saat aplikasi dibuka.
3. Urutan prioritas: pilihan user → `FLOW_BOT_BROWSER` → browser otomatis
   pertama. Urutan otomatis dipertahankan seperti versi lama (Linux:
   Brave→Chrome→Chromium→Edge; macOS/Windows: Chrome→Brave→Edge→Chromium).
4. **Folder profil tidak dicampur antar browser.** Profil lama
   `profiles/profile_N` tetap dipakai browser pemiliknya (dideteksi dari
   penanda `.flowbot_browser` atau `Local State` Brave). Browser lain memakai
   `profiles/<browser_id>/profile_N`, jadi login Brave tidak merusak login
   Chrome. Penanda ditulis saat launch.
5. Ganti browser ditolak selama bot berjalan atau masih ada jendela browser
   terbuka; user diminta menutup browser dulu. Setelah ganti, manager & kartu
   dibuat ulang dengan folder profil yang benar.
6. CLI: `python main.py --list-browsers` dan `--browser chrome` (id/path).

**Uji:** 63 assertion lulus (deteksi 2 browser, urutan, label/slug, cache
invalidasi, pilihan path kustom/relatif, penolakan browser tidak terpasang, id
tidak peka huruf besar, `settings.json` merge/rusak, penerapan pilihan tersimpan,
fallback saat browser tersimpan di-uninstall, folder khusus dipertahankan saat
folder lama hilang, penanda pemilik profil, deteksi Local State, fallback tanpa
browser). Smoke test GUI:
dropdown terisi “Brave”, header & subjudul kartu menampilkan browser, ganti
browser menulis settings dan memakai folder profil lama.
**Uji launch browser sungguhan (Brave→Chrome) belum dijalankan.**

---

## 🔧 Ringkasan perubahan kode (26 Nov 2026)

| File | Perubahan |
|---|---|
| `browser_manager.py` | `_ensure_download_prefs()` — merge `Default/Preferences` (default_directory, prompt_for_download=false, bubble off, savefile) ditulis atomik **sebelum** `Popen`; dipanggil di `launch()` hanya saat browser belum jalan; **hitung ulang folder profil saat launch + tulis penanda browser**; `BrowserManager.any_running()` |
| `flow_actions.py` | bug `picked` tertimpa setelah download langsung mulai diperbaiki; deadline `_click_keyword` ditegakkan di dalam loop (tidak lagi berputar >100 dtk); peringatan stall 8 dtk sebelum timeout download; **fitur pemulihan sesi**: `SESSION_LIMIT_SELECTOR`, `session_limit_message()`, `prompt_is_pending()`, `detect_session_block()`, `start_new_session()` + scan sidebar/reload; `generate_and_wait_all_songs(..., prompt_text=)` mendeteksi prompt tidak terkirim |
| `worker.py` | `configure_downloads()` dipindah tepat setelah `connect_over_cdp`; state `idle` saat tidak ada prompt tapi browser lain masih bekerja; `done — prompt habis` setelah selesai semua; `idle — tanpa prompt` untuk browser yang tak pernah dapat bagian; `close_menus()` sebelum prompt baru; **`_recover_session()`** + retry 2×/jeda 45 dtk, status `recovering`, prompt di-requeue tanpa penalti |
| `prompts.py` | `remaining_prompts()`, `has_work()`, `assignments()`; `requeue(..., penalize=False)` untuk kegagalan lingkungan (sesi/limit) |
| `app.py` | state `idle` (warna + label), ringkasan progres `• Bekerja N • Idle K`; state `recovering` (ungu, “Pulihkan sesi...”); **dropdown Browser + header/kartu menampilkan browser aktif**, ganti browser membuat ulang manager, kontrol dikunci saat bot berjalan |
| `config.py` | `IDLE_POLL_SECONDS`, `DOWNLOAD_STALL_WARN_SECONDS`, `GENERATE_STUCK_SECONDS`, `NEW_SESSION_READY_TIMEOUT`, `SESSION_RECOVERY_ATTEMPTS`, `SESSION_RECOVERY_COOLDOWN_SECONDS`, `SESSION_RECOVERY_MAX_STREAK`; **family Brave/Chrome/Chromium/Edge, `available_browsers()`, `set_browser_choice()`, `state/settings.json`, folder profil per browser + penanda `.flowbot_browser`** |
| `main.py` | opsi CLI `--browser` (id/path) dan `--list-browsers` |
| `README.md` | alur download tanpa dialog, tabel pembagian prompt, troubleshooting, bagian “Pemulihan sesi otomatis (anti-limit)”, bagian “Pilih browser (Brave/Chrome/Chromium/Edge)” |

**Sisa yang perlu dilakukan:** uji end-to-end browser sungguhan (unduh MP3 →
file di `downloads/`, tanpa dialog Save) dan uji pemulihan sesi (paksa kolom
prompt disabled / munculkan pesan limit, pastikan tombol session baru diklik dan
prompt yang sama diulang). Profil lama harus **ditutup lalu dibuka ulang** dari
kartu agar preferensi download baru terpakai.
