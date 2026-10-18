"""Isi mundur arsip harian (mode Riwayat) dari tanggal awal sampai kemarin.

    python -m pipeline.backfill_harian                 # sejak config.HISTORY_START
    python -m pipeline.backfill_harian --mulai 2026-08-01 --sampai 2026-09-30
    python -m pipeline.backfill_harian --tanpa-aerosol

- Tiga satelit VIIRS. Untuk tiap satelit dipakai data Standard Processing (SP)
  bila sudah tersedia, sisanya near real-time (NRT). Hari yang hanya tercakup
  sebagian satelit dicatat di arsip (dashboard menampilkan catatannya).
- Diproses per bulan supaya hemat memori; bulan yang semua harinya sudah ada
  dilewati, jadi aman dihentikan lalu dijalankan lagi.
- Ikut menulis hitungan harian (3 satelit & satelit referensi) dan, bila kunci
  CDSE tersedia, citra aerosol Sentinel-5P per hari.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pandas as pd

from . import aerosol as AE
from . import archive as A
from . import config as C
from . import firms as F
from . import harian as HR

log = logging.getLogger("backfill_harian")


def availability(key: str) -> dict:
    av = F.data_availability(key)
    av["min_date"] = pd.to_datetime(av["min_date"]).dt.date
    av["max_date"] = pd.to_datetime(av["max_date"]).dt.date
    return {r.data_id: (r.min_date, r.max_date) for r in av.itertuples()}


def plan(sat: str, first: date, last: date, av: dict) -> list[tuple[str, date, date]]:
    """Potongan maks. 5 hari berurutan dengan sumber yang sama:
    SP bila tanggal itu sudah tersedia di SP, selain itu NRT."""
    sp, nrt = av.get(f"{sat}_SP"), av.get(f"{sat}_NRT")

    def source(d):
        if sp and sp[0] <= d <= sp[1]:
            return f"{sat}_SP"
        if nrt and nrt[0] <= d <= nrt[1]:
            return f"{sat}_NRT"
        return None

    out, d = [], first
    while d <= last:
        src = source(d)
        if src is None:
            d += timedelta(days=1)
            continue
        start = d
        while d + timedelta(days=1) <= last and (d - start).days < 4 and source(d + timedelta(days=1)) == src:
            d += timedelta(days=1)
        out.append((src, start, d))
        d += timedelta(days=1)
    return out


def month_ranges(first: date, last: date):
    cur = first
    while cur <= last:
        nxt = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        yield cur, min(nxt - timedelta(days=1), last)
        cur = nxt


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mulai", default=C.HISTORY_START)
    ap.add_argument("--sampai", default=None, help="default: kemarin (WIB)")
    ap.add_argument("--pekerja", type=int, default=3, help="permintaan FIRMS paralel")
    ap.add_argument("--ulang", action="store_true", help="tulis ulang hari yang sudah ada")
    ap.add_argument("--tanpa-aerosol", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    key = os.environ.get("FIRMS_MAP_KEY") or sys.exit("FIRMS_MAP_KEY belum diisi")

    yesterday = pd.Timestamp.now(tz=C.TIMEZONE).date() - timedelta(days=1)
    first = date.fromisoformat(args.mulai)
    last = date.fromisoformat(args.sampai) if args.sampai else yesterday
    last = min(last, yesterday)

    av = availability(key)
    for sat in C.SATELLITES:
        log.info("%s: SP %s, NRT %s", sat, av.get(f"{sat}_SP"), av.get(f"{sat}_NRT"))

    provinces = F.load_provinces()
    assign = F.Assigner(provinces)
    kabupaten = F.load_kabupaten(assign)
    ctx_layers = F.load_context_layers()
    prov_names = provinces["prov_name"].tolist()
    kab_names = kabupaten["kab_name"].tolist() if kabupaten is not None else []
    ref_prefix = C.REF_SATELLITE + "_"
    aer_client = None
    if not args.tanpa_aerosol and AE.credentials():
        aer_client = AE.Client(*AE.credentials())
    elif not args.tanpa_aerosol:
        log.info("kunci CDSE tidak ada; arsip aerosol dilewati")

    for m_first, m_last in month_ranges(first, last):
        days = [m_first + timedelta(days=i) for i in range((m_last - m_first).days + 1)]
        todo = days if args.ulang else [d for d in days if not HR.day_path(d).exists()]
        if not todo:
            log.info("%s: sudah lengkap, dilewati", m_first.strftime("%Y-%m"))
        else:
            # 1 Jan WIB dimulai 31 Des 17.00 UTC, jadi tarik mulai sehari sebelumnya
            u_first, u_last = min(todo) - timedelta(days=1), max(todo)
            jobs = [(sat, *c) for sat in C.SATELLITES for c in plan(sat, u_first, u_last, av)]
            log.info("%s: %d hari, %d permintaan FIRMS", m_first.strftime("%Y-%m"), len(todo), len(jobs))

            def run(job):
                sat, src, a, b = job
                raw = F.fetch(key, src, (b - a).days + 1, a, C.FIRMS_MODE)
                kept, _ = F.filter_confidence(F.normalize(raw, src))
                return sat, a, b, kept

            covered: dict[str, set] = {sat: set() for sat in C.SATELLITES}
            frames = []
            with ThreadPoolExecutor(max_workers=max(1, args.pekerja)) as ex:
                for sat, a, b, kept in ex.map(run, jobs):
                    covered[sat].update(a + timedelta(days=i) for i in range((b - a).days + 1))
                    if len(kept):
                        frames.append(kept)
            det = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            if len(det):
                det = det.drop_duplicates(["latitude", "longitude", "t_utc", "source"])
            det, outside = F.attach_regions(det, assign, kabupaten, ctx_layers, drop_outside=True)
            groups = F.dedup_daily(det)

            changed = []
            for d in todo:
                # Satelit dihitung tersedia bila kedua tanggal UTC yang membentuk hari WIB tercakup
                sats = [C.SATELLITES[s]["label"] for s in C.SATELLITES
                        if d in covered[s] and d - timedelta(days=1) in covered[s]]
                p = HR.write_day_json(d, groups, sats, prov_names, kab_names, ctx_layers)
                if p:
                    changed.append(p)
                if len(det):
                    p = A.write_raw_day(d, det, prov_names, kab_names, ctx_layers)
                    if p:
                        changed.append(p)
            changed += A.upsert_year_files("daily", A.daily_counts(groups, prov_names, todo))
            if len(det):
                ref = det[det["source"].str.startswith(ref_prefix)]
                ref_groups = F.dedup_daily(ref) if len(ref) else ref
            else:
                ref_groups = det
            changed += A.upsert_year_files("ref_daily", A.daily_counts(ref_groups, prov_names, todo))
            n_days = groups[groups["date_wib"].isin(todo)] if len(groups) else groups
            log.info("%s selesai: %d titik unik, %d di luar Indonesia dibuang, %d file berubah",
                     m_first.strftime("%Y-%m"), len(n_days), outside, len(changed))
        if aer_client is not None:
            AE.archive_days(days, aer_client, overwrite=args.ulang)
        HR.build_index()

    log.info("Selesai. Jalankan `python -m pipeline.update` supaya dashboard memakai arsip baru.")


if __name__ == "__main__":
    main()
