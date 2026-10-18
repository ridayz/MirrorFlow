# MirrorFlow

Mirror layar Android ke PC lewat browser, dengan kontrol penuh dari mouse + keyboard.
Tampilannya dibuat seperti **Xcode / Hopper di macOS** — jendela tool developer:

```text
┌ menu bar ────────────────────────────────────────────────────────────┐
│  MirrorFlow   Berkas   Device   I/O   Tampilan   Bantuan                   │
├ toolbar ─────────────────────────────────────────────────────────────┤
│ ● ● ●   ▶ ■  ⟳     Demo Device (720x1280)      ⌂ ⇱ ▤  ⏻ ⟳  ⌐ ⌐      │
│                    720 × 1280 · 30 fps · h264                        │
├ navigator ──┬ kanvas (device iPhone) ─────────────┬ inspector ────────┤
│ Perangkat   │            ╭──────────╮             │ ▾ Sesi            │
│ ▸ Demo      │            │  layar   │             │   Device   [ … ]  │
│             │            ╰──────────╯             │   Resolusi [ … ]  │
├ konsol ─────┴─────────────────────────────────────┤ ▾ Kualitas        │
│ 12:04:31 Menyiarkan Demo Device (720x1280)        │ ▾ Tampilan        │
└───────────────────────────────────────────────────┴───────────────────┘
```

Menu barnya benar-benar berfungsi (MirrorFlow, Berkas, Device, I/O, Tampilan, Bantuan)
dan isinya persis kebutuhan mirror — di antaranya menu **Device** dengan Home, Back,
Recent apps, Menu, Kunci layar, Putar layar, Volume naik/turun, Panel notifikasi, dan
Pengaturan cepat, serta menu **I/O** dengan sakelar Layar tetap menyala dan
Tampilkan sentuhan (berlaku saat sesi dimulai), sama seperti menu Simulator di Xcode.

Semua jalan tanpa `pip install`. Backendnya hanya pustaka standar Python; rendering
video dilakukan browser memakai WebCodecs.

```text
HP Android ──USB/ADB──> scrcpy-server ──socket──> Python ──WebSocket──> Chrome ──WebCodecs──> canvas
   ▲                                                                              │
   └──────────────────── kanal kontrol (touch/key/scroll) ◄────────────────────────┘
```

## Coba dulu tanpa HP (mode demo)

```bat
run-demo.bat
```

Server menyala di `http://127.0.0.1:27190/`, lalu klik **Coba mode demo**.
Semua jalur asli dilewati kecuali device-nya: framing scrcpy, WebSocket, WebCodecs,
dan kanvas — jadi kalau di sini jalan, masalah device ada di sisi ADB, bukan di UI.

## Pakai HP asli

1. Di HP: **Setelan → Opsi developer → USB debugging** aktif, lalu colok kabel.
2. Terima dialog "Izinkan USB debugging" di layar HP.
3. Klik dua kali:

```bat
run.bat
```

4. Device muncul di daftar, klik untuk mulai mirror.

Kontrol yang tersedia: klik = tap, tahan = long press, seret = swipe, scroll roda =
scroll, tombol Back/Home/Recents, panel notifikasi, pengaturan cepat, putar layar,
nyalakan/matikan layar, tombol fisik (F untuk layar penuh).

Traffic light di kiri atas juga berfungsi, seperti tombol jendela macOS:

| Tombol | Fungsi |
|---|---|
| 🔴 merah | hentikan sesi mirror |
| 🟡 kuning | jeda / lanjut stream |
| 🟢 hijau | layar penuh |

Tombol lain di toolbar: **▶** jalankan sesi, **■** hentikan, **⟳** muat ulang daftar
device, dan dua tombol paling kanan untuk menyembunyikan navigator / inspector.
Konsol di bawah bisa dilipat lewat tombol panah di kanan atasnya, dan inspector punya
sakelar **Bingkai iPhone** dan **Animasi UI** (animasi panel/console/menu/kanvas
bisa dimatikan terpisah; default nyala). Jendela dan panel meluncur dengan transisi
halus, dan kanvas ikut beranimasi saat ukurannya berubah.

Ikon kamera di toolbar menyimpan frame terakhir sebagai PNG
(`mirrorflow-<serial>-<waktu>.png`).

## Syarat

| Kebutuhan | Wajib? | Keterangan |
|---|---|---|
| Windows + Python 3.10+ | ya | backend + server UI |
| Google Chrome / Edge 94+ | ya | butuh `VideoDecoder` (WebCodecs) |
| ADB (platform-tools) | untuk HP asli | `winget install Google.PlatformTools` |
| FFmpeg | tidak | hanya untuk membuat ulang aset demo |
| `vendor/scrcpy-server-v5.0.1.jar` | sudah disertakan | di-push otomatis ke HP |

Kalau `adb` sudah terpasang tapi tidak ketemu, set `ADB=C:\path\ke\adb.exe`
(atau tambahkan ke PATH). Lokasi `adb` yang dipakai selalu tampil di kiri bawah.

## Opsi baris perintah

```bat
python -m mirrorflow [--host 127.0.0.1] [--port 27190] [--demo] [--no-browser]
```

| Opsi | Arti |
|---|---|
| `--port` | port UI; kalau terpakai, server otomatis pindah ke port bebas berikutnya |
| `--demo` | tambahkan device palsu `demo` tanpa HP |
| `--no-browser` | jangan buka browser otomatis |
| `--host` | default `127.0.0.1` (UI tidak terbuka ke LAN) |

Pengaturan kualitas (resolusi maksimum, batas FPS, bitrate, layar tetap menyala,
tampilkan sentuhan) diubah dari sidebar; nilainya dipakai saat sesi dimulai.

## Cara kerjanya

Python **tidak** men-decode video. Tugasnya cuma pipa + kanal kontrol, persis pilihan
desain scrcpy sendiri: decoding di klien jauh lebih murah daripada copy frame bolak-balik.

1. `session.py` mengikuti urutan scrcpy: push jar → `adb forward tcp:<port>
   localabstract:scrcpy_<scid>` → jalankan `app_process` di device → buka socket video
   (baca dummy byte) → buka socket kontrol → baca 64 byte nama device.
2. `sniff.py` memecah aliran byte jadi paket utuh: codec id, session packet (ukuran
   layar), config packet (SPS/PPS), dan frame. Formatnya big-endian, dengan bit
   `CONFIG = 1<<62` dan `KEY_FRAME = 1<<61` di dalam field pts.
3. `hub.py` menyalurkan paket ke banyak klien sekaligus. Pelanggan baru menerima
   *preroll* (codec + ukuran layar + SPS/PPS), lalu **frame biasa ditahan sampai
   keyframe berikutnya lewat**. Tanpa ini, klien yang menyambung di tengah GOP
   menerima delta frame tanpa referensi dan decoder langsung mati.
4. `session.py` juga meminta keyframe baru (`RESET_VIDEO`) begitu ada klien menyambung,
   supaya jeda hitamnya sesingkat mungkin.
5. `server.py` mengirim byte apa adanya lewat WebSocket (`/ws`); `stream.js` di browser
   yang mem-parsing ulang framing itu dan menyerahkan H.264 Annex-B ke `VideoDecoder`.
6. `control.js` menerjemahkan input jadi pesan kontrol scrcpy (touch, key, scroll).

WebCodecs dikonfigurasi **tanpa** `description`, jadi Chrome memperlakukan input sebagai
Annex-B. SPS/PPS dari config packet selalu dititipkan di depan keyframe, tidak pernah
dikirim sebagai chunk sendiri — chunk berisi SPS/PPS saja bukan keyframe dan ditolak
decoder.

## Struktur berkas

```text
mirrorflow/
  config.py     lokasi adb/ffmpeg, path aset, nilai default
  adb.py        pembungkus adb: devices, push, forward, spawn server
  control.py    encoder pesan kontrol scrcpy (touch/key/scroll/clipboard/…)
  session.py    siklus hidup satu device + request keyframe
  hub.py        pub/sub ke banyak klien + penyaringan sampai keyframe
  sniff.py      parser framing stream scrcpy
  mock.py       sesi demo: memutar berkas H.264 lokal dengan framing yang sama
  ws.py         WebSocket minimal (RFC 6455) di atas socket Python
  server.py     HTTP statis + endpoint /api + /ws
  static/       index.html, style.css, app.js, stream.js, control.js
tools/
  check_stream.py  klien uji: attach, hitung paket/keyframe, cek total byte
  make_demo.py     bikin ulang assets/demo.h264 + demo.json dengan ffmpeg
vendor/
  scrcpy-server-v5.0.1.jar   server resmi scrcpy, tidak diubah
```

## Kalau ada masalah

**Device tidak muncul** — jalankan `adb devices`; kalau tertulis `unauthorized`, terima
dialog izin di layar HP. Kalau `offline`, cabut-colok kabel.

**Layar hitam / macet di "Menghubungkan"** — nyalakan layar HP (saat layar mati, encoder
kadang berhenti). Lihat baris terakhir di status bar; jalankan server dengan
`MIRRORFLOW_DEBUG=1` untuk melihat jejak koneksi, attach, dan pump di stderr.

**Pesan "decoder: Decoding error"** — decoder browser menolak stream. Coba mulai ulang
sesi (klik device lagi); kode sekarang meminta keyframe baru saat attach.

**Canvas hijau/berkotak** — stream rusak di tengah jalan, biasanya karena jaringan
WebSocket tersendat. Turunkan bitrate di sidebar.

**Video jalan tapi fps rendah** — turunkan resolusi maksimum atau bitrate; jendela
browser yang diminimalkan juga membatasi `requestAnimationFrame`.

## Catatan jujur tentang status

- Jalur video diuji lewat mode demo: 720×1280, 30 fps, ~6,7 Mbps, tanpa frame jatuh,
  dan hasilnya terlihat benar di kanvas.
- Bingkai iPhone dan Dynamic Island murni hiasan tampilan, dan **bisa dimatikan**:
  inspector → **Tampilan → Bingkai iPhone**, atau menu **Tampilan → Bingkai device
  iPhone**. Waktu dimatikan, layar tampil apa adanya — tanpa radius sudut dan tanpa
  Dynamic Island — jadi tidak ada satu pixel pun yang tertutup atau terpotong.
- Animasi UI (panel meluncur, konsol melipat, bagian inspector, menu mekar turun,
  kanvas ikut berubah ukuran) bisa dimatikan lewat inspector → **Tampilan →
  Animasi UI** (atau menu **Tampilan → Animasi UI**). Default nyala; tersimpan di
  peramban. Catatan: Windows "Show animations in Windows" yang dimatikan **tidak**
  lagi mematikan animasi di aplikasi ini — kontrolnya ada di dalam aplikasi.
- Belum ada HP Android yang tersambung selama pengembangan, jadi jalur device asli
  (push jar, forward, kontrol) baru diverifikasi terhadap sumber scrcpy v5.0.1, bukan
  terhadap device sungguhan. Kalau ada yang meleset, kemungkinan besar di sini.
- Arah scroll sudah dicocokkan dengan `mouse_sdk.c` scrcpy (`vscroll = -y`), tapi
  sensasinya belum pernah dicoba di device.
- Clipboard, audio, dan rekaman belum diimplementasikan.
