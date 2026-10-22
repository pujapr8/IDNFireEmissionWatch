"""Pembaruan rutin: tarik data FIRMS terbaru, olah, perbarui arsip, dan tulis
file JSON untuk dashboard ke data/live/.

    FIRMS_MAP_KEY=xxxx python -m pipeline.update
    python -m pipeline.update --offline contoh/   # pakai CSV lokal untuk uji
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import geopandas as gpd
import pandas as pd

from . import archive as A
from . import config as C
from . import firms as F
from . import aerosol as AE
from . import harian as HR
from . import weather as W

log = logging.getLogger("update")


def fetch_all(map_key: str, mode: str, first, older_n: int) -> dict:
    """Tarik semua bagian (3 satelit x 2 rentang tanggal) serentak.
    Hasil: {sumber: [DataFrame, ...]} atau {sumber: Exception} bila gagal."""
    jobs = []
    for src in C.SOURCES:
        jobs.append((src, 5, None))
        if older_n > 0:
            jobs.append((src, older_n, first))
    out: dict = {src: [] for src in C.SOURCES}
    with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        futs = [(src, ex.submit(F.fetch, map_key, src, n, start, mode)) for src, n, start in jobs]
        for src, fut in futs:
            try:
                df = fut.result()
                if not isinstance(out[src], Exception):
                    out[src].append(df)
            except Exception as e:  # noqa: BLE001
                out[src] = e
    return out


def collect(map_key: str | None, offline: Path | None, today_utc):
    """Kembalikan (deteksi gabungan yang lolos filter, status per sumber, mode)."""
    first = today_utc - timedelta(days=C.LIVE_DAYS - 1)
    older_n = C.LIVE_DAYS - 5
    frames, status = [], []
    mode = C.FIRMS_MODE
    fetched = {}
    if not offline:
        fetched = fetch_all(map_key, mode, first, older_n)
        bad = [v for v in fetched.values() if isinstance(v, Exception)]
        if mode == "country" and bad and not any("MAP_KEY" in str(e) for e in bad):
            log.warning("endpoint country gagal (%s); beralih ke area",
                        str(bad[0]).replace(map_key, "***"))
            mode = "area"
            fetched = fetch_all(map_key, mode, first, older_n)
    for src, meta in C.SOURCES.items():
        st = {"key": src, "label": meta["label"], "ok": False}
        try:
            if offline:
                path = offline / f"{src}.csv"
                raw = pd.read_csv(path) if path.exists() else pd.DataFrame()
                mode = "offline"
            else:
                parts = fetched[src]
                if isinstance(parts, Exception):
                    raise parts
                raw = pd.concat([p for p in parts if len(p)], ignore_index=True) \
                    if any(len(p) for p in parts) else pd.DataFrame()
            norm = F.normalize(raw, src)
            if len(norm):
                # Dua permintaan bisa tumpang tindih; buang baris kembar
                norm = norm.drop_duplicates(["latitude", "longitude", "t_utc"])
            kept, n_low = F.filter_confidence(norm)
            st.update(ok=True, n_raw=int(len(norm)), n_low=n_low, n_kept=int(len(kept)),
                      latest_wib=None if kept.empty
                      else kept["t_wib"].max().strftime("%Y-%m-%d %H:%M"))
            frames.append(kept)
        except Exception as e:  # noqa: BLE001 - satu satelit gagal tidak boleh menggagalkan semua
            msg = str(e).replace(map_key, "***") if map_key else str(e)
            log.error("%s gagal: %s", src, msg)
            st["error"] = msg[:300]  # summary.json publik: jangan pernah memuat kunci
        status.append(st)
    frames = [f for f in frames if len(f)]
    det = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return det, status, mode


def finalizable_dates(now_wib: pd.Timestamp, today_utc) -> list:
    first_utc = today_utc - timedelta(days=C.LIVE_DAYS - 1)
    out = []
    d = first_utc + timedelta(days=1)  # tanggal WIB pertama yang tercakup penuh
    while d <= now_wib.date() - timedelta(days=1):
        ready = pd.Timestamp(datetime.combine(d + timedelta(days=1), datetime.min.time()),
                             tz=C.TIMEZONE) + pd.Timedelta(hours=C.FINALIZE_HOUR_NEXT_DAY)
        if now_wib >= ready:
            out.append(d)
        d += timedelta(days=1)
    return out


def round_geom_geojson(gdf: gpd.GeoDataFrame, path: Path, precision: int = 4):
    gdf = gdf.copy()
    gdf["geometry"] = gdf.geometry.set_precision(10 ** -precision)
    gdf = gdf[~gdf.geometry.is_empty]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(gdf.to_json(drop_id=True, to_wgs84=True))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", type=Path, help="folder berisi <SOURCE>.csv (uji lokal)")
    ap.add_argument("--now", help="waktu acuan ISO (uji lokal), mis. 2026-10-18T10:00Z")
    ap.add_argument("--skip-weather", action="store_true")
    ap.add_argument("--weather-sample", type=Path, help="folder berisi wind_raw.json & aq_raw.json")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    map_key = os.environ.get("FIRMS_MAP_KEY")
    if not args.offline and not map_key:
        sys.exit("FIRMS_MAP_KEY belum diisi")

    now_utc = pd.Timestamp(args.now) if args.now else pd.Timestamp.now(tz="UTC")
    if now_utc.tzinfo is None:
        now_utc = now_utc.tz_localize("UTC")
    now_wib = now_utc.tz_convert(C.TIMEZONE)
    today_utc = now_utc.date()

    provinces = F.load_provinces()
    assign = F.Assigner(provinces)
    kabupaten = F.load_kabupaten(assign)
    ctx_layers = F.load_context_layers()
    prov_names = provinces["prov_name"].tolist()
    kab_names = kabupaten["kab_name"].tolist() if kabupaten is not None else []

    log.info("Mulai: menarik data FIRMS 3 satelit (6 permintaan serentak; biasanya 1-3 menit)")
    det, status, mode = collect(map_key, args.offline, today_utc)
    if not any(s["ok"] for s in status):
        sys.exit("Semua sumber FIRMS gagal; dashboard lama dibiarkan.")

    log.info("Memasangkan %d deteksi ke provinsi...", len(det))
    det, outside = F.attach_regions(det, assign, kabupaten, ctx_layers, drop_outside=True)
    groups = F.dedup_daily(det)
    log.info("deteksi %d -> %d titik unik (tanggal x sel)", len(det), len(groups))

    # ---------------- arsip ----------------
    final_dates = finalizable_dates(now_wib, today_utc)
    changed = []
    ok_sats = [F.sat_base(s["key"]) for s in status if s["ok"]]
    failed = [s["label"] for s in status if not s["ok"]]
    if final_dates and failed:
        # Arsip tetap ditulis dengan satelit yang berhasil. Selama tanggal itu masih
        # di jendela live, run berikutnya menulis ulang dengan satelit lengkap;
        # sesudahnya, "Isi arsip awal" melengkapinya bila FIRMS menyediakan datanya.
        log.warning("satelit gagal diunduh (%s); arsip harian ditulis tanpa satelit itu", ", ".join(failed))
    if final_dates:
        day_sats, keep = {}, []
        for d in final_dates:
            gone = A.empty_sats(groups, d, ok_sats)
            day_sats[d] = [s for s in ok_sats if s not in gone]
            if gone:
                log.warning("%s: data %s kosong (satelit lain mendeteksi api); tidak dihitung sebagai nol",
                            d, ", ".join(C.SATELLITES[s]["label"] for s in sorted(gone)))
            # Jangan menimpa arsip yang satelitnya lebih lengkap dengan yang kurang lengkap
            had = HR.day_sats(d)
            now = {C.SATELLITES[s]["label"] for s in day_sats[d]}
            if had is not None and not now >= set(had):
                keep.append(d)
        if keep:
            log.info("arsip lama dipertahankan (satelitnya lebih lengkap): %s",
                     ", ".join(d.isoformat() for d in keep))
        final_dates = [d for d in final_dates if d not in keep]
        changed += A.upsert_year_files("daily", A.daily_counts(groups, prov_names, final_dates))
        ref_src = f"{C.REF_SATELLITE}_NRT"
        ref_dates = [d for d in final_dates if C.REF_SATELLITE in day_sats[d]]
        no_ref = [d for d in final_dates if d not in ref_dates]
        if ref_dates:
            ref_groups = F.dedup_daily(det[det["source"] == ref_src]) if len(det) else det
            changed += A.upsert_year_files("ref_daily",
                                           A.daily_counts(ref_groups, prov_names, ref_dates))
        if no_ref:
            changed += A.drop_dates("ref_daily", no_ref)
            log.warning("tanpa data %s (tidak dihitung di perbandingan antartahun): %s",
                        C.REF_LABEL, ", ".join(d.isoformat() for d in no_ref))
        for d in final_dates:
            sats_d = [C.SATELLITES[s]["label"] for s in day_sats[d]]
            if not sats_d:
                continue
            p = HR.write_day_json(d, groups, sats_d, prov_names, kab_names, ctx_layers)
            if p:
                changed.append(p)
            if len(det):
                p = A.write_raw_day(d, det, prov_names, kab_names, ctx_layers)
                if p:
                    changed.append(p)
        if not args.skip_weather:
            changed += AE.archive_days(final_dates)
    if not args.skip_weather and AE.credentials():
        # Pindah dari citra berwarna (format lama) ke citra nilai indeks
        if HR.OLD_AEROSOL_DIR.exists():
            shutil.rmtree(HR.OLD_AEROSOL_DIR)
            changed.append("data/archive/aerosol/ (format lama, dihapus)")
            log.info("citra aerosol format lama dihapus; arsip diunduh ulang bertahap")
        # Lengkapi arsip aerosol yang belum ada, beberapa hari per pembaruan
        missing = HR.missing_aerosol_days()
        if missing:
            n = min(len(missing), C.AEROSOL_CATCHUP_PER_RUN)
            log.info("aerosol: %d hari arsip belum punya citra; mengunduh %d terbaru", len(missing), n)
            changed += AE.archive_days(missing[:n])
    HR.build_index()
    if changed:
        log.info("arsip berubah: %d file (%s%s)", len(changed), ", ".join(changed[:4]),
                 ", ..." if len(changed) > 4 else "")
    else:
        log.info("arsip berubah: tidak ada")

    # ---------------- keluaran live ----------------
    out = C.OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    t0_date = now_wib.date() - timedelta(days=7)
    t0 = pd.Timestamp(datetime.combine(t0_date, datetime.min.time()), tz=C.TIMEZONE)
    cutoff = now_wib - pd.Timedelta(days=7)
    if len(groups):
        keep = (groups["t_last"] >= cutoff) | (groups["date_wib"] >= now_wib.date() - timedelta(days=6))
        g = groups[keep & (groups["date_wib"] >= t0_date)].copy()
    else:
        g = groups
    ranked = [l for l in ctx_layers if l["ranked"]]
    rows, ctx_names = [], {l["id"]: {"names": [], "groups": [], "idx": []} for l in ranked}
    name_pos = {l["id"]: {} for l in ranked}
    for r in g.itertuples(index=False):
        rows.append([
            round(float(r.longitude), 4), round(float(r.latitude), 4),
            (r.date_wib - t0_date).days,
            int((r.t_first - t0).total_seconds() // 60),
            int((r.t_last - t0).total_seconds() // 60),
            int(r.h), None if pd.isna(r.frp) else round(float(r.frp), 1),
            int(r.sats), int(r.n_det), int(r.prov_i), int(r.kab_i), int(r.ctx), int(r.cell),
        ])
        for l in ranked:
            nm = getattr(r, f"ctxname_{l['id']}") or ""
            gr = getattr(r, f"ctxgroup_{l['id']}") or ""
            if nm:
                key = (nm, gr)
                if key not in name_pos[l["id"]]:
                    name_pos[l["id"]][key] = len(ctx_names[l["id"]]["names"])
                    ctx_names[l["id"]]["names"].append(nm)
                    ctx_names[l["id"]]["groups"].append(gr)
                ctx_names[l["id"]]["idx"].append(name_pos[l["id"]][key])
            else:
                ctx_names[l["id"]]["idx"].append(-1)
    A.dump_json({
        "fields": ["lon", "lat", "day", "t_first", "t_last", "high", "frp", "sats",
                   "n_det", "prov", "kab", "ctx", "cell"],
        "t0_wib": t0.isoformat(),
        "t0_date": t0_date.isoformat(),
        "now_min": int((now_wib - t0).total_seconds() // 60),
        "rows": rows,
        "ctx_names": ctx_names,
    }, out / "hotspots.json")

    ctx_meta = []
    for i, l in enumerate(ctx_layers):
        fname = f"konteks_{l['id']}.geojson"
        gg = l["gdf"]
        simp = gg.to_crs(3395)
        simp["geometry"] = simp.geometry.simplify(l.get("simplify_m", 100))
        simp = simp.to_crs(4326)[["ctx_name", "ctx_group", "geometry"]]
        round_geom_geojson(simp, out / fname)
        size_mb = (out / fname).stat().st_size / 1e6
        if size_mb > 15:
            log.warning("%s %.1f MB terlalu besar untuk peta; tidak ditampilkan", fname, size_mb)
            (out / fname).unlink()
            fname = None
        ctx_meta.append({"id": l["id"], "label": l["label"], "color": l.get("color", "#888"),
                         "bit": 1 << i, "ranked": l["ranked"], "file": fname})

    prov_disp = provinces.copy()
    prov_disp = prov_disp.rename(columns={"prov_i": "i", "prov_name": "name"})
    round_geom_geojson(prov_disp[["i", "name", "region", "geometry"]], out / "provinsi.geojson")

    latest = None
    if len(det):
        latest = det["t_wib"].max().strftime("%Y-%m-%d %H:%M")
    A.dump_json({
        "generated_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_wib": now_wib.strftime("%Y-%m-%d %H:%M"),
        "latest_detection_wib": latest,
        "fetch_mode": mode,
        "sources": status,
        "confidence_kept": ["nominal", "high"],
        "grid_deg": C.GRID_DEG,
        "provinces": [{"i": int(r.prov_i), "name": r.prov_name, "region": r.region}
                      for r in provinces.itertuples()],
        "regions": list(C.REGIONS),
        "kabupaten": [] if kabupaten is None else
                     [{"i": int(r.kab_i), "name": r.kab_name, "prov": int(r.prov_i)}
                      for r in kabupaten.itertuples()],
        "context_layers": ctx_meta,
        "stats": {"detections_kept": int(len(det)), "outside_indonesia": outside,
                  "low_dropped": int(sum(s.get("n_low", 0) for s in status)),
                  "unique_cells_7d": int(len(g))},
        "ref_label": C.REF_LABEL,
        "archive_final_dates": [d.isoformat() for d in final_dates],
    }, out / "summary.json", compact=False)

    A.dump_json(A.build_yoy(now_wib.year), out / "yoy.json")

    # ---------------- cuaca ----------------
    if not args.skip_weather:
        log.info("Mengambil data angin dan kualitas udara...")
        for name, fn, raw in (("angin.json", W.build_wind, "wind_raw.json"),
                              ("udara.json", W.build_air_quality, "aq_raw.json")):
            try:
                responses = None
                if args.weather_sample:
                    import json
                    responses = json.loads((args.weather_sample / raw).read_text())
                A.dump_json(fn(now_utc, responses), out / name)
            except Exception as e:  # noqa: BLE001
                log.error("%s gagal: %s", name, e)
                (out / name).unlink(missing_ok=True)

    # ---------------- aerosol Sentinel-5P ----------------
    if not args.skip_weather:
        try:
            meta = AE.build_aerosol(now_utc, out)
            if meta:
                A.dump_json(meta, out / "aerosol.json", compact=False)
            else:
                (out / "aerosol.json").unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001
            log.error("aerosol Sentinel-5P gagal: %s", e)
            (out / "aerosol.json").unlink(missing_ok=True)

    log.info("Selesai. File dashboard ada di %s", out)
    print("ARCHIVE_CHANGED=" + ("1" if changed else "0"))


if __name__ == "__main__":
    main()
