"""Fungsi bersama: unduh data FIRMS, normalisasi, pasangkan ke wilayah,
dan deduplikasi lintas satelit."""
from __future__ import annotations

import io
import logging
import socket
import time
from datetime import date, timedelta

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
import urllib3.util.connection as _u3conn

from . import config as C

log = logging.getLogger("firms")

# Server GitHub Actions tidak punya jalur IPv6. Paksa IPv4 supaya koneksi tidak
# gagal dengan "Network is unreachable" ketika DNS mendahulukan alamat IPv6.
_u3conn.allowed_gai_family = lambda: socket.AF_INET


class FirmsError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Unduh
# ---------------------------------------------------------------------------
# FIRMS kadang butuh >2 menit sebelum mulai mengirim data area besar
TIMEOUT = (30, 300)  # (sambung, baca) detik


# Jeda sebelum mencoba lagi (detik): gangguan jaringan sesaat biasanya pulih dalam 1-2 menit
RETRY_WAIT = (10, 30, 60)


def _get_csv(url: str, retries: int = 4, header: str = "latitude") -> pd.DataFrame:
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=TIMEOUT)
            text = r.text.strip()
            # FIRMS mengembalikan teks error biasa (mis. "Invalid MAP_KEY.")
            if r.status_code != 200 or not text or header not in text.split("\n", 1)[0]:
                if text == "" and r.status_code == 200:
                    return pd.DataFrame()
                raise FirmsError(f"HTTP {r.status_code}: {text[:200]}")
            return pd.read_csv(io.StringIO(text))
        except (requests.RequestException, FirmsError) as e:
            last = e
            msg = str(e)
            if "Invalid" in msg or "MAP_KEY" in msg:
                break  # tidak ada gunanya mengulang
            if attempt + 1 < retries:
                log.warning("FIRMS belum merespons dengan benar (%s); mencoba lagi (%d/%d)",
                            type(e).__name__, attempt + 2, retries)
            if attempt + 1 < retries:
                time.sleep(RETRY_WAIT[min(attempt, len(RETRY_WAIT) - 1)])
    # Pesan error requests memuat URL lengkap; jangan sampai kunci ikut tercetak
    msg = str(last)
    key = _key_in(url)
    if key:
        msg = msg.replace(key, "***")
    raise FirmsError(f"{_redact(url)} -> {msg}")


def _key_in(url: str) -> str | None:
    parts = url.split("/")
    if "csv" in parts:
        i = parts.index("csv")
        if i + 1 < len(parts) and parts[i + 1]:
            return parts[i + 1]
    return None


def _redact(url: str) -> str:
    parts = url.split("/")
    if "csv" in parts:
        i = parts.index("csv")
        if i + 1 < len(parts):
            parts[i + 1] = "***"
    return "/".join(parts)


def fetch(map_key: str, source: str, day_range: int, start: date | None = None,
          mode: str = "country") -> pd.DataFrame:
    """Tarik CSV FIRMS. `start` None = data terbaru (hari ini mundur)."""
    if not 1 <= day_range <= 5:
        raise ValueError("FIRMS hanya menerima DAY_RANGE 1-5")
    if mode == "country":
        url = f"{C.FIRMS_BASE}/api/country/csv/{map_key}/{source}/{C.COUNTRY_CODE}/{day_range}"
    else:
        bbox = ",".join(str(v) for v in C.BBOX)
        url = f"{C.FIRMS_BASE}/api/area/csv/{map_key}/{source}/{bbox}/{day_range}"
    if start is not None:
        url += f"/{start.isoformat()}"
    span = f"{day_range} hari mulai {start}" if start else f"{day_range} hari terakhir"
    log.info("FIRMS %s (%s): mengunduh...", source, span)
    t = time.time()
    df = _get_csv(url)
    log.info("FIRMS %s (%s): %d baris, %.0f detik", source, span, len(df), time.time() - t)
    return df


def fetch_range(map_key: str, source: str, first: date, last: date,
                mode: str = "country", pause: float = 1.0) -> pd.DataFrame:
    """Tarik rentang tanggal (UTC, inklusif) dalam potongan 5 hari."""
    frames = []
    cur = first
    while cur <= last:
        n = min(5, (last - cur).days + 1)
        df = fetch(map_key, source, n, cur, mode)
        if len(df):
            frames.append(df)
        cur = cur + timedelta(days=n)
        time.sleep(pause)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def data_availability(map_key: str) -> pd.DataFrame:
    url = f"{C.FIRMS_BASE}/api/data_availability/csv/{map_key}/ALL"
    return _get_csv(url, header="data_id")


# ---------------------------------------------------------------------------
# Normalisasi
# ---------------------------------------------------------------------------
def confidence_class(series: pd.Series) -> pd.Series:
    """VIIRS: l/n/h (atau low/nominal/high). MODIS: 0-100 -> <30 l, <80 n."""
    s = series.astype(str).str.strip().str.lower()
    num = pd.to_numeric(s, errors="coerce")
    out = s.str[0]
    is_num = num.notna()
    out[is_num] = np.where(num[is_num] < 30, "l", np.where(num[is_num] < 80, "n", "h"))
    return out


def sat_base(source: str) -> str:
    """'VIIRS_SNPP_SP' / 'VIIRS_SNPP_NRT' -> 'VIIRS_SNPP'."""
    return source.rsplit("_", 1)[0] if source.endswith(("_NRT", "_SP")) else source


def sat_info(source: str) -> dict:
    return C.SATELLITES.get(sat_base(source), {"label": source, "bit": 0})


def normalize(df: pd.DataFrame, source: str) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df["source"] = source
    df["satbit"] = sat_info(source)["bit"]
    df["conf"] = confidence_class(df["confidence"])
    hhmm = df["acq_time"].astype(int).astype(str).str.zfill(4)
    df["t_utc"] = pd.to_datetime(df["acq_date"].astype(str) + " " + hhmm,
                                 format="%Y-%m-%d %H%M", utc=True)
    df["t_wib"] = df["t_utc"].dt.tz_convert(C.TIMEZONE)
    df["date_wib"] = df["t_wib"].dt.date
    if "frp" not in df:
        df["frp"] = np.nan
    if "daynight" not in df:
        df["daynight"] = ""
    keep = ["latitude", "longitude", "t_utc", "t_wib", "date_wib", "conf",
            "frp", "daynight", "source", "satbit"]
    return df[keep]


def filter_confidence(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    if df.empty:
        return df, 0
    mask = df["conf"].isin(C.CONFIDENCE_KEEP)
    return df[mask].reset_index(drop=True), int((~mask).sum())


# ---------------------------------------------------------------------------
# Wilayah
# ---------------------------------------------------------------------------
NAME_FIELDS = ("PROVINSI", "provinsi", "name", "nama", "NAME_1", "prov_name")


def load_provinces() -> gpd.GeoDataFrame:
    """Batas 38 provinsi = basis wilayah untuk hitungan DAN peta, supaya titik
    yang tampil di dalam garis provinsi di peta terhitung di provinsi itu."""
    g = gpd.read_file(C.BOUNDARY_DIR / "provinsi.geojson")
    g = g.to_crs(4326) if g.crs else g.set_crs(4326)
    g["geometry"] = g.geometry.make_valid()
    field = next((f for f in NAME_FIELDS if f in g.columns), None)
    if field is None:
        raise ValueError(f"provinsi.geojson butuh kolom nama provinsi, salah satu dari {NAME_FIELDS}")
    g = g.rename(columns={field: "prov_name"})
    g["prov_name"] = g["prov_name"].astype(str).str.strip()
    unknown = sorted(set(g["prov_name"]) - set(C.PROVINCE_TO_REGION))
    if unknown:
        log.warning("nama provinsi tidak dikenal di config.REGIONS: %s", unknown)
    g = g.dissolve("prov_name", as_index=False)  # satu baris per provinsi
    g = g.sort_values("prov_name").reset_index(drop=True)
    g["prov_i"] = np.arange(len(g))
    g["region"] = g["prov_name"].map(C.PROVINCE_TO_REGION).fillna("Lainnya")
    return g[["prov_i", "prov_name", "region", "geometry"]]


class Assigner:
    """Memasangkan titik api ke 38 provinsi.

    1. Titik di daratan negara tetangga dibuang (Natural Earth).
    2. Titik di laut lebih dari COAST_TOLERANCE_M dari daratan Indonesia dibuang
       (garis pantai Natural Earth, yang memuat pulau-pulau kecil).
    3. Sisanya dipasangkan ke poligon 38 provinsi: di dalam poligon, atau poligon
       terdekat untuk titik di pulau kecil / garis pantai yang tidak tercakup
       batas provinsi yang disederhanakan.
    File Natural Earth bersifat opsional; tanpa itu langkah 1-2 memakai batas
    38 provinsi saja (titik > COAST_TOLERANCE_M dari poligon dibuang)."""

    def __init__(self, provinces: gpd.GeoDataFrame):
        self.prov = provinces
        land_path = C.BOUNDARY_DIR / "daratan_indonesia.geojson"
        nb_path = C.BOUNDARY_DIR / "negara_tetangga.geojson"
        self.land = None
        if land_path.exists():
            land = gpd.read_file(land_path).to_crs(4326)
            land["geometry"] = land.geometry.make_valid()
            land = land.explode(index_parts=False).reset_index(drop=True)
            land["land_i"] = np.arange(len(land))
            self.land = land[["land_i", "geometry"]]
        else:
            log.warning("boundaries/daratan_indonesia.geojson tidak ada; "
                        "titik di pesisir/pulau kecil bisa terbuang")
        self.nb = None
        if nb_path.exists():
            nb = gpd.read_file(nb_path).to_crs(4326)
            nb["geometry"] = nb.geometry.make_valid()
            self.nb = nb[["geometry"]]

    def __call__(self, points: gpd.GeoDataFrame) -> pd.Series:
        out = pd.Series(-1, index=points.index, dtype=int)
        if points.empty:
            return out
        cand = points[["geometry"]]
        if self.nb is not None:
            j = gpd.sjoin(cand, self.nb, how="left", predicate="within")
            in_nb = set(j.index[j["index_right"].notna()])
            cand = cand[~cand.index.isin(in_nb)]
        if self.land is None:
            out.loc[cand.index] = _assign(cand, self.prov, "prov_i", C.COAST_TOLERANCE_M)
            return out
        on_land = _assign(cand, self.land, "land_i", C.COAST_TOLERANCE_M)
        cand = cand.loc[on_land.index[on_land >= 0]]
        out.loc[cand.index] = _assign(cand, self.prov, "prov_i", None)
        return out


def load_kabupaten(assign: Assigner) -> gpd.GeoDataFrame | None:
    path = C.BOUNDARY_DIR / "kabupaten.geojson"
    if not path.exists():
        log.info("boundaries/kabupaten.geojson tidak ada; level kabupaten dilewati")
        return None
    g = gpd.read_file(path).to_crs(4326)
    g["geometry"] = g.geometry.make_valid()
    if "kab_name" not in g:
        raise ValueError("kabupaten.geojson harus punya kolom 'kab_name' "
                         "(jalankan pipeline.fetch_boundaries)")
    # Provinsi induk ditentukan ulang (titik di dalam poligon), jadi kabupaten
    # di Papua otomatis masuk provinsi hasil pemekaran.
    pts = gpd.GeoDataFrame(geometry=g.geometry.representative_point(), crs=4326)
    g["prov_i"] = assign(pts).values
    g = g[g["prov_i"] >= 0]
    g = g.sort_values(["prov_i", "kab_name"]).reset_index(drop=True)
    g["kab_i"] = np.arange(len(g))
    return g[["kab_i", "kab_name", "prov_i", "geometry"]]


def _assign(points: gpd.GeoDataFrame, polys: gpd.GeoDataFrame, idx_col: str,
            max_m: float | None) -> pd.Series:
    """Indeks poligon untuk tiap titik: di dalam poligon, atau terdekat <= max_m
    (None = tanpa batas jarak)."""
    out = pd.Series(-1, index=points.index, dtype=int)
    if points.empty:
        return out
    j = gpd.sjoin(points[["geometry"]], polys[[idx_col, "geometry"]],
                  how="left", predicate="within")
    j = j[~j.index.duplicated(keep="first")]
    hit = j[idx_col].notna()
    out.loc[j.index[hit]] = j.loc[hit, idx_col].astype(int)
    miss = out.index[out < 0]
    if len(miss):
        jn = gpd.sjoin_nearest(points.loc[miss, ["geometry"]].to_crs(3395),
                               polys[[idx_col, "geometry"]].to_crs(3395),
                               how="left", max_distance=max_m)
        jn = jn[~jn.index.duplicated(keep="first")]
        ok = jn[idx_col].notna()
        out.loc[jn.index[ok]] = jn.loc[ok, idx_col].astype(int)
    return out


def load_context_layers() -> list[dict]:
    layers = []
    for spec in C.CONTEXT_LAYERS:
        path = C.ROOT / spec["path"]
        if not path.exists():
            continue
        g = gpd.read_file(path)
        g = g.to_crs(4326) if g.crs else g.set_crs(4326)
        g["geometry"] = g.geometry.make_valid()
        g = g[~g.geometry.is_empty].reset_index(drop=True)
        nf, gf = spec.get("name_field"), spec.get("group_field")
        if nf and nf not in g:
            log.warning("%s: kolom nama '%s' tidak ada, peringkat dimatikan", spec["id"], nf)
            nf = None
        if gf and gf not in g:
            gf = None
        g["ctx_name"] = g[nf].astype(str).str.strip() if nf else ""
        g["ctx_group"] = g[gf].astype(str).str.strip() if gf else ""
        g["poly_i"] = np.arange(len(g))
        layers.append({**spec, "gdf": g, "ranked": bool(nf)})
        log.info("lapisan konteks %s: %d poligon", spec["id"], len(g))
    return layers


def attach_regions(df: pd.DataFrame, assign: Assigner, kabupaten, ctx_layers,
                   drop_outside: bool = True) -> tuple[pd.DataFrame, int]:
    """Tambah kolom prov_i, kab_i, ctx (bitmask) dan ctxname_<id> (nama)."""
    if df.empty:
        df = df.copy()
        for col in ("prov_i", "kab_i", "ctx"):
            df[col] = pd.Series(dtype=int)
        for layer in ctx_layers:
            df[f"ctxname_{layer['id']}"] = pd.Series(dtype=str)
            df[f"ctxgroup_{layer['id']}"] = pd.Series(dtype=str)
        return df, 0
    pts = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.longitude, df.latitude),
                           crs=4326)
    pts["prov_i"] = assign(pts)
    outside = int((pts["prov_i"] < 0).sum())
    if drop_outside:
        pts = pts[pts["prov_i"] >= 0].copy()
    if kabupaten is not None:
        pts["kab_i"] = _assign(pts, kabupaten, "kab_i", 10_000)
    else:
        pts["kab_i"] = -1
    pts["ctx"] = 0
    for bit, layer in enumerate(ctx_layers):
        g = layer["gdf"]
        j = gpd.sjoin(pts[["geometry"]], g[["poly_i", "ctx_name", "ctx_group", "geometry"]],
                      how="left", predicate="within")
        j = j[~j.index.duplicated(keep="first")]
        inside = j["poly_i"].notna()
        pts.loc[j.index[inside], "ctx"] |= (1 << bit)
        pts[f"ctxname_{layer['id']}"] = ""
        pts[f"ctxgroup_{layer['id']}"] = ""
        pts.loc[j.index[inside], f"ctxname_{layer['id']}"] = j.loc[inside, "ctx_name"].values
        pts.loc[j.index[inside], f"ctxgroup_{layer['id']}"] = j.loc[inside, "ctx_group"].values
    return pd.DataFrame(pts.drop(columns="geometry")), outside


# ---------------------------------------------------------------------------
# Deduplikasi
# ---------------------------------------------------------------------------
def cell_key(lat: pd.Series, lon: pd.Series) -> pd.Series:
    i = np.floor(lat.to_numpy() / C.GRID_DEG).astype(np.int64)
    j = np.floor(lon.to_numpy() / C.GRID_DEG).astype(np.int64)
    return pd.Series((i + 5000) * 100000 + j, index=lat.index)


def dedup_daily(df: pd.DataFrame) -> pd.DataFrame:
    """Satu baris per (tanggal WIB, sel grid). Lokasi & atribut wilayah
    diambil dari deteksi dengan FRP tertinggi di sel itu."""
    if df.empty:
        return df.assign(cell=pd.Series(dtype="int64"))
    d = df.copy()
    d["cell"] = cell_key(d["latitude"], d["longitude"])
    d["is_h"] = (d["conf"] == "h").astype(int)
    d["hbit"] = d["satbit"] * d["is_h"]  # bit satelit yang menilai deteksinya high
    d["frp_sort"] = d["frp"].fillna(-1)
    keys = ["date_wib", "cell"]
    d = d.sort_values(keys + ["frp_sort"], ascending=[True, True, False])
    rep = d.drop_duplicates(keys, keep="first").set_index(keys)
    g = d.groupby(keys)
    agg = pd.DataFrame({
        "h": g["is_h"].max(),
        "sats": g["satbit"].agg(lambda s: int(np.bitwise_or.reduce(s.to_numpy()))),
        # satelit mana saja yang punya deteksi high di sel ini (h = salah satunya ada).
        # Dipakai dashboard supaya "1 satelit + tinggi saja" berarti high menurut
        # satelit itu sendiri, sama seperti hitungan ref_daily.
        "hsats": g["hbit"].agg(lambda s: int(np.bitwise_or.reduce(s.to_numpy()))),
        "n_det": g.size(),
        "t_first": g["t_wib"].min(),
        "t_last": g["t_wib"].max(),
        "frp": g["frp"].max(),
    })
    drop = ["is_h", "hbit", "frp_sort", "conf", "satbit", "source", "t_utc", "t_wib", "frp"]
    rep = rep.drop(columns=[c for c in drop if c in rep])
    out = agg.join(rep).reset_index()
    return out
