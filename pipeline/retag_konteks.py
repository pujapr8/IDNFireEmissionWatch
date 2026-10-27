"""Tandai ulang arsip dengan lapisan konteks terbaru (mis. taman nasional baru
ditambahkan), tanpa mengunduh ulang data FIRMS.

    python -m pipeline.retag_konteks          # hanya bila lapisan berubah
    python -m pipeline.retag_konteks --paksa  # selalu

Yang diperbarui: arsip harian (data/archive/harian/*/*.json: bit konteks &
nama kawasan per titik) dan deteksi mentah (data/archive/raw/*/*.csv.gz: satu
kolom per lapisan). Tanda tangan lapisan (id + isi file) disimpan di
data/archive/harian/konteks.json; pipeline.update memanggil modul ini setiap
pembaruan, jadi menambah atau mengganti file di context/ cukup di-push.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import logging

import geopandas as gpd
import numpy as np
import pandas as pd

from . import config as C
from . import firms as F
from . import harian as HR

log = logging.getLogger("retag_konteks")
SIG_PATH = HR.DAY_DIR / "konteks.json"
RAW_DIR = C.ARCHIVE_DIR / "raw"


def signature(ctx_layers) -> list:
    out = []
    for l in ctx_layers:
        h = hashlib.sha1((C.ROOT / l["path"]).read_bytes()).hexdigest()[:12]
        out.append({"id": l["id"], "sha1": h, "name_field": l.get("name_field"), "group_field": l.get("group_field")})
    return out


def tag(lon, lat, ctx_layers):
    """(bit per titik, {id: (nama, grup)}) untuk koordinat. Sama dengan
    firms.attach_regions: titik di dalam poligon, ambil kecocokan pertama."""
    n = len(lon)
    bits = np.zeros(n, dtype=np.int64)
    names = {}
    if not n:
        return bits, {l["id"]: ([], []) for l in ctx_layers}
    pts = gpd.GeoDataFrame(geometry=gpd.points_from_xy(lon, lat), crs=4326)
    for bit, layer in enumerate(ctx_layers):
        g = layer["gdf"]
        j = gpd.sjoin(pts, g[["poly_i", "ctx_name", "ctx_group", "geometry"]], how="left", predicate="within")
        j = j[~j.index.duplicated(keep="first")].sort_index()
        inside = j["poly_i"].notna().to_numpy()
        bits[inside] |= (1 << bit)
        nm = np.where(inside, j["ctx_name"].fillna("").astype(str).to_numpy(), "")
        gr = np.where(inside, j["ctx_group"].fillna("").astype(str).to_numpy(), "")
        names[layer["id"]] = (nm, gr)
    return bits, names


def retag_day(path, ctx_layers) -> bool:
    obj = json.loads(path.read_text(encoding="utf-8"))
    f = obj["fields"]
    li, la, ci = f.index("lon"), f.index("lat"), f.index("ctx")
    rows = obj["rows"]
    bits, names = tag([r[li] for r in rows], [r[la] for r in rows], ctx_layers)
    ctx_names = {}
    for l in ctx_layers:
        if not l["ranked"]:
            continue
        nm, gr = names[l["id"]]
        c = {"names": [], "groups": [], "idx": []}
        pos = {}
        for k in range(len(rows)):
            if nm[k]:
                key = (nm[k], gr[k])
                if key not in pos:
                    pos[key] = len(c["names"])
                    c["names"].append(nm[k])
                    c["groups"].append(gr[k])
                c["idx"].append(pos[key])
            else:
                c["idx"].append(-1)
        ctx_names[l["id"]] = c
    for k, r in enumerate(rows):
        r[ci] = int(bits[k])
    obj["ctx_layers"] = [l["id"] for l in ctx_layers]
    obj["ctx_names"] = ctx_names
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    if path.read_text(encoding="utf-8") == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def retag_raw(path, ctx_layers) -> bool:
    old = path.read_bytes()
    df = pd.read_csv(io.BytesIO(gzip.decompress(old)), dtype=str, keep_default_na=False)
    # Pertahankan semua kolom selain kolom lapisan konteks (yang ditulis ulang di bawah)
    ctx_ids = {l["id"] for l in C.CONTEXT_LAYERS} | {l["id"] for l in ctx_layers}
    df = df[[c for c in df.columns if c not in ctx_ids]].copy()
    bits, names = tag(pd.to_numeric(df["bujur"]).to_numpy(), pd.to_numeric(df["lintang"]).to_numpy(), ctx_layers)
    for bit, l in enumerate(ctx_layers):
        inside = (bits & (1 << bit)) > 0
        df[l["id"]] = names[l["id"]][0] if l["ranked"] else np.where(inside, "ya", "")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, filename="") as gz:
        gz.write(df.to_csv(index=False).encode("utf-8"))
    data = buf.getvalue()
    if data == old:
        return False
    path.write_bytes(data)
    return True


def run(ctx_layers=None, force: bool = False) -> list[str]:
    ctx_layers = ctx_layers if ctx_layers is not None else F.load_context_layers()
    sig = signature(ctx_layers)
    try:
        old = json.loads(SIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        old = None
    if not force and old == sig:
        return []
    days = sorted(HR.DAY_DIR.glob("*/*.json"))
    raws = sorted(RAW_DIR.glob("*/*.csv.gz")) if RAW_DIR.exists() else []
    log.info("lapisan konteks berubah (%s); menandai ulang %d hari arsip dan %d berkas mentah...",
             ", ".join(l["id"] for l in ctx_layers) or "tidak ada", len(days), len(raws))
    changed = []
    for p in days:
        if retag_day(p, ctx_layers):
            changed.append(str(p.relative_to(C.ROOT)))
    for p in raws:
        if retag_raw(p, ctx_layers):
            changed.append(str(p.relative_to(C.ROOT)))
    SIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    SIG_PATH.write_text(json.dumps(sig, ensure_ascii=False, indent=1), encoding="utf-8")
    changed.append(str(SIG_PATH.relative_to(C.ROOT)))
    log.info("penandaan ulang selesai: %d berkas berubah", len(changed) - 1)
    return changed


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--paksa", action="store_true", help="tandai ulang walau lapisan tidak berubah")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run(force=args.paksa)


if __name__ == "__main__":
    main()
