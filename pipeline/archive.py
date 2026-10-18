"""Arsip permanen (di-commit ke repo): hitungan harian per provinsi dan
deteksi mentah harian. Juga membangun data perbandingan antartahun."""
from __future__ import annotations

import gzip
import io
import json
from datetime import date

import pandas as pd

from . import config as C
from .firms import sat_info

TOTAL_ROW = "__semua__"  # baris penanda: tanggal sudah diproses (meski nol)


def daily_counts(groups: pd.DataFrame, prov_names: list[str], dates) -> pd.DataFrame:
    """groups = hasil dedup_daily. Kembalikan baris date,provinsi,nh,h untuk
    tanggal-tanggal yang diminta, termasuk baris total per tanggal."""
    rows = []
    for d in sorted(dates):
        g = groups[groups["date_wib"] == d] if len(groups) else groups
        rows.append({"date": d.isoformat(), "provinsi": TOTAL_ROW,
                     "nh": int(len(g)), "h": int(g["h"].sum()) if len(g) else 0})
        if len(g):
            agg = g.groupby("prov_i").agg(nh=("cell", "size"), h=("h", "sum"))
            for pi, r in agg.iterrows():
                rows.append({"date": d.isoformat(), "provinsi": prov_names[int(pi)],
                             "nh": int(r.nh), "h": int(r.h)})
    return pd.DataFrame(rows, columns=["date", "provinsi", "nh", "h"])


def upsert_year_files(prefix: str, counts: pd.DataFrame) -> list[str]:
    """Tulis/perbarui data/archive/<prefix>_<tahun>.csv. Kembalikan file yang berubah."""
    changed = []
    if counts.empty:
        return changed
    C.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    counts = counts.copy()
    counts["year"] = counts["date"].str[:4]
    for year, new in counts.groupby("year"):
        path = C.ARCHIVE_DIR / f"{prefix}_{year}.csv"
        new = new.drop(columns="year")
        if path.exists():
            old = pd.read_csv(path, dtype={"date": str, "provinsi": str})
            old = old[~old["date"].isin(set(new["date"]))]
            merged = pd.concat([old, new], ignore_index=True)
        else:
            merged = new
        merged = merged.sort_values(["date", "provinsi"]).reset_index(drop=True)
        text = merged.to_csv(index=False)
        if not path.exists() or path.read_text() != text:
            path.write_text(text)
            changed.append(str(path.relative_to(C.ROOT)))
    return changed


def write_raw_day(d: date, det: pd.DataFrame, prov_names, kab_names, ctx_layers) -> str | None:
    """Simpan deteksi (sudah difilter n/h) satu tanggal WIB ke csv.gz yang
    deterministik (mtime=0) sehingga git tidak melihat perubahan bila isinya sama."""
    sub = det[det["date_wib"] == d].copy()
    out = pd.DataFrame({
        "waktu_wib": sub["t_wib"].dt.strftime("%Y-%m-%d %H:%M"),
        "lintang": sub["latitude"].round(5),
        "bujur": sub["longitude"].round(5),
        "satelit": sub["source"].map(lambda s: sat_info(s)["label"]),
        "confidence": sub["conf"].map({"n": "nominal", "h": "high"}),
        "frp_mw": sub["frp"].round(2),
        "siang_malam": sub["daynight"].map({"D": "siang", "N": "malam"}).fillna(""),
        "provinsi": sub["prov_i"].map(lambda i: prov_names[i] if i >= 0 else ""),
        "kabupaten_kota": sub["kab_i"].map(lambda i: kab_names[i] if i >= 0 else ""),
    })
    for layer in ctx_layers:
        col = f"ctxname_{layer['id']}"
        bit = 1 << ctx_layers.index(layer)
        inside = (sub["ctx"] & bit) > 0
        if layer["ranked"]:
            out[layer["id"]] = sub[col].where(inside, "")
        else:
            out[layer["id"]] = inside.map({True: "ya", False: ""})
    out = out.sort_values(["waktu_wib", "lintang", "bujur", "satelit"])
    path = C.ARCHIVE_DIR / "raw" / f"{d.year}" / f"{d.isoformat()}.csv.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, filename="") as gz:
        gz.write(out.to_csv(index=False).encode("utf-8"))
    data = buf.getvalue()
    if path.exists() and path.read_bytes() == data:
        return None
    path.write_bytes(data)
    return str(path.relative_to(C.ROOT))


def build_yoy(current_year: int) -> dict:
    """Seri harian satelit referensi per tahun, disejajarkan per tanggal-bulan
    (366 slot, 29 Feb kosong pada tahun biasa).

    series[tahun][nh|h][provinsi] = daftar 366 angka; kunci "__semua__" = nasional.
    null = tanggal belum/tidak diproses. Provinsi yang tidak tercantum pada
    tanggal yang sudah diproses berarti nol titik api."""
    labels = pd.date_range("2024-01-01", "2024-12-31").strftime("%m-%d").tolist()
    pos = {m: i for i, m in enumerate(labels)}
    series, processed_dates = {}, {}
    last_date = None
    for f in sorted(C.ARCHIVE_DIR.glob("ref_daily_*.csv")):
        year = int(f.stem.split("_")[-1])
        df = pd.read_csv(f, dtype={"date": str, "provinsi": str})
        if df.empty:
            continue
        processed = sorted(set(df.loc[df["provinsi"] == TOTAL_ROW, "date"]))
        processed_dates[str(year)] = [pos[d[5:]] for d in processed]
        ys = {"nh": {}, "h": {}}
        for key in ("nh", "h"):
            for name, sub in df.groupby("provinsi"):
                if sub[key].sum() == 0 and name != TOTAL_ROW:
                    continue
                arr = [None] * 366
                for i in processed_dates[str(year)]:
                    arr[i] = 0
                for d, v in zip(sub["date"], sub[key]):
                    arr[pos[d[5:]]] = int(v)
                ys[key][name] = arr
        series[str(year)] = ys
        if year == current_year and processed:
            last_date = processed[-1]
    # Seri tiga satelit untuk tahun berjalan (arsip daily_<tahun>.csv)
    multi = None
    f = C.ARCHIVE_DIR / f"daily_{current_year}.csv"
    if f.exists():
        df = pd.read_csv(f, dtype={"date": str, "provinsi": str})
        if len(df):
            processed = sorted(set(df.loc[df["provinsi"] == TOTAL_ROW, "date"]))
            multi = {"year": current_year, "last_date": processed[-1] if processed else None,
                     "nh": {}, "h": {}}
            for key in ("nh", "h"):
                for name, sub in df.groupby("provinsi"):
                    if sub[key].sum() == 0 and name != TOTAL_ROW:
                        continue
                    arr = [None] * 366
                    for d in processed:
                        arr[pos[d[5:]]] = 0
                    for d, v in zip(sub["date"], sub[key]):
                        arr[pos[d[5:]]] = int(v)
                    multi[key][name] = arr
    return {"ref_label": C.REF_LABEL, "current_year": current_year,
            "last_date": last_date, "days": labels, "total_key": TOTAL_ROW,
            "series": series, "multi": multi}


def dump_json(obj, path, compact=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=1)
