"""Isi arsip tahun-tahun lalu untuk grafik perbandingan antartahun.

Hanya memakai satelit referensi (config.REF_SATELLITE) supaya angka antartahun
sebanding. Memakai data Standard Processing (SP) bila tersedia, sisanya NRT.

    FIRMS_MAP_KEY=xxxx python -m pipeline.backfill --years 2015 2019 2023 2024 2025 2026

Satu tahun ~75 permintaan ke FIRMS (jauh di bawah batas 5.000 per 10 menit).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, timedelta

import pandas as pd

from . import archive as A
from . import config as C
from . import firms as F

log = logging.getLogger("backfill")


def plan_chunks(year: int, sp_max: date | None, nrt_min: date | None, until: date):
    """Daftar (sumber, tanggal_awal_UTC, tanggal_akhir_UTC). Mulai 31 Des tahun
    sebelumnya karena 1 Jan WIB dimulai pukul 17.00 UTC hari sebelumnya."""
    start, end = date(year - 1, 12, 31), min(date(year, 12, 31), until)
    sp_src, nrt_src = f"{C.REF_SATELLITE}_SP", f"{C.REF_SATELLITE}_NRT"
    chunks, cur = [], start
    while cur <= end:
        stop = min(cur + timedelta(days=4), end)
        if sp_max and stop <= sp_max:
            src = sp_src
        elif sp_max and cur <= sp_max < stop:
            stop, src = sp_max, sp_src
        else:
            src = nrt_src
            if nrt_min and cur < nrt_min:
                log.warning("tanggal %s-%s tidak tercakup SP maupun NRT", cur, stop)
        chunks.append((src, cur, stop))
        cur = stop + timedelta(days=1)
    return chunks


def main(argv=None):
    ap = argparse.ArgumentParser()
    this_year = pd.Timestamp.now(tz=C.TIMEZONE).year
    ap.add_argument("--years", type=int, nargs="+",
                    default=C.BASELINE_YEARS_DEFAULT + [this_year],
                    help="default: tahun pembanding di config + tahun berjalan")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    key = os.environ.get("FIRMS_MAP_KEY") or sys.exit("FIRMS_MAP_KEY belum diisi")

    avail = F.data_availability(key)
    avail["max_date"] = pd.to_datetime(avail["max_date"]).dt.date
    avail["min_date"] = pd.to_datetime(avail["min_date"]).dt.date
    av = avail.set_index("data_id")
    sp_max = av.at[f"{C.REF_SATELLITE}_SP", "max_date"] if f"{C.REF_SATELLITE}_SP" in av.index else None
    nrt_min = av.at[f"{C.REF_SATELLITE}_NRT", "min_date"] if f"{C.REF_SATELLITE}_NRT" in av.index else None
    log.info("SP sampai %s, NRT sejak %s", sp_max, nrt_min)

    provinces = F.load_provinces()
    assign = F.Assigner(provinces)
    prov_names = provinces["prov_name"].tolist()
    now_wib = pd.Timestamp.now(tz=C.TIMEZONE)
    yesterday = now_wib.date() - timedelta(days=1)

    for year in args.years:
        frames = []
        for src, a, b in plan_chunks(year, sp_max, nrt_min, until=yesterday):
            n = (b - a).days + 1
            raw = F.fetch(key, src, n, a, C.FIRMS_MODE)
            norm = F.normalize(raw, src)
            kept, _ = F.filter_confidence(norm)
            if len(kept):
                frames.append(kept)
            log.info("%s %s..%s: %d deteksi n/h", src, a, b, len(kept))
        det = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if len(det):
            det = det.drop_duplicates(["latitude", "longitude", "t_utc"])
        det, outside = F.attach_regions(det, assign, None, [], drop_outside=True)
        groups = F.dedup_daily(det)
        last = min(date(year, 12, 31), yesterday)
        dates = [d.date() for d in pd.date_range(date(year, 1, 1), last)]
        counts = A.daily_counts(groups, prov_names, dates)
        changed = A.upsert_year_files("ref_daily", counts)
        log.info("tahun %d selesai: %d titik unik, %d di luar Indonesia, file: %s",
                 year, len(groups), outside, changed)


if __name__ == "__main__":
    main()
