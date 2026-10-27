"""Siapkan lapisan konteks taman nasional dari WDPA (World Database on
Protected Areas) Indonesia.

    python -m pipeline.prep_taman_nasional path/ke/SHP_Kawasan_Lindung_Indonesia_May2019.zip

Hasil: context/taman_nasional.geojson (kolom: nama, provinsi, luas_ha, wdpaid,
tahun_penetapan). Yang diambil:
- semua poligon berpenetapan Taman Nasional / National Park, kecuali taman
  nasional laut murni (MARINE = 2), karena titik api hanya ada di darat;
- Wasur (Papua Selatan), yang di WDPA 2019 hanya tercatat sebagai situs Ramsar
  dengan batas yang sama dengan taman nasionalnya.
Catatan ganda dibuang: poligon yang >= 80% luasnya berada di dalam taman
nasional lain (mis. "Bantimurung" di dalam "Bantimurung Bulusaraung", dan
"Gunung Boliyohuto" yang di WDPA 2019 ternyata berbatas sama dengan Gunung
Gede - Pangrango). Yang dipertahankan: berpenetapan "National Park".
Geometri disederhanakan ~20 m (jauh di bawah ukuran piksel VIIRS 375 m) supaya
berkas tetap ringan untuk pembaruan tiap jam.
"""
from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd

from . import config as C

OUT = C.ROOT / "context" / "taman_nasional.geojson"
SIMPLIFY_DEG = 0.0002  # ~20 m
MIN_SHARE = 0.10       # provinsi dicantumkan bila memuat >= 10% luas taman
# Pulau yang tidak tercakup batas provinsi dan provinsi terdekatnya keliru
PROVINCE_OVERRIDE = {"Kepulauan Seribu": "DKI Jakarta"}
# Nama di WDPA 2019 yang kini berbeda (penggabungan/ejaan resmi). Ditampilkan di
# bawah nama dan ikut dicari di dashboard.
ALIASES = {
    "Manupeu Tanadaru": "kini bagian TN Matalawa",
    "Laiwangi Wanggameti": "kini bagian TN Matalawa",
    "Berbak": "kini bagian TN Berbak Sembilang",
    "Sembilang": "kini bagian TN Berbak Sembilang",
    "Betung Kerihun": "kini bagian TN Betung Kerihun dan Danau Sentarum",
    "Danau Sentarum": "kini bagian TN Betung Kerihun dan Danau Sentarum",
    "Gunung Tambora Selatan": "kini TN Tambora",
    "Take Bone Rate": "nama resmi: Taka Bonerate",
    "Teluk Cendrawasih": "nama resmi: Teluk Cenderawasih",
    "Kepulauan Wakatobi": "nama resmi: Wakatobi",
    "Ganda Dewata": "nama resmi: Gandang Dewata",
}


def load(src: Path) -> gpd.GeoDataFrame:
    if src.suffix.lower() == ".zip":
        tmp = Path(tempfile.mkdtemp())
        with zipfile.ZipFile(src) as z:
            z.extractall(tmp)
        shp = next(tmp.rglob("*polygons.shp"))
    else:
        shp = src
    g = gpd.read_file(shp)
    return g.to_crs(4326) if g.crs else g.set_crs(4326)


def select(g: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    desig = g["DESIG"].fillna("").str.strip()
    eng = g["DESIG_ENG"].fillna("").str.strip()
    is_tn = desig.str.startswith("Taman Nasional") | eng.str.contains("National Park", case=False)
    is_wasur = g["NAME"].str.contains("Wasur", case=False, na=False)
    t = g[(is_tn | is_wasur) & (g["MARINE"].astype(str) != "2")].copy().reset_index(drop=True)
    # Buang catatan ganda (lihat keterangan di atas)
    ea = t.to_crs(6933)
    pref = t["DESIG_ENG"].fillna("").str.contains("National Park", case=False)
    drop = set()
    for i in range(len(ea)):
        for j in range(len(ea)):
            if i == j or i in drop or j in drop:
                continue
            gi, gj = ea.geometry.iloc[i], ea.geometry.iloc[j]
            if not gi.intersects(gj):
                continue
            if gi.intersection(gj).area / gi.area >= 0.8 and (pref.iloc[j] or not pref.iloc[i]):
                drop.add(i)
    for i in sorted(drop):
        print(f"dibuang (ganda): {t['NAME'].iloc[i]} ({t['DESIG'].iloc[i]} / {t['DESIG_ENG'].iloc[i]})")
    t = t.drop(index=sorted(drop))
    t["nama"] = (t["NAME"].str.replace(r"\s*National Park$", "", regex=True)
                 .str.replace(r"\s+-\s+", " - ", regex=True).str.strip())
    # satu baris per nama (gabungkan bila ada beberapa poligon)
    t = t.dissolve(by="nama", aggfunc={"WDPAID": "first", "STATUS_YR": "max", "GIS_AREA": "sum"}).reset_index()
    t["luas_ha"] = (t["GIS_AREA"] * 100).round().astype(int)
    t["wdpaid"] = t["WDPAID"].astype("int64")
    t["tahun_penetapan"] = t["STATUS_YR"].astype(int)
    return t[["nama", "luas_ha", "wdpaid", "tahun_penetapan", "geometry"]]


def add_provinces(t: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    prov = gpd.read_file(C.BOUNDARY_DIR / "provinsi.geojson")
    prov = prov.to_crs(4326) if prov.crs else prov.set_crs(4326)
    field = next(f for f in ("prov_name", "PROVINSI", "Propinsi", "provinsi", "name", "NAME_1") if f in prov.columns)
    ea = 6933  # luas sama (equal-area)
    inter = gpd.overlay(t[["nama", "geometry"]].to_crs(ea), prov[[field, "geometry"]].to_crs(ea),
                        how="intersection", keep_geom_type=True)
    inter["a"] = inter.area
    out = {}
    for nama, grp in inter.groupby("nama"):
        s = grp.groupby(field)["a"].sum().sort_values(ascending=False)
        keep = [str(p).strip() for p, a in s.items() if a / s.sum() >= MIN_SHARE] or [str(s.index[0]).strip()]
        out[nama] = ", ".join(keep)
    t["provinsi"] = t["nama"].map(out).fillna("")
    # Taman kepulauan yang tidak tercakup poligon provinsi: provinsi terdekat
    miss = t["provinsi"] == ""
    if miss.any():
        pts = t.loc[miss, ["nama", "geometry"]].copy().to_crs(ea)
        pts["geometry"] = pts.representative_point()
        near = gpd.sjoin_nearest(pts, prov[[field, "geometry"]].to_crs(ea), how="left")
        near = near[~near.index.duplicated()]
        t.loc[miss, "provinsi"] = near[field].astype(str).str.strip().values
    for nama, p in PROVINCE_OVERRIDE.items():
        t.loc[t["nama"] == nama, "provinsi"] = p
    return t


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        sys.exit(__doc__)
    t = add_provinces(select(load(Path(argv[0]))))
    t["geometry"] = t.geometry.make_valid().simplify(SIMPLIFY_DEG, preserve_topology=True)
    t = t[~t.geometry.is_empty].sort_values("nama").reset_index(drop=True)
    t["alias"] = t["nama"].map(ALIASES).fillna("")
    t = t[["nama", "provinsi", "alias", "luas_ha", "wdpaid", "tahun_penetapan", "geometry"]]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    t["geometry"] = t.geometry.set_precision(1e-5)
    OUT.write_text(t.to_json(drop_id=True), encoding="utf-8")
    print(f"{len(t)} taman nasional -> {OUT.relative_to(C.ROOT)} ({OUT.stat().st_size / 1e6:.1f} MB)")
    print(pd.DataFrame(t.drop(columns="geometry")).to_string())


if __name__ == "__main__":
    main()
