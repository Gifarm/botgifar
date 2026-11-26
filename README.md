# 🎵 Flow Music Bot v2.0

Bot otomasi untuk membuat & mengunduh musik dari [Google Flow Music](https://www.flowmusic.app/)
memakai beberapa browser (multi-akun) sekaligus — **tanpa mengganggu pekerjaan lain di laptop**.
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

1. **Pilih jumlah browser** (1–10) di bagian *1. Browser*.
2. **Klik kartu Browser 1** → jendela Brave/Chrome terbuka di Flow Music.
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
| "Browser Chrome/Chromium/Brave tidak ditemukan" | Install Brave/Chrome, atau set `FLOW_BOT_BROWSER=/path/ke/browser` |
| Kartu browser "Perlu login" | Klik **Buka**, login manual, lalu **Sembunyi** |
| Bot berhenti "Credits habis" | Akun tersebut kehabisan kuota; browser itu berhenti, browser lain lanjut |
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

- Versi ini masih tahap pengembangan (v2.0), belum dibungkus jadi `.exe`.
- Selector situs Flow Music bisa berubah kapan saja. Kalau ada tombol yang
  tidak terdeteksi, perbaiki selector di `flow_actions.py`.
- Gunakan dengan bijak. Menjalankan banyak akun otomatis berisiko terkena
  pembatasan dari pihak Flow Music.
