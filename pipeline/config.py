"""Konfigurasi pipeline Pantau Titik Api & Asap.

Semua pilihan metodologis dikumpulkan di sini supaya mudah diubah dan
mudah dijelaskan di panel metodologi dashboard.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOUNDARY_DIR = ROOT / "boundaries"
CONTEXT_DIR = ROOT / "context"
ARCHIVE_DIR = ROOT / "data" / "archive"   # di-commit ke repo (arsip permanen)
OUT_DIR = ROOT / "data" / "live"          # dibuat ulang tiap run, tidak di-commit

TIMEZONE = "Asia/Jakarta"  # semua tanggal harian memakai WIB

# ---------------------------------------------------------------------------
# NASA FIRMS
# ---------------------------------------------------------------------------
FIRMS_BASE = "https://firms.modaps.eosdis.nasa.gov"
# "area" = kotak batas di bawah lalu disaring dengan batas negara (default).
# "country" = endpoint per negara FIRMS (saat diuji Okt 2026 ditolak FIRMS).
FIRMS_MODE = "area"
COUNTRY_CODE = "IDN"
BBOX = (94.0, -11.5, 141.5, 6.5)  # W, S, E, N

# Sumber near real-time yang ditarik. Hanya VIIRS (piksel 375 m);
# MODIS (1 km) sengaja tidak dipakai supaya resolusi seragam.
SATELLITES = {
    "VIIRS_SNPP": {"label": "VIIRS S-NPP", "bit": 1},
    "VIIRS_NOAA20": {"label": "VIIRS NOAA-20", "bit": 2},
    "VIIRS_NOAA21": {"label": "VIIRS NOAA-21", "bit": 4},
}
# Untuk pembaruan rutin dipakai versi near real-time (NRT)
SOURCES = {f"{k}_NRT": v for k, v in SATELLITES.items()}

# Arsip harian (mode Riwayat di dashboard) dimulai dari tanggal ini
HISTORY_START = "2026-01-01"

# Hanya confidence nominal (n) dan high (h). Low (l) dibuang.
CONFIDENCE_KEEP = ("n", "h")

# Deduplikasi: deteksi dari beberapa satelit di sel grid yang sama pada
# tanggal (WIB) yang sama dihitung sebagai satu titik api.
# 0,0036 derajat ~ 400 m di khatulistiwa, sedikit di atas ukuran piksel VIIRS.
GRID_DEG = 0.0036

# Jendela data live. FIRMS membatasi 1-5 hari per permintaan, jadi
# pipeline menarik 2 permintaan per satelit untuk menutup 8 hari (UTC).
LIVE_DAYS = 8

# Satu hari (WIB) dianggap final dan masuk arsip setelah jam ini pada
# hari berikutnya, memberi waktu untuk keterlambatan data NRT (~3 jam).
FINALIZE_HOUR_NEXT_DAY = 9

# Titik di laut lebih jauh dari ini dari garis pantai (Natural Earth) dibuang,
# mis. anjungan migas lepas pantai. Titik di daratan negara tetangga selalu dibuang.
COAST_TOLERANCE_M = 5_000

# ---------------------------------------------------------------------------
# Perbandingan antartahun
# ---------------------------------------------------------------------------
# Satu satelit saja supaya sebanding antartahun. S-NPP tersedia sejak 2012
# sehingga mencakup 2015. Ganti ke "VIIRS_NOAA20" bila S-NPP bermasalah.
REF_SATELLITE = "VIIRS_SNPP"
REF_LABEL = "VIIRS S-NPP"
BASELINE_YEARS_DEFAULT = [2015, 2019, 2023, 2024, 2025]

# Satelit dianggap TIDAK punya data (lengkap) pada suatu hari bila:
#  - nol titik padahal satelit lain mendeteksi >= EMPTY_SAT_MIN_OTHERS titik, atau
#  - titiknya < EMPTY_SAT_RATIO x satelit lain yang terbanyak, dan satelit itu
#    mendeteksi >= EMPTY_SAT_RATIO_MIN titik (mis. S-NPP 15 vs NOAA-20 4.000).
# Ketiga satelit VIIRS sama dan melintas berselang <1 jam, jadi selisih sebesar
# itu berarti data bolong (gangguan satelit), bukan beda kondisi api. Hari seperti
# itu tidak dihitung di perbandingan antartahun.
EMPTY_SAT_MIN_OTHERS = 20
EMPTY_SAT_RATIO = 0.25
EMPTY_SAT_RATIO_MIN = 100

# ---------------------------------------------------------------------------
# Wilayah besar (dipakai juga di skrip GEE tabel hotspot per wilayah)
# ---------------------------------------------------------------------------
REGIONS = {
    "Sumatera": [
        "Aceh", "Sumatera Utara", "Sumatera Barat", "Riau", "Jambi",
        "Sumatera Selatan", "Bengkulu", "Lampung",
        "Kepulauan Bangka Belitung", "Kepulauan Riau",
    ],
    "Jawa": [
        "DKI Jakarta", "Jawa Barat", "Jawa Tengah",
        "Daerah Istimewa Yogyakarta", "Jawa Timur", "Banten",
    ],
    "Bali & Nusa Tenggara": ["Bali", "Nusa Tenggara Barat", "Nusa Tenggara Timur"],
    "Kalimantan": [
        "Kalimantan Barat", "Kalimantan Tengah", "Kalimantan Selatan",
        "Kalimantan Timur", "Kalimantan Utara",
    ],
    "Sulawesi": [
        "Sulawesi Utara", "Sulawesi Tengah", "Sulawesi Selatan",
        "Sulawesi Tenggara", "Gorontalo", "Sulawesi Barat",
    ],
    "Maluku & Papua": [
        "Maluku", "Maluku Utara", "Papua", "Papua Barat", "Papua Selatan",
        "Papua Tengah", "Papua Pegunungan", "Papua Barat Daya",
    ],
}
PROVINCE_TO_REGION = {p: r for r, ps in REGIONS.items() for p in ps}

# ---------------------------------------------------------------------------
# Lapisan konteks opsional (taruh file di folder context/)
# ---------------------------------------------------------------------------
# - path       : file GeoJSON / GPKG / SHP (EPSG apa pun, akan diubah ke 4326)
# - name_field : kolom nama untuk peringkat (None = hanya hitung persentase)
# - group_field: kolom induk/grup usaha (opsional, ikut ditampilkan)
# - simplify_m : toleransi penyederhanaan untuk file tampilan di peta
# - group_label: judul kolom grup di tabel peringkat (default "Grup")
# - source, source_url: atribusi data, ditampilkan di bagian Sumber dashboard
# Lapisan yang filenya tidak ada akan dilewati tanpa error.
# Bila daftar lapisan atau isi filenya berubah, pembaruan berikutnya menandai
# ulang seluruh arsip harian (pipeline.retag_konteks), tanpa unduh ulang FIRMS.
CONTEXT_LAYERS = [
    {
        "id": "gambut",
        "label": "Kesatuan Hidrologis Gambut",
        "path": "context/gambut.geojson",
        "name_field": None,
        "group_field": None,
        "color": "#8c6d46",
        "simplify_m": 300,
    },
    {
        "id": "sawit",
        "label": "Konsesi sawit (HGU)",
        "path": "context/konsesi_sawit.geojson",
        "name_field": "nama",
        "group_field": "grup",
        "color": "#d4a72c",
        "simplify_m": 100,
    },
    {
        "id": "hti",
        "label": "Konsesi kayu (PBPH-HT)",
        "path": "context/konsesi_hti.geojson",
        "name_field": "nama",
        "group_field": "grup",
        "color": "#4f9d69",
        "simplify_m": 100,
    },
    {
        # Dibuat dari WDPA Indonesia: python -m pipeline.prep_taman_nasional <zip WDPA>
        "id": "taman_nasional",
        "label": "Taman nasional",
        "path": "context/taman_nasional.geojson",
        "name_field": "nama",
        "group_field": "provinsi",
        "group_label": "Provinsi",
        "alias_field": "alias",  # nama lain/nama baru: ditampilkan & ikut dicari
        "color": "#22a38f",
        "simplify_m": 120,
        "source": "World Database on Protected Areas (UNEP-WCMC & IUCN), Mei 2019",
        "source_url": "https://www.protectedplanet.net/country/IDN",
    },
]

# ---------------------------------------------------------------------------
# Angin & kualitas udara (Open-Meteo)
# ---------------------------------------------------------------------------
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_AQ = "https://air-quality-api.open-meteo.com/v1/air-quality"

# Angin di 925 hPa (~750 m), setara ketinggian angin 2.500 kaki yang
# dipakai ASMC di peta kabut asap regional.
WIND_LEVEL = "925hPa"
WIND_GRID_DEG = 1.5
WIND_BBOX = (93.0, -12.0, 142.5, 9.0)
WIND_FRAMES_H = [0, 12, 24]  # jam ke depan dari jam terdekat

# Indeks aerosol Sentinel-5P (Copernicus Data Space): satu PNG per pembaruan
AEROSOL_BBOX = (93.0, -12.0, 142.5, 9.0)  # W, S, E, N
AEROSOL_WIDTH_PX = 1600
# Citra aerosol menyimpan NILAI indeks, bukan warna: piksel 1..255 = AI min..max
# (linear), 0 = transparan (AI < min, awan tebal, atau di luar lintasan).
# Warnanya diatur di index.html (AER_RAMPS), jadi bisa diganti tanpa unduh ulang.
AEROSOL_ENCODING = {"type": "ai_u8", "min": 0.5, "max": 6.0}
AEROSOL_ARCHIVE_DIRNAME = "aerosol_ai"
# Indeks aerosol Sentinel-5P baru tersedia sejak akhir April 2018
AEROSOL_START = "2018-05-01"
AEROSOL_CATCHUP_PER_RUN = 50  # maks. citra arsip yang belum ada, diunduh per pembaruan

# Bola dunia Sentinel-5P: aerosol UV & karbon monoksida, ditampilkan sebagai
# rata-rata 3 hari. Arsip menyimpan satu PNG per hari per variabel (grid
# lintang-bujur); dashboard yang merata-ratakan 3 hari dan mewarnainya.
# Piksel 1..255 = nilai min..max (linear), 0 = tidak ada data.
S5P_BBOX = (65.0, -45.0, 170.0, 30.0)  # W, S, E, N; India timur s.d. Pasifik barat, Australia
S5P_RES = 0.125  # derajat per piksel (~14 km); 840 x 600 piksel
S5P_START = "2026-08-01"  # tanggal pertama di garis waktu
S5P_CATCHUP_PER_RUN = 40  # citra yang belum ada, diunduh per variabel per pembaruan
S5P_DIRNAME = "s5p"
# Kolom CO (mol/m2) -> perkiraan rasio campuran rata-rata kolom (ppb), memakai
# kolom udara kering pada tekanan permukaan standar: 101325 / (9,80665 x 0,028964)
# = 356.770 mol/m2. Di dataran tinggi angka ini sedikit lebih rendah dari aslinya.
S5P_AIR_COLUMN = 356770.0
S5P_VARS = {
    "aer": {"label": "Aerosol", "band": "AER_AI_340_380", "factor": 1.0,
            "min": -2.0, "max": 6.0, "unit": "indeks aerosol UV"},
    "co": {"label": "Karbon monoksida", "band": "CO", "factor": 1e9 / S5P_AIR_COLUMN,
           "min": 0.0, "max": 254.0, "unit": "ppb"},
}

# Kategori mengikuti ambang ISPU PM2.5 (rata-rata 24 jam)
ISPU_PM25 = [
    (15.5, "Baik"),
    (55.4, "Sedang"),
    (150.4, "Tidak Sehat"),
    (250.4, "Sangat Tidak Sehat"),
    (float("inf"), "Berbahaya"),
]

CITIES = [
    ("Medan", 3.595, 98.672), ("Pekanbaru", 0.507, 101.448),
    ("Dumai", 1.665, 101.447), ("Padang", -0.950, 100.354),
    ("Jambi", -1.610, 103.613), ("Palembang", -2.976, 104.775),
    ("Bengkulu", -3.800, 102.266), ("Bandar Lampung", -5.429, 105.262),
    ("Pangkalpinang", -2.129, 106.110), ("Jakarta", -6.200, 106.817),
    ("Surabaya", -7.257, 112.752), ("Pontianak", -0.026, 109.343),
    ("Ketapang", -1.853, 109.976), ("Palangka Raya", -2.208, 113.917),
    ("Sampit", -2.531, 112.950), ("Banjarmasin", -3.317, 114.590),
    ("Samarinda", -0.502, 117.154), ("Balikpapan", -1.265, 116.831),
    ("Makassar", -5.148, 119.432), ("Palu", -0.899, 119.870),
    ("Kupang", -10.177, 123.607), ("Merauke", -8.493, 140.402),
    ("Jayapura", -2.533, 140.718), ("Singapura", 1.352, 103.820),
    ("Kuching", 1.553, 110.359), ("Kuala Lumpur", 3.139, 101.687),
]
