"""Isi mundur arsip harian (mode Riwayat) dari tanggal awal sampai kemarin.

    python -m pipeline.backfill_harian                 # sejak config.HISTORY_START
    python -m pipeline.backfill_harian --mulai 2026-08-01 --sampai 2026-09-30
    python -m pipeline.backfill_harian --tanpa-aerosol
    python -m pipeline.backfill_harian --tahun 2015 2019 2023 --tanpa-mentah

- Tiga satelit VIIRS. Untuk tiap satelit dipakai data Standard Processing (SP)
  bila sudah tersedia, sisanya near real-time (NRT). Hari yang hanya tercakup
  sebagian satelit dicatat di arsip (dashboard menampilkan catatannya).
- Beberapa permintaan FIRMS berjalan serentak (--pekerja, default 4) dan
  terus mengalir lintas bulan. Hasil disimpan tiap bulan selesai; bulan yang
  semua harinya sudah ada dilewati, jadi aman dihentikan lalu dijalankan lagi.
  Bulan yang gagal (mis. FIRMS sedang lambat) dilewati dan dicoba lagi pada
  run berikutnya.
- Ikut menulis hitungan harian (3 satelit & satelit referensi) dan, bila kunci
  CDSE tersedia, citra aerosol Sentinel-5P per hari.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pandas as pd

from . import aerosol as AE
from . import archive as A
from . import config as C
from . import firms as F
from . import harian as HR

log = logging.getLogger("backfill_harian")


def fmt_range(r) -> str:
    return f"{r[0]} s.d. {r[1]}" if r else "tidak ada"


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


def fmt_duration(sec: float) -> str:
    m = max(1, round(sec / 60))
    if m < 60:
        return f"{m} menit"
    return f"{m // 60} jam {m % 60} menit"


class Progress:
    """Catat kemajuan per bulan dan perkiraan sisa waktu."""

    def __init__(self, total: int):
        self.total, self.done, self.t0 = total, 0, time.time()

    def step(self) -> str:
        self.done += 1
        el = time.time() - self.t0
        left = el / self.done * (self.total - self.done)
        tail = f"perkiraan sisa ±{fmt_duration(left)}" if self.done < self.total else "bulan terakhir"
        return f"progres {self.done}/{self.total} bulan, {tail}"


def days_covered(results) -> set:
    out = set()
    for a, b in results:
        out.update(a + timedelta(days=i) for i in range((b - a).days + 1))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mulai", default=C.HISTORY_START)
    ap.add_argument("--sampai", default=None, help="default: kemarin (WIB)")
    ap.add_argument("--pekerja", type=int, default=4, help="permintaan FIRMS serentak")
    ap.add_argument("--ulang", action="store_true", help="tulis ulang hari yang sudah ada")
    ap.add_argument("--tanpa-aerosol", action="store_true")
    ap.add_argument("--tahun", type=int, nargs="+",
                    help="isi tahun penuh (1 Jan-31 Des), mis. --tahun 2015 2019 2023; "
                         "menggantikan --mulai/--sampai")
    ap.add_argument("--tanpa-mentah", action="store_true",
                    help="jangan simpan deteksi mentah (raw/*.csv.gz); hemat ruang")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    key = os.environ.get("FIRMS_MAP_KEY") or sys.exit("FIRMS_MAP_KEY belum diisi")

    yesterday = pd.Timestamp.now(tz=C.TIMEZONE).date() - timedelta(days=1)
    if args.tahun:
        ranges = [(date(y, 1, 1), min(date(y, 12, 31), yesterday)) for y in sorted(set(args.tahun))]
    else:
        first = date.fromisoformat(args.mulai)
        last = date.fromisoformat(args.sampai) if args.sampai else yesterday
        ranges = [(first, min(last, yesterday))]
    ranges = [(a, b) for a, b in ranges if a <= b]

    av = availability(key)
    for sat in C.SATELLITES:
        log.info("%s: SP %s, NRT %s", sat, fmt_range(av.get(f"{sat}_SP")), fmt_range(av.get(f"{sat}_NRT")))

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
    if aer_client is not None and HR.OLD_AEROSOL_DIR.exists():
        import shutil
        shutil.rmtree(HR.OLD_AEROSOL_DIR)
        log.info("citra aerosol format lama dihapus; diunduh ulang dalam format baru")

    months = []
    for m_first, m_last in (m for a, b in ranges for m in month_ranges(a, b)):
        days = [m_first + timedelta(days=i) for i in range((m_last - m_first).days + 1)]
        todo = days if args.ulang else [d for d in days if not HR.day_path(d).exists()]
        months.append((m_first, days, todo))
    n_todo = sum(1 for m in months if m[2])
    if n_todo:
        log.info("%d bulan perlu diunduh, %d permintaan FIRMS serentak", n_todo, args.pekerja)
    prog = Progress(n_todo)

    def run(job):
        sat, src, a, b = job
        raw = F.fetch(key, src, (b - a).days + 1, a, C.FIRMS_MODE)
        kept, _ = F.filter_confidence(F.normalize(raw, src))
        return sat, a, b, kept

    failed = []
    with ThreadPoolExecutor(max_workers=max(1, args.pekerja)) as ex:
        # Semua permintaan diantrikan sekaligus supaya pekerja tidak menganggur
        # di pergantian bulan; hasilnya tetap diolah berurutan per bulan.
        futs = {}
        for m_first, days, todo in months:
            if todo:
                # 1 Jan WIB dimulai 31 Des 17.00 UTC, jadi tarik mulai sehari sebelumnya
                u_first, u_last = min(todo) - timedelta(days=1), max(todo)
                jobs = [(sat, *c) for sat in C.SATELLITES for c in plan(sat, u_first, u_last, av)]
                futs[m_first] = [ex.submit(run, j) for j in jobs]
        for m_first, days, todo in months:
            label = m_first.strftime("%Y-%m")
            if not todo:
                log.info("%s: sudah lengkap, dilewati", label)
            else:
                try:
                    results = [f.result() for f in futs.pop(m_first)]
                except Exception as e:  # noqa: BLE001
                    log.error("%s gagal, dilewati (dicoba lagi pada run berikutnya): %s",
                              label, str(e).replace(key, "***"))
                    failed.append(label)
                    results = None
                if results is not None:
                    process_month(label, todo, results, prov_names, kab_names, ctx_layers,
                                  assign, kabupaten, ref_prefix, prog, raw=not args.tanpa_mentah)
            if aer_client is not None:
                try:
                    AE.archive_days(days, aer_client, overwrite=args.ulang)
                except Exception as e:  # noqa: BLE001
                    log.error("%s: arsip aerosol gagal: %s", label, e)
            HR.build_index()

    if failed:
        log.warning("%d bulan gagal (%s). Jalankan perintah yang sama lagi untuk melanjutkan.",
                    len(failed), ", ".join(failed))
    else:
        log.info("Selesai. Jalankan `python -m pipeline.update` supaya dashboard memakai arsip baru.")


def process_month(label, todo, results, prov_names, kab_names, ctx_layers,
                  assign, kabupaten, ref_prefix, prog, raw=True):
    covered = {sat: set() for sat in C.SATELLITES}
    frames = []
    for sat, a, b, kept in results:
        covered[sat] |= days_covered([(a, b)])
        if len(kept):
            frames.append(kept)
    det = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if len(det):
        det = det.drop_duplicates(["latitude", "longitude", "t_utc", "source"])
    det, outside = F.attach_regions(det, assign, kabupaten, ctx_layers, drop_outside=True)
    groups = F.dedup_daily(det)

    def has(sat, d):
        # Satelit dihitung tersedia bila kedua tanggal UTC yang membentuk hari WIB tercakup
        return d in covered[sat] and d - timedelta(days=1) in covered[sat]

    changed, written, empty = [], [], []
    for d in todo:
        sats = [C.SATELLITES[s]["label"] for s in C.SATELLITES if has(s, d)]
        if not sats:
            empty.append(d)  # belum ada data satu satelit pun; dicoba lagi lain kali
            continue
        written.append(d)
        p = HR.write_day_json(d, groups, sats, prov_names, kab_names, ctx_layers)
        if p:
            changed.append(p)
        if raw and len(det):
            p = A.write_raw_day(d, det, prov_names, kab_names, ctx_layers)
            if p:
                changed.append(p)
    changed += A.upsert_year_files("daily", A.daily_counts(groups, prov_names, written))
    ref_days = [d for d in written if has(C.REF_SATELLITE, d)]
    if len(det):
        ref = det[det["source"].str.startswith(ref_prefix)]
        ref_groups = F.dedup_daily(ref) if len(ref) else ref
    else:
        ref_groups = det
    changed += A.upsert_year_files("ref_daily", A.daily_counts(ref_groups, prov_names, ref_days))
    n_days = groups[groups["date_wib"].isin(written)] if len(groups) else groups
    if empty:
        log.warning("%s: %d hari tanpa data satelit, dilewati: %s", label, len(empty),
                    ", ".join(d.isoformat() for d in empty[:5]) + (" ..." if len(empty) > 5 else ""))
    log.info("%s selesai: %d titik unik, %d di luar Indonesia dibuang · %s",
             label, len(n_days), outside, prog.step())


if __name__ == "__main__":
    main()
