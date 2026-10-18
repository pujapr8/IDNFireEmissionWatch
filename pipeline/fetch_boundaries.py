"""Unduh batas kabupaten/kota (geoBoundaries, gbOpen IDN ADM2) sekali saja,
lalu simpan ke boundaries/kabupaten.geojson dengan kolom kab_name.

    python -m pipeline.fetch_boundaries

Provinsi induk tiap kabupaten ditentukan ulang dari batas 38 provinsi saat
pipeline berjalan, jadi kabupaten di Papua otomatis masuk provinsi barunya.
Bila Anda punya batas resmi BIG yang lebih baru, cukup taruh sendiri di
boundaries/kabupaten.geojson dengan kolom kab_name.
"""
from __future__ import annotations

import io
import json
import logging

import geopandas as gpd
import requests

from . import config as C

API = "https://www.geoboundaries.org/api/current/gbOpen/IDN/ADM2/"


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    meta = requests.get(API, timeout=60).json()
    url = meta.get("simplifiedGeometryGeoJSON") or meta["gjDownloadURL"]
    logging.info("mengunduh %s", url)
    r = requests.get(url, timeout=300)
    r.raise_for_status()
    g = gpd.read_file(io.BytesIO(r.content)).to_crs(4326)
    g = g.rename(columns={"shapeName": "kab_name"})[["kab_name", "geometry"]]
    g["geometry"] = g.geometry.make_valid().set_precision(1e-4)
    C.BOUNDARY_DIR.mkdir(exist_ok=True)
    (C.BOUNDARY_DIR / "kabupaten.geojson").write_text(g.to_json(drop_id=True))
    keep = {k: meta.get(k) for k in ("boundarySource", "boundaryLicense",
                                     "boundaryYearRepresented", "licenseDetail",
                                     "sourceDataUpdateDate", "boundaryID")}
    (C.BOUNDARY_DIR / "kabupaten_sumber.json").write_text(json.dumps(keep, indent=1))
    logging.info("tersimpan: %d kabupaten/kota; sumber %s", len(g), keep)


if __name__ == "__main__":
    main()
