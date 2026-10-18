"""Membuat boundaries/daratan_indonesia.geojson dan boundaries/negara_tetangga.geojson
dari Natural Earth 10m (domain publik). File hasilnya sudah disertakan di repo;
skrip ini hanya untuk dokumentasi / membuat ulang.

    python -m pipeline.prepare_boundaries_ne <folder berisi geojson Natural Earth>

Butuh ne_10m_admin_0_countries.geojson dari
https://github.com/nvkelso/natural-earth-vector (folder geojson/).

Kenapa perlu: batas 38 provinsi (basis hitungan dan peta) sangat disederhanakan.
Sekitar 109 pulau kecil (±5.100 km², mis. Anambas) tidak tercakup, garis pantai
di pesisir gambut bisa meleset beberapa km (hingga ±30 km di Riau dan Kalbar),
dan batas darat menjorok ke negara tetangga. Natural Earth dipakai hanya untuk
menyaring: membuang titik di negara tetangga dan di laut lepas. Provinsinya
tetap ditentukan oleh batas 38 provinsi.
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box

from . import config as C

NEIGHBORS = ["MYS", "BRN", "TLS", "PNG", "PHL", "SGP", "AUS"]


def main():
    src = Path(sys.argv[1])
    a0 = gpd.read_file(src / "ne_10m_admin_0_countries.geojson")
    bb = box(92.5, -13.0, 143.5, 9.0)

    idn = a0[a0["ADM0_A3"] == "IDN"][["geometry"]].copy()
    idn["geometry"] = idn.geometry.simplify(0.0005, preserve_topology=True).set_precision(1e-4)
    idn.to_file(C.BOUNDARY_DIR / "daratan_indonesia.geojson", driver="GeoJSON")

    nb = a0[a0["ADM0_A3"].isin(NEIGHBORS)][["ADM0_A3", "NAME", "geometry"]].copy()
    nb["geometry"] = nb.geometry.intersection(bb)
    nb = nb[~nb.geometry.is_empty]
    nb["geometry"] = nb.geometry.simplify(0.002, preserve_topology=True).set_precision(1e-4)
    nb = nb.rename(columns={"ADM0_A3": "kode", "NAME": "negara"})
    nb.to_file(C.BOUNDARY_DIR / "negara_tetangga.geojson", driver="GeoJSON")
    print(f"daratan Indonesia + {len(nb)} negara tetangga tersimpan")


if __name__ == "__main__":
    main()
