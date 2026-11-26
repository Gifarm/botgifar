# 🎵 Flow Music Bot v3.0.0

Bot otomasi untuk membuat & mengunduh musik dari [Google Flow Music](https://www.flowmusic.app/)
memakai beberapa browser (multi-akun) sekaligus — **Brave, Google Chrome,
Chromium, atau Microsoft Edge** — **tanpa mengganggu pekerjaan lain di laptop**.
Browser bisa disembunyikan (minimize) dan bot tetap bekerja di belakang layar,
termasuk saat kamu sedang Zoom.

---

## Cara menjalankan

### Linux
```bash
./run_app.sh
```
atau
```bash
source venv/bin/activate
python app.py
```

### Windows
Klik dua kali `run_app.bat`.

---

## Alur pemakaian

1. **Pilih browser** (Brave/Chrome/Chromium/Edge) dan **jumlah browser**
   (1–10) di bagian *1. Browser*.
2. **Klik kartu Browser 1** → jendela browser pilihanmu terbuka di Flow Music.
3. **Login manual** dengan akun Google di jendela itu.
4. Klik tombol **Sembunyi** di kartu → browser masuk ke belakang layar dan
   **tetap tersembunyi** selama bot bekerja (walau opsi sembunyikan tidak
   dicentang). Semua klik & pengetikan dilakukan bot di jendela tersembunyi.
5. Ulangi untuk Browser 2, 3, dst. (setiap browser punya profil & akun sendiri).
6. **Pilih file `.txt`** berisi prompt di bagian *2. File Prompt*.
7. Klik **▶️ JALANKAN BOT**. Bot **hanya memakai browser yang sudah kamu
   buka** — browser yang masih tertutup tidak ikut dijalankan. Kalau belum ada
   satu pun yang terbuka, aplikasi akan mengingatkan.

Kamu bebas membuka Zoom, browser lain, atau aplikasi apa pun. Bot tidak akan
mencuri fokus jendela.

---

## Pilih browser (Brave / Chrome / Chromium / Edge)

Di bagian *1. Browser* ada dropdown **Browser:** yang hanya menampilkan browser
yang benar-benar terpasang di komputer. Pilihan disimpan ke
`state/settings.json`, jadi tidak perlu dipilih ulang saat aplikasi dibuka lagi.

- **Otomatis (default):** browser pertama yang terdeteksi. Urutan di Linux:
  Brave → Chrome → Chromium → Edge; di macOS/Windows: Chrome → Brave → Edge →
  Chromium.
- **Profil terpisah per browser:** folder lama `profiles/profile_N` tetap
  dipakai browser yang sudah login di situ (biasanya Brave). Kalau kamu
  memilih browser lain, bot memakai `profiles/<browser_id>/profile_N`
  (mis. `profiles/chrome/profile_1`) supaya cookie/login tidak tertukar.
  Karena folder-nya baru, **login sekali lagi** untuk browser tersebut.
- **Ganti browser saat ada jendela terbuka?** Tutup dulu semua jendela browser
  (atau tutup lalu buka lagi aplikasi). Ini mencegah dua browser memakai
  folder profil yang sama.
- **Lewat command line:**
  ```bash
  ./venv/bin/python main.py --list-browsers      # lihat yang terdeteksi
  ./venv/bin/python main.py --browser chrome     # pilih Chrome
  ```
- Environment variable `FLOW_BOT_BROWSER=/path/ke/binary` tetap didukung
  untuk instalasi tidak standar. Pilihan di menu Browser menang atas env;
  env dipakai kalau belum ada pilihan tersimpan.

---

## Format file prompt

Satu prompt = **satu blok paragraf**. Pisahkan antar prompt dengan **satu baris kosong**.

```
Create a completely original folk song about ... (prompt 1)

Create a completely original pop song about ... (prompt 2)

Create a completely original rock song about ... (prompt 3)
```

Setiap prompt yang **diambil** oleh browser langsung **dihapus dari file `.txt`**
sumber, jadi setiap browser mendapat promptnya sendiri dan tidak ada prompt yang
terpakai dua kali. Salinan lengkap tetap disimpan di folder `state/run_<tanggal>/`:

- `queue.json` — status & teks semua prompt
- `done.txt` — prompt yang berhasil
- `failed.txt` — prompt yang gagal

---

## Pembagian prompt ke browser

Setiap prompt dikerjakan oleh **tepat satu browser**. Satu browser boleh
mengerjakan beberapa prompt secara berurutan sampai antrean habis. Browser yang
tidak mendapat bagian menampilkan **Idle — tanpa prompt** dan tidak mengganggu
browser lain.

Contoh: 5 browser dibuka, 4 prompt di file.

| Slot | Prompt | Status akhir |
|---|---|---|
| Browser 1 | #1 | Selesai |
| Browser 2 | #2 | Selesai |
| Browser 3 | #3 | Selesai |
| Browser 4 | #4 | Selesai |
| Browser 5 | — | Idle |

Kalau sebuah browser gagal (mis. credits habis), prompt-nya **dikembalikan ke
antrean** dan boleh diambil browser lain yang masih idle, supaya prompt tidak
hangus.

---

## Hasil

- Semua file **MP3** masuk ke folder **`downloads/`** (kalau MP3 tidak ada di
  menu, bot memakai WAV dan memberi peringatan di log).
- Tidak ada dialog **Save / file manager** yang perlu diklik: folder & opsi
  download sudah diatur di profil browser sebelum browser dibuka, lalu
  ditegaskan lewat CDP. File sementara dikumpulkan per browser di
  `downloads/.browser_N/` agar dua browser tidak rebutan nama file, lalu
  dipindahkan ke `downloads/`.
- Kalau dua browser menghasilkan judul yang sama, file kedua otomatis
  diberi akhiran `_1`, `_2`, dst.
- Setiap prompt bisa menghasilkan 1–3 lagu, dan **semuanya** diunduh.

---

## Pemulihan sesi otomatis (anti-limit)

Google Flow bisa membatasi satu sesi/percakapan walau akun masih punya kuota.
Gejalanya: **prompt tidak bisa di-paste** atau **Enter tidak mengirim**.
Bot mendeteksinya dari beberapa tanda:

- prompt gagal diisi ke kolom input;
- kolom prompt `disabled`/`readonly` atau tidak bisa diedit;
- muncul pesan batas sesi ("session limit", "session has ended",
  "new session to continue", dll.);
- prompt masih tertinggal di kolom input dan tidak ada lagu baru sampai
  `GENERATE_STUCK_SECONDS` (150 detik).

Kalau salah satu terdeteksi:

1. Kartu browser berubah ungu **“Pulihkan sesi...”** dan log menampilkan
   `🔁 Sesi macet (...) — mencoba session baru...`.
2. Bot memindai **sidebar kiri** Flow Music, mencari tombol
   *New session / New chat / Sesi baru / Obrolan baru*, lalu mengkliknya.
3. Bot memastikan kolom prompt sudah bisa dipakai lagi, lalu **mengulangi
   prompt yang sama** dan lanjut sampai antrean habis.
4. Kalau tombol tidak ditemukan, bot memuat ulang halaman sebagai fallback.
5. Pemulihan dicoba **2 kali** (jeda 45 detik). Kalau tetap gagal, browser itu
   berhenti dengan status **“Macet — klik Cek”**; prompt-nya **ditahan untuk
   browser itu** (tidak dikembalikan ke antrean umum, tidak mengurangi jatah
   percobaan) dan browser lain tetap bekerja. Lihat *Tombol Cek* di bawah.
6. Kalau limit terus berulang sampai **10 kali berturut-turut tanpa satu lagu
   pun berhasil**, browser itu berhenti dengan status error/limit dan prompt
   terakhirnya ditahan — perbaiki lalu tekan **Cek**.

---

## Tombol Cek — lanjutkan browser yang error

Kalau satu browser bermasalah (mis. Tombol/step di halaman Flow kepencet
sehingga alurnya ngaco, tab ditutup, koneksi putus, atau **limit/credits
habis**), bot **tidak membuang prompt-nya**. Kartu browser menampilkan status
merah, misalnya:

- `Macet — klik Cek (#12)`
- `Limit — klik Cek (#12)`
- `Tertutup — klik Cek (#12)`
- `Perlu login — klik Cek`
- `Tidak bisa connect — klik Cek`

Langkahnya:

1. Buka browser itu (klik kartu / tombol **Buka** atau **Tampilkan**).
2. Perbaiki sendiri di jendela browser: login ulang, buat **session baru** manual,
   ganti akun, atau klik ulang ke halaman yang benar.
3. Klik tombol **Cek** di kartu browser itu.
4. Bot mengecek koneksi + login, lalu **mem-paste ulang prompt yang sama**
   (nomor prompt tertera di kartu) dan lanjut bekerja — tanpa menghentikan
   browser lain maupun me-restart seluruh bot.

Selama masih ada browser yang menunggu diperbaiki, bot tetap “berjalan”
(label progres menampilkan `Tertahan N (klik Cek)`). Menekan **STOP** tetap
menghentikan semuanya seperti biasa.

---

## Cara kerja "di belakang layar"

- Browser diluncurkan dengan profil terpisah dan port debugging masing-masing.
- Saat bot bekerja, jendela browser **diminimalkan lewat CDP**, bukan sekadar
  dipindah keluar layar (GNOME/Wayland mengabaikan posisi jendela).
- Flag khusus (`--disable-background-timer-throttling`,
  `--disable-backgrounding-occluded-windows`, dll.) membuat halaman tetap aktif
  walau diminimalkan.
- Bot **tidak pernah** memanggil `bring_to_front()` saat bekerja, jadi fokus
  tidak berpindah dari Zoom/editor kamu.
- Audio browser di-mute (`--mute-audio`) supaya tidak ada suara tak terduga.

---

## Troubleshooting

| Masalah | Solusi |
|---|---|
| "Browser Chrome/Chromium/Brave/Edge tidak ditemukan" | Install salah satunya, cek dengan `python main.py --list-browsers`, atau set `FLOW_BOT_BROWSER=/path/ke/browser` |
| Browser yang mau dipakai tidak muncul di dropdown | Browser belum terpasang/terdeteksi. Install dulu, lalu buka ulang aplikasi (dropdown diisi saat aplikasi dimulai) |
| Setelah ganti browser, kartu minta login lagi | Normal: browser lain memakai folder profil terpisah (`profiles/<browser_id>/`) supaya cookie tidak tertukar. Login sekali di browser baru |
| Dropdown Browser tidak bisa diubah | Tutup dulu semua jendela browser (atau tutup & buka aplikasi), karena profil tidak boleh dipakai dua browser sekaligus |
| Kartu browser "Perlu login" | Klik **Buka**, login manual, lalu **Sembunyi** |
| Bot berhenti "Limit tercapai" / "Credits habis" | Akun kehabisan kuota / kena limit. Ganti akun atau buat session baru, lalu klik **Cek** di kartu browser itu — prompt yang tadi dikerjakan otomatis dipaste ulang |
| Prompt tidak bisa di-paste / Enter tidak mengirim | Bot otomatis mencari & mengklik session baru (lihat log `🔁 Sesi macet`). Kalau kartu menjadi **Macet — klik Cek (#N)**, buka browser itu, klik session baru manual, lalu tekan **Cek** |
| Kartu merah "Macet / Error / Tertutup — klik Cek" | Perbaiki browser itu (login ulang, session baru, buka tab, dsb.), lalu tekan **Cek**. Prompt terakhirnya ditahan dan akan dipaste ulang |
| Kartu ungu "Pulihkan sesi..." | Bot sedang membuat session baru; tunggu sampai kembali “Siap” / “Session baru — lanjut” |
| Prompt gagal terus | Cek `state/run_*/failed.txt` dan `bot.log` |
| File mp3 justru masuk `~/Downloads` | Tutup browser itu, lalu buka lagi dari kartu — preferensi folder download ditulis saat browser diluncurkan |
| Muncul dialog Save saat download | Cek log `⚠️ Kemungkinan dialog Save muncul`; pastikan browser ditutup lalu dibuka ulang dari aplikasi supaya preferensi download diterapkan |
| Tampilan Flow Music berubah | Selector ada di bagian atas `flow_actions.py` |
| RAM penuh (5 browser) | Kurangi jumlah browser; tiap profil butuh ±300–500 MB |

Lokasi log: **`bot.log`** dan panel *Log Aktivitas* di aplikasi.

---

## Struktur singkat

| File | Fungsi |
|---|---|
| `app.py` | Aplikasi GUI + kartu browser |
| `worker.py` | Mesin bot (1 worker per browser) |
| `browser_manager.py` | Launch, minimize/show, cek login |
| `flow_actions.py` | Selector & aksi di halaman Flow Music |
| `prompts.py` | Antrean prompt yang aman |
| `config.py` | Semua pengaturan |
| `main.py` | Mode command-line (opsional) |

---

## Catatan

- Versi ini masih tahap pengembangan (v3.0.0), belum dibungkus jadi `.exe`.
- Selector situs Flow Music bisa berubah kapan saja. Kalau ada tombol yang
  tidak terdeteksi, perbaiki selector di `flow_actions.py`.
- Gunakan dengan bijak. Menjalankan banyak akun otomatis berisiko terkena
  pembatasan dari pihak Flow Music.
