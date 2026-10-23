"""Arsip harian Sentinel-5P untuk bola dunia di dashboard: indeks aerosol UV
dan karbon monoksida (CO), ditampilkan sebagai rata-rata 3 hari.

    python -m pipeline.s5p                       # lengkapi sejak config.S5P_START
    python -m pipeline.s5p --mulai 2026-08-01 --sampai 2026-10-22
    python -m pipeline.s5p --ulang --mulai 2026-10-01   # unduh ulang

- Satu PNG abu-abu per hari (00.00-24.00 WIB) per variabel, grid lintang-bujur
  config.S5P_BBOX dengan resolusi config.S5P_RES. Piksel menyimpan NILAI
  (lihat config.S5P_VARS), bukan warna; 0 = tidak ada data (awan, celah orbit).
- Dashboard merata-ratakan hari d-2, d-1, dan d per piksel (hanya piksel yang
  ada datanya) lalu mewarnainya. Jadi skala warna bisa diganti tanpa unduh ulang.
- Hari yang diunduh saat datanya masih "cepat" (NRTI, <= 3 hari) diunduh
  sekali lagi setelah 5 hari, memakai produk OFFL yang lebih baik.
- Butuh CDSE_CLIENT_ID & CDSE_CLIENT_SECRET (sama dengan lapisan aerosol).
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import date, timedelta

import pandas as pd

from . import aerosol as AE
from . import config as C

log = logging.getLogger("s5p")

DIR = C.ARCHIVE_DIR / C.S5P_DIRNAME
INDEX = DIR / "indeks.json"

EVALSCRIPT = """//VERSION=3
function setup() {
  return { input: ["__BAND__", "dataMask"], output: { bands: 1, sampleType: "UINT8" } };
}
var MIN = __MIN__, MAX = __MAX__, F = __F__;
function evaluatePixel(s) {
  if (!s.dataMask) return [0];
  var v = s.__BAND__ * F;
  if (!isFinite(v)) return [0];
  v = Math.max(MIN, Math.min(MAX, v));
  return [1 + Math.round((v - MIN) / (MAX - MIN) * 254)];
}
"""


def grid() -> dict:
    w, s, e, n = C.S5P_BBOX
    return {"bbox": list(C.S5P_BBOX), "res": C.S5P_RES,
            "width": round((e - w) / C.S5P_RES), "height": round((n - s) / C.S5P_RES)}


def evalscript(var: str) -> str:
    v = C.S5P_VARS[var]
    return (EVALSCRIPT.replace("__BAND__", v["band"]).replace("__MIN__", repr(float(v["min"])))
            .replace("__MAX__", repr(float(v["max"]))).replace("__F__", repr(float(v["factor"]))))


def path(var: str, d: date):
    return DIR / var / f"{d.year}" / f"{d.isoformat()}.png"


EMPTY_PNG_BYTES = 3000  # PNG sebesar ini praktis kosong (semua piksel 0)


def fetch(client: AE.Client, var: str, d: date, offl: bool = False) -> bytes:
    """Satu hari WIB (00.00-24.00) satu variabel. offl=True: utamakan produk
    OFFL (lebih akurat); bila kosong, pakai produk apa pun yang tersedia."""
    if offl:
        content = _fetch(client, var, d, "OFFL")
        if len(content) > EMPTY_PNG_BYTES:
            return content
    return _fetch(client, var, d, None)


def _fetch(client: AE.Client, var: str, d: date, timeliness: str | None) -> bytes:
    g = grid()
    t_from = pd.Timestamp(d.isoformat(), tz=C.TIMEZONE).tz_convert("UTC")
    t_to = t_from + pd.Timedelta(hours=24) - pd.Timedelta(seconds=1)
    data_filter = {"timeRange": {"from": t_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                 "to": t_to.strftime("%Y-%m-%dT%H:%M:%SZ")},
                   "mosaickingOrder": "mostRecent"}
    if timeliness:
        data_filter["timeliness"] = timeliness
    return client.process({
        "input": {
            "bounds": {"bbox": g["bbox"], "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"}},
            "data": [{"type": "sentinel-5p-l2", "dataFilter": data_filter,
                      "processing": {"upsampling": "BILINEAR"}}],
        },
        "output": {"width": g["width"], "height": g["height"],
                   "responses": [{"identifier": "default", "format": {"type": "image/png"}}]},
        "evalscript": evalscript(var),
    })


def _read_index() -> dict:
    try:
        return json.loads(INDEX.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def build_index(fresh: dict | None = None) -> dict:
    """indeks.json: grid, skala tiap variabel, tanggal yang tersedia."""
    old = _read_index()
    fresh = fresh if fresh is not None else old.get("fresh", {})
    out_vars = {}
    for var, v in C.S5P_VARS.items():
        days = sorted(f.stem for f in (DIR / var).glob("*/*.png")) if (DIR / var).exists() else []
        out_vars[var] = {"label": v["label"], "unit": v["unit"], "min": v["min"], "max": v["max"],
                         "dir": f"{C.S5P_DIRNAME}/{var}", "days": days}
    idx = {"grid": grid(), "start": C.S5P_START, "window_days": 3, "vars": out_vars,
           "fresh": {k: sorted(set(vs)) for k, vs in fresh.items() if vs},
           "source": "Copernicus Sentinel-5P TROPOMI (Copernicus Data Space Ecosystem)"}
    text = json.dumps(idx, ensure_ascii=False, separators=(",", ":"))
    DIR.mkdir(parents=True, exist_ok=True)
    if not INDEX.exists() or INDEX.read_text(encoding="utf-8") != text:
        INDEX.write_text(text, encoding="utf-8")
    return idx


def run(first: date, last: date, client: AE.Client | None = None, limit: int | None = None,
        overwrite: bool = False) -> list[str]:
    """Unduh hari yang belum ada (terbaru dulu, maks. `limit` per variabel) dan
    unduh ulang sekali hari yang dulu masih data cepat. Kembalikan file yang berubah."""
    cred = AE.credentials()
    if client is None:
        if not cred:
            log.info("CDSE_CLIENT_ID/CDSE_CLIENT_SECRET belum diisi; arsip Sentinel-5P dilewati")
            return []
        client = AE.Client(*cred)
    fresh = {k: set(v) for k, v in _read_index().get("fresh", {}).items()}
    days = [last - timedelta(days=i) for i in range((last - first).days + 1)]  # terbaru dulu
    changed = []
    for var in C.S5P_VARS:
        fr = fresh.setdefault(var, set())
        missing = [d for d in days if overwrite or not path(var, d).exists()]
        if limit is not None:
            missing = missing[:limit]
        # Data cepat yang sudah >= 5 hari: unduh sekali lagi (produk OFFL)
        upgrade = [d for d in days if d.isoformat() in fr and (last - d).days >= 5 and d not in missing]
        todo = missing + upgrade
        if not todo:
            continue
        log.info("Sentinel-5P %s: %d hari diunduh (%d baru, %d diperbarui)", var, len(todo), len(missing), len(upgrade))
        for d in todo:
            try:
                content = fetch(client, var, d, offl=(last - d).days >= 4)
            except Exception as e:  # noqa: BLE001
                log.warning("Sentinel-5P %s %s gagal: %s", var, d, e)
                continue
            p = path(var, d)
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists() or p.read_bytes() != content:
                p.write_bytes(content)
                changed.append(str(p.relative_to(C.ROOT)))
            if (last - d).days <= 3:
                fr.add(d.isoformat())
            else:
                fr.discard(d.isoformat())
    build_index({k: sorted(v) for k, v in fresh.items()})
    return changed


def update(now_wib: pd.Timestamp, client: AE.Client | None = None) -> list[str]:
    """Dipanggil tiap pembaruan: lengkapi s.d. kemarin (WIB), bertahap."""
    last = now_wib.date() - timedelta(days=1)
    first = date.fromisoformat(C.S5P_START)
    if last < first:
        return []
    return run(first, last, client, limit=C.S5P_CATCHUP_PER_RUN)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mulai", default=C.S5P_START)
    ap.add_argument("--sampai", default=None, help="default: kemarin (WIB)")
    ap.add_argument("--ulang", action="store_true", help="unduh ulang hari yang sudah ada")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    yesterday = pd.Timestamp.now(tz=C.TIMEZONE).date() - timedelta(days=1)
    last = min(date.fromisoformat(args.sampai) if args.sampai else yesterday, yesterday)
    changed = run(date.fromisoformat(args.mulai), last, overwrite=args.ulang)
    log.info("Selesai: %d file berubah", len(changed))


if __name__ == "__main__":
    main()
