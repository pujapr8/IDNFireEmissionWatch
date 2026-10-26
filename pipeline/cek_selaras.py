"""Cek apakah angka dashboard (arsip harian) dan grafik bulanan (ref_daily)
selaras, bulan demi bulan, untuk satu tahun penuh atau rentang tertentu.

    python -m pipeline.cek_selaras                      # tahun berjalan, Jan s.d. kemarin
    python -m pipeline.cek_selaras --tahun 2026
    python -m pipeline.cek_selaras --mulai 2026-08-01 --sampai 2026-09-30

Untuk satelit S-NPP, keyakinan tinggi saja. Per bulan dicetak:
  ref_daily   = batang grafik bulanan (jumlah per hari)
  arsip/hari  = hitungan dari arsip harian yang dipakai dashboard (harus = ref_daily)
  sel unik    = angka KPI atas bila periodenya sebulan penuh
  lama        = cara hitung sebelum perbaikan (high menurut satelit mana pun)
  tanpa_hs    = file harian yang belum punya hsats (hitungan memakai perkiraan lama)
Di akhir: daftar tanggal yang arsipnya tidak sama dengan ref_daily, beserta
kemungkinan sebabnya.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, timedelta

import pandas as pd

from . import archive as A
from . import config as C
from . import harian as HR

SNPP = C.SATELLITES[C.REF_SATELLITE]["bit"]


def load_ref(a: date, b: date) -> dict[str, int]:
    out = {}
    for y in range(a.year, b.year + 1):
        fn = C.ARCHIVE_DIR / f"ref_daily_{y}.csv"
        if not fn.exists():
            continue
        df = pd.read_csv(fn, dtype={"date": str, "provinsi": str})
        df = df[df["provinsi"] == A.TOTAL_ROW]
        out.update(dict(zip(df["date"], df["h"].astype(int))))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tahun", type=int)
    ap.add_argument("--mulai")
    ap.add_argument("--sampai")
    args = ap.parse_args(argv)
    yesterday = pd.Timestamp.now(tz=C.TIMEZONE).date() - timedelta(days=1)
    if args.mulai:
        a = date.fromisoformat(args.mulai)
        b = date.fromisoformat(args.sampai) if args.sampai else yesterday
    else:
        y = args.tahun or yesterday.year
        a, b = date(y, 1, 1), min(date(y, 12, 31), yesterday)

    ref = load_ref(a, b)
    M = defaultdict(lambda: {"ref": 0, "per_day": 0, "lama": 0, "no_hs": 0, "no_arsip": 0,
                             "cells": set(), "lama_cells": set()})
    year_cells, year_old_cells = set(), set()
    diffs = []
    for k in range((b - a).days + 1):
        d = a + timedelta(days=k)
        iso = d.isoformat()
        m = M[iso[:7]]
        if iso in ref:
            m["ref"] += ref[iso]
        p = HR.day_path(d)
        if not p.exists():
            m["no_arsip"] += 1
            if iso in ref:
                diffs.append((iso, ref[iso], None, "tidak ada arsip harian"))
            continue
        obj = json.loads(p.read_text(encoding="utf-8"))
        f = obj["fields"].index
        has = "hsats" in obj["fields"]
        m["no_hs"] += not has
        n_new = n_old = 0
        for r in obj["rows"]:
            if not r[f("sats")] & SNPP:
                continue
            hs = r[f("hsats")] if has else (r[f("sats")] if r[f("high")] else 0)
            if hs & SNPP:
                n_new += 1
                m["cells"].add(r[f("cell")])
                year_cells.add(r[f("cell")])
            if r[f("high")]:
                n_old += 1
                m["lama_cells"].add(r[f("cell")])
                year_old_cells.add(r[f("cell")])
        m["per_day"] += n_new
        m["lama"] += n_old
        if iso not in ref:
            diffs.append((iso, None, n_new, "ref_daily tidak punya tanggal ini (satelit referensi kosong/bolong atau belum diisi)"))
        elif ref[iso] != n_new:
            why = ("file belum punya hsats" if not has else
                   "arsip dan ref_daily dibangun pada waktu berbeda (SP vs NRT) atau salah satunya perlu dibangun ulang")
            diffs.append((iso, ref[iso], n_new, why))

    print(f"{'bulan':8}{'ref_daily':>11}{'arsip/hari':>12}{'selisih':>9}{'sel unik':>10}{'lama/hari':>11}{'lama unik':>10}{'tanpa_hs':>9}{'tanpa_arsip':>12}")
    t = defaultdict(int)
    for ym in sorted(M):
        m = M[ym]
        print(f"{ym:8}{m['ref']:>11,}{m['per_day']:>12,}{m['per_day'] - m['ref']:>+9,}{len(m['cells']):>10,}"
              f"{m['lama']:>11,}{len(m['lama_cells']):>10,}{m['no_hs']:>9}{m['no_arsip']:>12}")
        for k in ("ref", "per_day", "lama", "no_hs", "no_arsip"):
            t[k] += m[k]
    print(f"{'TOTAL':8}{t['ref']:>11,}{t['per_day']:>12,}{t['per_day'] - t['ref']:>+9,}{len(year_cells):>10,}"
          f"{t['lama']:>11,}{len(year_old_cells):>10,}{t['no_hs']:>9}{t['no_arsip']:>12}")
    print("\n(sel unik bulanan = KPI atas untuk periode sebulan penuh; sel unik TOTAL = KPI atas untuk seluruh rentang)")

    if t["no_hs"]:
        print(f"\nPERINGATAN: {t['no_hs']} file harian belum punya hsats; jalankan python -m pipeline.tambah_hsats")
    if diffs:
        print(f"\n{len(diffs)} tanggal arsip != ref_daily:")
        for iso, r, n, why in diffs[:60]:
            print(f"  {iso}  ref_daily={r if r is not None else '-':>6}  arsip={n if n is not None else '-':>6}  {why}")
        if len(diffs) > 60:
            print(f"  ... dan {len(diffs) - 60} tanggal lain")
    else:
        print("\nSemua tanggal: arsip harian = ref_daily.")


if __name__ == "__main__":
    main()
