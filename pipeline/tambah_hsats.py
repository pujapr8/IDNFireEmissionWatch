"""Tambahkan kolom `hsats` (bitmask satelit yang menilai sel itu high) ke arsip
harian yang sudah ada, tanpa mengunduh ulang dari FIRMS.

    python -m pipeline.tambah_hsats            # semua tanggal
    python -m pipeline.tambah_hsats --cek      # hanya laporan, tidak menulis

Sumbernya data/archive/raw/<tahun>/<tanggal>.csv.gz (satu baris per deteksi,
lengkap dengan satelit & confidence). Deteksi dikelompokkan ke sel grid yang sama
dengan firms.cell_key, lalu bit satelit yang berconfidence high digabung.

Hari yang file mentahnya tidak ada TIDAK diubah; dashboard memakai perkiraan
untuk hari itu (semua satelit yang melihat sel dianggap high bila baris itu high).
Hari seperti itu dicantumkan di akhir laporan. Setelah selesai jalankan
`python -m pipeline.update` supaya indeks dibangun ulang.
"""
from __future__ import annotations

import argparse
import json
import logging

import numpy as np
import pandas as pd

from . import config as C
from . import firms as F
from . import harian as HR

log = logging.getLogger("tambah_hsats")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cek", action="store_true", help="hanya laporan, jangan menulis")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    bit_of = {v["label"]: v["bit"] for v in C.SATELLITES.values()}
    done = skipped = no_raw = mismatch_days = 0
    no_raw_days = []
    for f in sorted(HR.DAY_DIR.glob("*/*.json")):
        if f.name == "indeks.json":
            continue
        obj = json.loads(f.read_text(encoding="utf-8"))
        if "hsats" in obj["fields"]:
            skipped += 1
            continue
        raw = C.ARCHIVE_DIR / "raw" / f.parent.name / f"{f.stem}.csv.gz"
        if not raw.exists():
            no_raw += 1
            no_raw_days.append(f.stem)
            continue
        det = pd.read_csv(raw, usecols=["lintang", "bujur", "satelit", "confidence"])
        det["cell"] = F.cell_key(det["lintang"], det["bujur"])
        det["bit"] = det["satelit"].map(bit_of).fillna(0).astype(int) * (det["confidence"] == "high")
        hs = det.groupby("cell")["bit"].agg(lambda s: int(np.bitwise_or.reduce(s.to_numpy()))).to_dict()
        fi = obj["fields"].index
        ci, hi = fi("cell"), fi("high")
        bad = 0
        for r in obj["rows"]:
            v = int(hs.get(r[ci], 0))
            if bool(v) != bool(r[hi]):
                bad += 1  # selisih pembulatan koordinat di batas sel; nilai lama dipertahankan
                v = int(r[fi("sats")]) if r[hi] else 0
            r.append(v)
        obj["fields"].append("hsats")
        if bad:
            mismatch_days += 1
            log.warning("%s: %d dari %d baris tidak cocok dengan file mentah (dipakai perkiraan)",
                        f.stem, bad, len(obj["rows"]))
        if not args.cek:
            f.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        done += 1
    log.info("%s: %d hari diperbarui, %d sudah punya hsats, %d tanpa file mentah, %d hari dengan baris tidak cocok",
             "CEK" if args.cek else "Selesai", done, skipped, no_raw, mismatch_days)
    if no_raw_days:
        log.warning("hari tanpa file mentah (tidak diubah): %s", ", ".join(no_raw_days))


if __name__ == "__main__":
    main()
