"""Isi hitungan harian tahun-tahun pembanding (grafik per bulan & kumulatif).

    python -m pipeline.backfill --years 2015 2019 2023 2024 2025
    python -m pipeline.backfill --years 2019 --pekerja 6

- Hanya satelit referensi (config.REF_SATELLITE, S-NPP) supaya angka
  antartahun sebanding. Standard Processing (SP) bila tersedia, sisanya NRT.
- Beberapa permintaan FIRMS berjalan serentak (--pekerja, default 4).
- Hasil disimpan tiap bulan selesai. Bila terhenti, jalankan perintah yang
  sama lagi: bulan yang sudah ada di data/archive/ref_daily_<tahun>.csv
  dilewati. Tahun berjalan sudah diisi oleh pipeline.backfill_harian.
- Satu tahun ±85 permintaan, jauh di bawah batas FIRMS 5.000 per 10 menit.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pandas as pd

from . import archive as A
from . import backfill_harian as BH
from . import config as C
from . import firms as F

log = logging.getLogger("backfill")


def done_dates(year: int) -> set[str]:
    """Tanggal yang sudah punya baris total di ref_daily_<tahun>.csv."""
    path = C.ARCHIVE_DIR / f"ref_daily_{year}.csv"
    if not path.exists():
        return set()
    df = pd.read_csv(path, dtype={"date": str, "provinsi": str}, usecols=["date", "provinsi"])
    return set(df.loc[df["provinsi"] == A.TOTAL_ROW, "date"])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, nargs="+", default=C.BASELINE_YEARS_DEFAULT,
                    help="default: tahun pembanding di config")
    ap.add_argument("--pekerja", type=int, default=4, help="permintaan FIRMS serentak")
    ap.add_argument("--ulang", action="store_true", help="hitung ulang bulan yang sudah ada")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    key = os.environ.get("FIRMS_MAP_KEY") or sys.exit("FIRMS_MAP_KEY belum diisi")

    sat = C.REF_SATELLITE
    av = BH.availability(key)
    log.info("%s: SP %s, NRT %s", sat, BH.fmt_range(av.get(f"{sat}_SP")), BH.fmt_range(av.get(f"{sat}_NRT")))

    provinces = F.load_provinces()
    assign = F.Assigner(provinces)
    prov_names = provinces["prov_name"].tolist()
    yesterday = pd.Timestamp.now(tz=C.TIMEZONE).date() - timedelta(days=1)

    months = []  # (label, hari-hari yang perlu diisi)
    for year in sorted(set(args.years)):
        have = set() if args.ulang else done_dates(year)
        first, last = date(year, 1, 1), min(date(year, 12, 31), yesterday)
        skipped = 0
        for a, b in BH.month_ranges(first, last):
            days = [a + timedelta(days=i) for i in range((b - a).days + 1)]
            todo = [d for d in days if d.isoformat() not in have]
            if todo:
                months.append((a.strftime("%Y-%m"), todo))
            else:
                skipped += 1
        if skipped:
            log.info("%d: %d bulan sudah ada, dilewati", year, skipped)
    if not months:
        log.info("Semua bulan sudah terisi. Jalankan `python -m pipeline.update`.")
        return
    log.info("%d bulan perlu diunduh, %d permintaan FIRMS serentak", len(months), args.pekerja)
    prog = BH.Progress(len(months))

    def run(job):
        src, a, b = job
        raw = F.fetch(key, src, (b - a).days + 1, a, C.FIRMS_MODE)
        kept, _ = F.filter_confidence(F.normalize(raw, src))
        return a, b, kept

    failed = []
    with ThreadPoolExecutor(max_workers=max(1, args.pekerja)) as ex:
        # Antrikan semuanya sekaligus; hasil diolah berurutan per bulan
        futs = [[ex.submit(run, j) for j in BH.plan(sat, min(todo) - timedelta(days=1), max(todo), av)]
                for _, todo in months]
        for (label, todo), fl in zip(months, futs):
            try:
                results = [f.result() for f in fl]
            except Exception as e:  # noqa: BLE001
                log.error("%s gagal, dilewati (dicoba lagi pada run berikutnya): %s",
                          label, str(e).replace(key, "***"))
                failed.append(label)
                continue
            covered = BH.days_covered([(a, b) for a, b, _ in results])
            frames = [k for _, _, k in results if len(k)]
            det = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            if len(det):
                det = det.drop_duplicates(["latitude", "longitude", "t_utc"])
            det, outside = F.attach_regions(det, assign, None, [], drop_outside=True)
            groups = F.dedup_daily(det)
            ok_days = [d for d in todo if d in covered and d - timedelta(days=1) in covered]
            A.upsert_year_files("ref_daily", A.daily_counts(groups, prov_names, ok_days))
            n = groups[groups["date_wib"].isin(ok_days)] if len(groups) else groups
            miss = len(todo) - len(ok_days)
            if miss:
                log.warning("%s: %d hari tidak tercakup data FIRMS, dilewati", label, miss)
            log.info("%s selesai: %d titik, %d di luar Indonesia dibuang · %s",
                     label, len(n), outside, prog.step())

    if failed:
        log.warning("%d bulan gagal (%s). Jalankan perintah yang sama lagi untuk melanjutkan.",
                    len(failed), ", ".join(failed))
    else:
        log.info("Selesai. Jalankan `python -m pipeline.update` supaya grafik memakai data baru.")


if __name__ == "__main__":
    main()
