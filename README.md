# Pantau Titik Api & Asap

Dashboard publik pemantauan titik api kebakaran hutan dan lahan serta pergerakan asap di Indonesia. Data diperbarui otomatis tiap 2 jam lewat GitHub Actions dan diterbitkan di GitHub Pages. Tidak perlu server.

**Isi dashboard**

- Peta titik api VIIRS (NASA FIRMS) 24 jam / 48 jam / 7 hari: lingkaran merah tanpa garis tepi, makin pudar makin lama deteksinya.
- Filter tingkat keyakinan: *nominal & tinggi* atau *tinggi saja*. Deteksi berkeyakinan rendah selalu dibuang.
- Filter wilayah: seluruh Indonesia, 6 wilayah besar, atau 38 provinsi. Pilihan tersimpan di URL, jadi tautan bisa dibagikan, mis. `?wilayah=Kalimantan%20Tengah&periode=7d`.
- Lapisan asap:
  - animasi arah angin 925 hPa, sekarang/+12/+24 jam;
  - citra satelit harian VIIRS (asap terlihat langsung);
  - sebaran asap/aerosol dari indeks aerosol UV Sentinel-5P, produk yang sama dengan Copernicus Browser (perlu akun CDSE gratis, lihat langkah 3b);
  - estimasi PM2.5 per kota (kotak berwarna kategori ISPU).
- **Asap dan karbon monoksida (bola dunia):** dua bola dunia berdampingan, indeks aerosol UV dan karbon monoksida (CO) Sentinel-5P, masing-masing rata-rata 3 hari (hari itu dan dua hari sebelumnya). Garis waktu mulai 1 Agustus 2026; bola bisa diseret, diperbesar, dan diputar per hari. Butuh kunci CDSE yang sama dengan lapisan aerosol.
- **Mode Riwayat:** buka kondisi hari mana pun sejak 1 Januari 2026 (1, 7, atau 30 hari), geser slider atau tekan *Putar* untuk melihat perkembangannya hari demi hari. Lapisan aerosol Sentinel-5P ikut berganti sesuai tanggal. Tautannya bisa dibagikan, mis. `?tanggal=2026-09-15&durasi=7`.
- Peringkat provinsi dan kabupaten/kota dan tren 7 hari. Jumlah titik api per bulan atau per tahun dibaca dari mode Riwayat (pilih rentangnya).
- Persentase titik api di lahan gambut/konsesi beserta peringkat konsesinya, bila Anda menambahkan datanya.
- Unduh CSV sesuai filter, arsip harian permanen, panel metodologi.

---

## Setup (sekali saja, ±20 menit)

1. **Minta MAP_KEY FIRMS (gratis).** Daftar di <https://firms.modaps.eosdis.nasa.gov/api/map_key/>; kunci dikirim lewat email.
2. **Buat repositori GitHub** dan unggah seluruh isi folder ini. Repo publik lebih aman: menit Actions gratis tanpa batas.
3. **Simpan kunci sebagai secret.** Buka *Settings → Secrets and variables → Actions → New repository secret*. Isi Name `FIRMS_MAP_KEY` dan Value dengan kunci Anda.

   3b. **(Untuk lapisan asap Sentinel-5P) Buat OAuth client Copernicus, gratis:**
   1. Daftar akun di <https://dataspace.copernicus.eu/>.
   2. Buka <https://shapps.dataspace.copernicus.eu/dashboard/>, lalu *User Settings → OAuth clients → Create*. Beri nama, pilih masa berlaku "Never expire", lalu salin *Client ID* dan *Client secret*. Secret hanya tampil sekali.
   3. Simpan keduanya sebagai secret repo: `CDSE_CLIENT_ID` dan `CDSE_CLIENT_SECRET`.

   Tanpa langkah ini, dashboard tetap jalan; lapisan asap Sentinel-5P saja yang tidak muncul.

   Pipeline hanya meminta **satu gambar per pembaruan** (±12 permintaan per hari), jadi kuota gratis CDSE tidak bertambah berapa pun jumlah pengunjung.
4. **Aktifkan Pages.** Buka *Settings → Pages → Build and deployment → Source* dan pilih **GitHub Actions**.
5. **Isi arsip awal.** Buka tab *Actions*, pilih **Isi arsip awal (backfill)**, lalu klik *Run workflow*. Proses ini mengunduh batas kabupaten/kota, mengisi arsip harian sejak 1 Januari 2026 (untuk mode Riwayat), serta data tahun-tahun pembanding. Perkiraan 30–90 menit. Bila terhenti di tengah jalan, jalankan lagi: hari yang sudah tersimpan dilewati. Menjalankannya lagi juga melengkapi hari yang belum punya data salah satu satelit, misalnya S-NPP, bila FIRMS kini sudah menyediakannya. Hari yang tetap tanpa data S-NPP tidak dihitung sebagai nol di hitungan satu satelit dan disebut di log (baris "RINGKASAN").
6. **Terbitkan pertama kali.** Di tab *Actions*, pilih **Perbarui data** lalu klik *Run workflow*. Setelah selesai, alamat situs muncul di ringkasan job, biasanya `https://<akun>.github.io/<repo>/`. Selanjutnya workflow ini berjalan sendiri tiap 2 jam.

**Opsional: lokasi titik api tahun pembanding.** Supaya tanggal di tahun 2015, 2019, dan 2023 bisa dibuka di mode Riwayat (peta, peringkat, unduh CSV), jalankan workflow **Unduh lokasi titik api tahun lalu** di tab *Actions*. Butuh beberapa jam; hasilnya di-commit tiap satu tahun selesai, jadi bila terhenti tinggal dijalankan lagi. Setelah selesai, jalankan **Perbarui data**. Satelit mengikuti yang tersedia pada tahun itu (2015 hanya S-NPP; 2019 dan 2023 S-NPP + NOAA-20), dan citra aerosol hanya ada sejak 2018.

### Yang perlu dicek setelah run pertama

Bagian berikut tidak bisa diuji dari luar jaringan, jadi pastikan di situs yang sudah terbit:

- **Citra satelit.** Pilih *Peta dasar → Citra satelit*. Bila kosong untuk hari ini, coba tanggal kemarin, karena citra NRT baru tersedia beberapa jam setelah satelit lewat.
- **Asap & aerosol (Sentinel-5P).** Log workflow harus memuat baris `Sentinel-5P aerosol: tersimpan`. Bila muncul `Process API HTTP 401/403`, cek kembali `CDSE_CLIENT_ID` dan `CDSE_CLIENT_SECRET`. Area tanpa warna ungu berarti tidak ada aerosol penyerap (AI < 0,5), tertutup awan, atau di luar lintasan satelit.
- **Mode Riwayat.** Tombol *Riwayat* hanya muncul bila `data/archive/harian/indeks.json` ada. Hari yang tidak tercakup ketiga satelit diberi catatan di bawah slider (mis. NOAA-21 belum punya data untuk tanggal itu).
- **Batas kabupaten/kota.** Log workflow menampilkan jumlah kabupaten yang terunduh dari geoBoundaries. Bila Anda punya batas resmi BIG yang lebih baru, simpan sebagai `boundaries/kabupaten.geojson` dengan kolom `kab_name`.

---

## Cara kerja

```
GitHub Actions (tiap 2 jam)
  └─ pipeline/update.py
       ├─ FIRMS API (area): VIIRS S-NPP, NOAA-20, NOAA-21 (NRT, 8 hari terakhir)
       ├─ filter confidence nominal + high
       ├─ buang titik di negara tetangga & laut lepas, pasangkan ke 38 provinsi,
       │  kabupaten/kota, lapisan konteks
       ├─ deduplikasi lintas satelit (sel ~400 m per tanggal WIB)
       ├─ Open-Meteo: angin 925 hPa + PM2.5 kota (CAMS)
       ├─ Copernicus Data Space: 1 PNG indeks aerosol Sentinel-5P (24 jam terakhir)
       ├─ data/live/*.json      → dibaca index.html (tidak di-commit)
       └─ data/archive/         → arsip permanen (di-commit), termasuk
                                  1 file per hari untuk mode Riwayat
  └─ deploy index.html + data ke GitHub Pages

Sekali saja (atau untuk mengisi celah): pipeline/backfill_harian.py
  └─ FIRMS Standard Processing (SP) bila sudah tersedia, selain itu NRT
       → arsip harian, hitungan harian, citra aerosol harian
```

| File | Isi |
|---|---|
| `index.html` | Dashboard, satu file (Leaflet, leaflet-velocity, peta dasar OpenFreeMap via MapLibre; semua dari CDN) |
| `pipeline/config.py` | **Semua pengaturan**: satelit, filter, grid deduplikasi, wilayah, lapisan konteks, kota, tahun pembanding |
| `pipeline/update.py` | Pembaruan rutin |
| `pipeline/backfill_harian.py` | Isi arsip harian 3 satelit sejak `HISTORY_START` (default 1 Jan 2026) untuk mode Riwayat |
| `pipeline/backfill.py` | Isi hitungan tahun-tahun pembanding (satelit referensi saja) |
| `pipeline/fetch_boundaries.py` | Unduh batas kabupaten/kota (geoBoundaries) |
| `boundaries/provinsi.geojson` | Batas 38 provinsi: basis hitungan **dan** peta (CC BY 4.0). Bisa diganti file 38 provinsi lain asal ada kolom nama (`PROVINSI`, `name`, atau `nama`) |
| `boundaries/daratan_indonesia.geojson`, `negara_tetangga.geojson` | Penyaring Natural Earth: buang titik di negara tetangga dan laut lepas (dibuat dengan `pipeline/prepare_boundaries_ne.py`) |
| `context/` | Lapisan konteks: `taman_nasional.geojson` (54 taman nasional, WDPA 2019) dan opsional gambut/konsesi |
| `data/archive/daily_<tahun>.csv` | Titik api harian per provinsi, multi-satelit |
| `data/archive/ref_daily_<tahun>.csv` | Sama, satu satelit (batang 7 hari di mode Riwayat dengan filter 1 satelit) |
| `data/archive/raw/<tahun>/<tanggal>.csv.gz` | Semua deteksi nominal/high per hari, lengkap dengan provinsi, kabupaten, dan konteks |
| `data/archive/harian/<tahun>/<tanggal>.json` | Titik api unik per hari (sesudah deduplikasi) untuk mode Riwayat, ±60 byte per titik |
| `data/archive/harian/indeks.json` | Daftar tanggal yang tersedia beserta total dan jumlah satelitnya |
| `data/archive/s5p/<aer\|co>/<tahun>/<tanggal>.png` | Bola dunia: indeks aerosol UV (abu-abu 1–255 = −2…6) dan CO (1–255 = 0…254 ppb) per hari WIB, grid lintang-bujur 65°–170° BT, 45° LS–30° LU, 0,125°. Dashboard merata-ratakan 3 hari dan mewarnainya. Indeksnya di `data/archive/s5p/indeks.json` |
| `data/archive/aerosol_ai/<tahun>/<tanggal>.png` | Indeks aerosol Sentinel-5P per hari WIB. Gambar menyimpan **nilai** indeks (abu-abu 1–255 = 0,5–6,0), bukan warna; dashboard yang mewarnainya, jadi warna bisa diganti di `CONFIG.aerosolRamps` (`index.html`) tanpa mengunduh ulang |

Di kedua CSV harian, kolom `nh` berisi jumlah titik nominal+tinggi dan kolom `h` jumlah titik tinggi saja. Baris `__semua__` adalah total nasional.

### Pilihan metodologis (bisa diubah di `config.py`)

- **Satelit:** hanya VIIRS 375 m; MODIS 1 km tidak dipakai supaya resolusi seragam.
- **Deduplikasi:** deteksi di sel grid 0,0036° (~400 m) pada tanggal WIB yang sama dihitung satu titik. Tanpa ini, satu kebakaran bisa terhitung 2–3 kali karena ketiga satelit lewat berdekatan.
- **Perbandingan antartahun:** hanya VIIRS S-NPP (tersedia sejak 2012, jadi 2015 ikut), karena NOAA-20/21 belum ada di tahun-tahun awal. Karena itu angkanya lebih kecil dari total multi-satelit. Hari yang data S-NPP-nya kosong atau bolong (jauh di bawah satelit lain, `EMPTY_SAT_*`) tidak dihitung, jadi bulannya tampil "belum penuh"; aturan ini juga diterapkan ke arsip yang ditulis sebelum aturannya ada.
- **Finalisasi harian:** satu tanggal masuk arsip pukul 09.00 WIB keesokan harinya, memberi waktu untuk keterlambatan data NRT.
- **Hitungan periode:** angka besar di mode Riwayat menghitung tiap lokasi (sel ~400 m) sekali dalam periode yang dipilih; untuk jumlah per bulan, pilih rentang satu bulan.
- **Keyakinan tinggi:** titik dihitung berkeyakinan tinggi bila dinilai tinggi oleh satelit mana pun yang mendeteksinya. Dengan filter 1 satelit (S-NPP), yang dihitung adalah lokasi yang terdeteksi S-NPP dan berkeyakinan tinggi. Pada tahun dengan lebih dari satu satelit (sejak 2018), penilaian tinggi bisa berasal dari NOAA-20/21.
- **Peta 30 hari:** lebih dari 7 hari, titik digabung menjadi sel kepadatan 0,1° (±11 km) supaya peta tetap terbaca dan ringan.
- **Angin:** level 925 hPa (~750 m), setara angin 2.500 kaki di peta kabut asap ASMC.

---

## Menambah lapisan gambut / konsesi

Taruh file GeoJSON, GPKG, atau SHP (EPSG apa pun) di `context/`, lalu sesuaikan `CONTEXT_LAYERS` di `pipeline/config.py`:

```python
{
    "id": "sawit",
    "label": "Konsesi sawit (HGU)",
    "path": "context/konsesi_sawit.geojson",
    "name_field": "NAME_HGU",   # kolom nama → peringkat konsesi
    "group_field": "GRUP",      # kolom grup usaha (opsional)
    "color": "#d4a72c",
    "simplify_m": 100,
},
```

Lapisan yang filenya tidak ada dilewati tanpa error. Bila file tampilan hasil penyederhanaan masih lebih dari 15 MB, lapisan tetap dihitung tetapi tidak digambar di peta; besarkan `simplify_m`.

Hasilnya:
- popup titik api menyebut nama konsesinya;
- muncul kartu "Di mana titik api berada" berisi persentase dan tabel konsesi dengan titik api terbanyak;
- CSV unduhan dan arsip harian mendapat kolom tambahan.

Arsip lama tidak perlu diunduh ulang: setiap kali daftar lapisan atau isi filenya berubah, pembaruan berikutnya menandai ulang seluruh arsip harian dan deteksi mentah (`pipeline/retag_konteks.py`, beberapa detik sampai beberapa menit). Bisa juga dijalankan manual: `python -m pipeline.retag_konteks --paksa`.

### Taman nasional

`context/taman_nasional.geojson` dibuat dari World Database on Protected Areas (WDPA) Indonesia, Mei 2019:

```bash
python -m pipeline.prep_taman_nasional SHP_Kawasan_Lindung_Indonesia_May2019.zip
```

- Diambil semua kawasan berpenetapan taman nasional (54), kecuali taman nasional laut murni karena titik api hanya ada di darat. Wasur (Papua Selatan) ikut dimasukkan walau di WDPA 2019 hanya tercatat sebagai situs Ramsar, karena batasnya sama dengan taman nasionalnya.
- Dua catatan ganda dibuang: "Bantimurung" (berada di dalam Bantimurung Bulusaraung) dan "Gunung Boliyohuto" (di WDPA 2019 batasnya sama dengan Gunung Gede - Pangrango).
- Nama mengikuti WDPA 2019, jadi taman nasional hasil penggabungan atau penetapan setelah 2019 tercatat dengan nama lamanya (mis. Manupeu Tanadaru dan Laiwangi Wanggameti, kini TN Matalawa; Berbak dan Sembilang, kini TN Berbak Sembilang). Nama barunya dicatat di kolom `alias` (`ALIASES` di `pipeline/prep_taman_nasional.py`), tampil di bawah nama dan ikut dicari di dashboard. Untuk memperbarui, ganti file WDPA lalu jalankan perintah di atas.
- Kolom provinsi diisi provinsi yang memuat minimal 10% luas taman.
- Di kartu "Di mana titik api berada", daftar taman nasional bisa dicari (nama, nama baru, atau provinsi) dan dibuka seluruhnya, termasuk taman tanpa titik api.
- Jumlah titik api per taman untuk periode tertentu: buka mode Riwayat dengan rentang yang diinginkan; tabel taman nasional menghitung ulang untuk rentang itu.
- **Per tahun:** tombol "Per tahun" di kartu taman nasional menampilkan lokasi titik api per taman untuk tiap tahun berarsip (2015, 2019, 2023, tahun berjalan), tiap lokasi dihitung sekali dalam setahun. Angkanya ikut filter Satelit dan Keyakinan, tidak terpengaruh Periode dan Wilayah. Klik nama taman untuk akumulasi per bulan (1 Januari s.d. akhir tiap bulan). Cara hitungnya sama dengan mode Riwayat; datanya `data/archive/harian/tahunan_taman_nasional.json`, dibuat `pipeline/tahunan_konteks.py` setiap pembaruan (hanya tahun yang berubah dihitung ulang; semua: `python -m pipeline.tahunan_konteks --ulang`).
- `pipeline/riwayat_konteks.py` tetap membuat `data/archive/harian/riwayat_taman_nasional.json` (hitungan harian per taman) setiap pembaruan; dashboard tidak lagi menampilkannya, tetapi datanya dipakai untuk menandai hari yang data S-NPP-nya kosong/bolong.
- WDPA bebas dipakai untuk keperluan nonkomersial dengan menyebut sumbernya (UNEP-WCMC & IUCN, Protected Planet); atribusinya sudah tampil di bagian Sumber dashboard.

---

## Menjalankan di komputer sendiri

Kedua perintah *backfill* menjalankan 4 permintaan FIRMS serentak, menyimpan hasil tiap bulan, dan mencetak perkiraan sisa waktu. Satu permintaan (5 hari, seluruh Indonesia) bisa makan 15–120 detik di server FIRMS, jadi pengisian awal memang perlu waktu. Kalau terhenti atau ada bulan yang gagal, jalankan perintah yang sama lagi: bulan yang sudah ada dilewati. Tambahkan `--pekerja 6` untuk mempercepat bila koneksi kuat. Tahun pembanding juga boleh diisi bertahap (mis. `--years 2023 2024 2025` dulu); rata-rata di grafik hanya memakai bulan yang sudah lengkap.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export FIRMS_MAP_KEY=kunci_anda
export CDSE_CLIENT_ID=...  CDSE_CLIENT_SECRET=...   # opsional, untuk lapisan asap
python -m pipeline.fetch_boundaries      # opsional, sekali
python -m pipeline.backfill_harian      # arsip harian sejak 1 Jan 2026 (bisa dihentikan & dilanjutkan)
python -m pipeline.backfill --years 2015 2019 2023 2024 2025   # tahun pembanding
python -m pipeline.backfill_harian --tahun 2015 2019 2023 --tanpa-mentah   # opsional: lokasi titik api tahun pembanding
python -m pipeline.update

mkdir -p _site/data && cp index.html _site/ && cp -r data/live data/archive _site/data/
python -m http.server -d _site 8000      # buka http://localhost:8000
```

---

## Batas layanan & lisensi

- **NASA FIRMS:** 5.000 transaksi per 10 menit per MAP_KEY; satu kali pembaruan memakai sekitar 6 transaksi. Saat menyebarkan data, ikuti panduan sitasi dan disclaimer LANCE/FIRMS.
- **Open-Meteo:** API gratis untuk **penggunaan non-komersial**. Bila diterbitkan oleh media komersial, periksa ketentuan dan pertimbangkan paket berbayar (cukup tambahkan parameter `apikey` di `pipeline/weather.py`).
- **Peta dasar OpenFreeMap:** gratis tanpa kunci API; atribusi OpenFreeMap/OpenMapTiles/OpenStreetMap wajib tampil (sudah otomatis di pojok peta). Gaya bisa diganti di `CONFIG.basemap` (`index.html`), mis. `liberty` atau `bright`. Bila browser tidak mendukung WebGL, peta jatuh ke ubin OpenStreetMap biasa.
- **Ukuran arsip:** arsip harian dan citra aerosol bertambah puluhan MB per tahun. Masih jauh di bawah batas GitHub Pages (1 GB), tetapi bila kelak terlalu besar, pindahkan tahun-tahun lama ke rilis GitHub atau penyimpanan lain.
- **Copernicus Data Space:** isi mundur aerosol memakai satu permintaan per hari (±290 untuk Jan–Okt 2026), jauh di bawah kuota gratis 10.000 per bulan. Bola dunia Sentinel-5P menambah 2 permintaan per hari (aerosol & CO) plus 2 unduhan ulang produk OFFL; isi mundur sejak 1 Agustus berjalan otomatis 40 hari per variabel per pembaruan (±166 permintaan, selesai dalam 3 pembaruan). Mulai garis waktu diatur di `S5P_START` (`pipeline/config.py`); untuk mengisi sekaligus: `python -m pipeline.s5p`.
- **Ukuran arsip bola dunia:** ±0,4 MB per hari (dua variabel), ±13 MB per bulan.
- **GitHub Actions:** jadwal bisa terlambat beberapa menit saat GitHub sibuk. Workflow terjadwal dinonaktifkan GitHub bila repo tidak aktif 60 hari; commit arsip harian otomatis menjaga repo tetap aktif.
- **Batas wilayah:**
  - Hitungan dan peta sama-sama memakai 38 provinsi dari [denyherianto/indonesia-geojson-topojson-maps-with-38-provinces](https://github.com/denyherianto/indonesia-geojson-topojson-maps-with-38-provinces) (CC BY 4.0). Jadi titik yang tampil di dalam garis suatu provinsi di peta juga terhitung di provinsi itu.
  - Batas ini sangat disederhanakan. Sekitar 109 pulau kecil (±5.100 km², mis. Anambas) tidak tercakup, garis pantai bisa meleset beberapa km (hingga ±30 km di Riau dan Kalbar), dan batas darat menjorok ke negara tetangga.
  - Karena itu, sebelum dipasangkan ke provinsi, titik api disaring dengan [Natural Earth](https://www.naturalearthdata.com/) 10m (domain publik). Titik di negara tetangga dan di laut lebih dari 5 km dari pantai (`COAST_TOLERANCE_M`) dibuang.
  - Titik di pulau kecil atau pesisir yang tidak tercakup poligon dipasangkan ke provinsi terdekat.
  - Kabupaten/kota dari [geoBoundaries](https://www.geoboundaries.org/); lisensinya tercatat di `boundaries/kabupaten_sumber.json` setelah diunduh.
- **Sumber panas non-kebakaran:** smelter (mis. kawasan nikel di Sulawesi dan Maluku Utara), kilang, dan gunung api aktif ikut terdeteksi FIRMS sebagai titik api. Data NRT belum punya penanda jenisnya, jadi untuk liputan cek lokasi titik yang selalu menyala di tempat yang sama.
