"""Siapkan lapisan taman nasional dan zonasinya dari data Zonasi Kawasan
Taman Nasional (Ditjen KSDAE, batas administrasi Juli 2021).

    python -m pipeline.prep_zonasi_tn path/ke/Zonasi_Kawasan_Taman_Nasional.shp

Hasil:
- context/zonasi_tn.geojson     satu poligon per taman & zona
                                (kolom: zona, kawasan, kode, luas_ha)
- context/taman_nasional.geojson batas tiap taman = gabungan zonanya
                                (kolom: nama, provinsi, alias, luas_ha)

Batas taman dibuat dari gabungan poligon zona yang SAMA, jadi titik api yang
berada di dalam taman selalu jatuh di salah satu zonanya dan jumlah per zona
pas dengan total taman.

- Kolom yang dipakai: nkws (nama kawasan, "TN ..."), remark (zona), kodezona.
- Poligon tanpa nama kawasan dan zona ("-", catatan "Belum Ada SK Penetapan")
  dicatat sebagai zona "Belum ditetapkan" dan dimasukkan ke taman terdekat.
- Geometri disederhanakan ~20 m dengan menjaga batas antarzona tetap
  berimpit (shapely.coverage_simplify), jauh di bawah piksel VIIRS 375 m.
- Provinsi tiap taman dihitung dari batas provinsi dashboard (provinsi yang
  memuat >= 10% luas taman), supaya nama provinsinya sama dengan di dashboard.
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely

from . import config as C
from .prep_taman_nasional import add_provinces

OUT_ZONES = C.ROOT / "context" / "zonasi_tn.geojson"
OUT_PARKS = C.ROOT / "context" / "taman_nasional.geojson"
SIMPLIFY_DEG = 0.0002  # ~20 m
UNSET = "Belum ditetapkan"
# Urutan tampil (dari yang paling dilindungi)
ZONE_ORDER = ["Inti", "Rimba", "Perlindungan Bahari", "Rehabilitasi", "Tradisional",
              "Pemanfaatan", "Religi, Budaya dan Sejarah", "Khusus", UNSET]
# Nama lama/penggabungan pengelolaan; ditampilkan di bawah nama dan ikut dicari
ALIASES = {
    "Manupeu Tanah Daru": "kini bagian TN Matalawa",
    "Laiwangi Wanggameti": "kini bagian TN Matalawa",
    "Berbak": "kini bagian TN Berbak Sembilang",
    "Sembilang": "kini bagian TN Berbak Sembilang",
    "Betung Kerihun": "kini bagian TN Betung Kerihun dan Danau Sentarum",
    "Danau Sentarum": "kini bagian TN Betung Kerihun dan Danau Sentarum",
    "Teluk Cendrawasih": "nama resmi: Teluk Cenderawasih",
}


def load(src: Path) -> gpd.GeoDataFrame:
    g = gpd.read_file(src)
    g = g.to_crs(4326) if g.crs else g.set_crs(4326)
    g["geometry"] = shapely.force_2d(g.geometry.values)
    g["geometry"] = g.geometry.make_valid()
    g = g[~g.geometry.is_empty].copy()
    g["kawasan"] = g["nkws"].fillna("-").astype(str).str.strip().str.replace(r"^TN\s+", "", regex=True)
    g["zona"] = g["remark"].fillna("-").astype(str).str.strip()
    g["kode"] = g["kodezona"].fillna(0).astype(int)
    g.loc[g["zona"].isin(["-", ""]), ["zona", "kode"]] = [UNSET, 0]
    return g[["kawasan", "zona", "kode", "geometry"]]


def assign_unnamed(g: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Poligon tanpa nama kawasan -> taman terdekat (dalam proyeksi luas sama)."""
    miss = g["kawasan"].isin(["-", ""])
    if not miss.any():
        return g
    ea = 6933
    named = g[~miss].dissolve("kawasan").reset_index()[["kawasan", "geometry"]].to_crs(ea)
    near = gpd.sjoin_nearest(g[miss].drop(columns="kawasan").to_crs(ea), named, how="left",
                             distance_col="jarak_m")
    near = near[~near.index.duplicated()]
    for i, r in near.iterrows():
        print(f"poligon tanpa nama ({r.geometry.area / 1e4:.0f} ha) -> TN {r['kawasan']} (jarak {r['jarak_m']:.0f} m)")
    g.loc[miss, "kawasan"] = near["kawasan"].values
    return g


def simplify_park(zones: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Sederhanakan zona-zona satu taman dengan batas bersama tetap berimpit."""
    geoms = zones.geometry.values
    try:
        if shapely.coverage_is_valid(geoms):
            out = shapely.coverage_simplify(geoms, SIMPLIFY_DEG)
        else:
            out = shapely.simplify(geoms, SIMPLIFY_DEG, preserve_topology=True)
    except Exception:  # noqa: BLE001 - jatuh ke penyederhanaan biasa
        out = shapely.simplify(geoms, SIMPLIFY_DEG, preserve_topology=True)
    zones = zones.copy()
    zones["geometry"] = shapely.make_valid(out)
    return zones


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        sys.exit(__doc__)
    g = assign_unnamed(load(Path(argv[0])))
    # satu poligon per taman & zona
    z = g.dissolve(by=["kawasan", "zona"], aggfunc={"kode": "first"}).reset_index()
    z = pd.concat([simplify_park(part) for _, part in z.groupby("kawasan")], ignore_index=True)
    z = gpd.GeoDataFrame(z, geometry="geometry", crs=4326)
    z["geometry"] = z.geometry.set_precision(1e-5)
    z = z[~z.geometry.is_empty]
    z["luas_ha"] = (z.to_crs(6933).area / 1e4).round().astype(int)
    z["urut"] = z["zona"].map({n: i for i, n in enumerate(ZONE_ORDER)}).fillna(len(ZONE_ORDER))
    z = z.sort_values(["kawasan", "urut"]).drop(columns="urut").reset_index(drop=True)
    unknown = sorted(set(z["zona"]) - set(ZONE_ORDER))
    if unknown:
        print("zona tak dikenal (tetap dipakai):", unknown)
    # taman = gabungan zona yang sudah disederhanakan (batasnya identik)
    p = z.dissolve(by="kawasan").reset_index()[["kawasan", "geometry"]].rename(columns={"kawasan": "nama"})
    p["geometry"] = p.geometry.make_valid().set_precision(1e-5)
    p = add_provinces(p)
    p["luas_ha"] = (p.to_crs(6933).area / 1e4).round().astype(int)
    p["alias"] = p["nama"].map(ALIASES).fillna("")
    p = p[["nama", "provinsi", "alias", "luas_ha", "geometry"]].sort_values("nama").reset_index(drop=True)
    z = z[["zona", "kawasan", "kode", "luas_ha", "geometry"]]
    OUT_ZONES.parent.mkdir(parents=True, exist_ok=True)
    OUT_ZONES.write_text(z.to_json(drop_id=True), encoding="utf-8")
    OUT_PARKS.write_text(p.to_json(drop_id=True), encoding="utf-8")
    print(f"{len(z)} zona -> {OUT_ZONES.relative_to(C.ROOT)} ({OUT_ZONES.stat().st_size / 1e6:.1f} MB)")
    print(f"{len(p)} taman nasional -> {OUT_PARKS.relative_to(C.ROOT)} ({OUT_PARKS.stat().st_size / 1e6:.1f} MB)")
    print(pd.DataFrame(p.drop(columns="geometry")).to_string())
    print(z.groupby("zona")["luas_ha"].sum().sort_values(ascending=False).to_string())


if __name__ == "__main__":
    main()
