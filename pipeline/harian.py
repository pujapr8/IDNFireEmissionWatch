"""Arsip harian untuk mode Riwayat di dashboard.

Satu file JSON per tanggal (WIB) di data/archive/harian/<tahun>/<tanggal>.json
berisi titik api unik hari itu (sudah difilter nominal/high, dideduplikasi
lintas satelit per sel ~400 m). Plus indeks tanggal di
data/archive/harian/indeks.json untuk slider di dashboard.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time

import pandas as pd

from . import config as C

DAY_DIR = C.ARCHIVE_DIR / "harian"
AEROSOL_DIR = C.ARCHIVE_DIR / C.AEROSOL_ARCHIVE_DIRNAME
OLD_AEROSOL_DIR = C.ARCHIVE_DIR / "aerosol"  # format lama (warna tertanam), dihapus otomatis
FIELDS = ["lon", "lat", "t_first", "t_last", "high", "frp", "sats", "n_det",
          "prov", "kab", "ctx", "cell", "hsats"]


def day_path(d: date):
    return DAY_DIR / f"{d.year}" / f"{d.isoformat()}.json"


def aerosol_path(d: date):
    return AEROSOL_DIR / f"{d.year}" / f"{d.isoformat()}.png"


def day_sats(d: date) -> list[str] | None:
    """Label satelit yang tercatat di arsip harian tanggal d, atau None bila
    belum ada arsipnya. Cukup membaca awal file ("sats" ditulis di depan)."""
    path = day_path(d)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        head = fh.read(400)
    i = head.find('"sats":[')
    if i < 0:  # format tak terduga: baca utuh
        return json.loads(path.read_text(encoding="utf-8")).get("sats", [])
    j = head.find("]", i)
    return json.loads(head[i + 7:j + 1])


def has_hsats(d: date) -> bool:
    """Apakah arsip harian d sudah memuat kolom hsats (satelit yang menilai titik
    berkeyakinan tinggi). Arsip lama tanpa kolom ini tidak bisa memisahkan
    "keyakinan tinggi" per satelit. Cukup membaca awal file."""
    path = day_path(d)
    if not path.exists():
        return False
    with open(path, encoding="utf-8") as fh:
        head = fh.read(1000)
    i = head.find('"fields":[')
    if i < 0:
        return "hsats" in json.loads(path.read_text(encoding="utf-8")).get("fields", [])
    return '"hsats"' in head[i:head.find("]", i)]


def effective_sats(d: date) -> list[str] | None:
    """Satelit yang BENAR-BENAR punya data di arsip harian d. Satelit yang
    tercatat tetapi datanya kosong/bolong dibanding satelit lain
    (lihat config.EMPTY_SAT_*) tidak ikut dihitung.
    None bila arsip hari itu belum ada."""
    path = day_path(d)
    if not path.exists():
        return None
    return effective_sats_obj(json.loads(path.read_text(encoding="utf-8")))


def effective_sats_obj(obj: dict) -> list[str]:
    """Seperti effective_sats, untuk isi arsip harian yang sudah dibaca."""
    listed = obj.get("sats", [])
    si = obj["fields"].index("sats")
    by_label = {v["label"]: v["bit"] for v in C.SATELLITES.values()}
    n = {lbl: 0 for lbl in listed}
    for row in obj.get("rows", []):
        bits = int(row[si])
        for lbl in listed:
            if bits & by_label.get(lbl, 0):
                n[lbl] += 1
    if len(listed) < 2:
        return listed
    from .archive import is_hollow
    return [lbl for lbl in listed
            if not is_hollow(n[lbl], max(n[o] for o in listed if o != lbl))]


def write_day_json(d: date, groups: pd.DataFrame, sats: list[str], prov_names: list[str],
                   kab_names: list[str], ctx_layers: list[dict]) -> str | None:
    """Tulis titik unik satu tanggal. Waktu disimpan sebagai menit sejak
    00.00 WIB tanggal itu. Nama kabupaten & konsesi disimpan per file supaya
    file lama tetap terbaca walau batas wilayah berganti."""
    g = groups[groups["date_wib"] == d] if len(groups) else groups
    day0 = pd.Timestamp(datetime.combine(d, time()), tz=C.TIMEZONE)
    ranked = [l for l in ctx_layers if l["ranked"]]
    kab_local: dict[str, int] = {}
    ctx_local = {l["id"]: {"names": [], "groups": [], "idx": [], "_pos": {}} for l in ranked}
    rows = []
    if len(g):
        g = g.sort_values("cell")
        for r in g.itertuples(index=False):
            kab = -1
            if r.kab_i >= 0 and kab_names:
                nm = kab_names[int(r.kab_i)]
                kab = kab_local.setdefault(nm, len(kab_local))
            rows.append([
                round(float(r.longitude), 4), round(float(r.latitude), 4),
                int((r.t_first - day0).total_seconds() // 60),
                int((r.t_last - day0).total_seconds() // 60),
                int(r.h), None if pd.isna(r.frp) else round(float(r.frp), 1),
                int(r.sats), int(r.n_det), int(r.prov_i), kab, int(r.ctx), int(r.cell),
                int(r.hsats),
            ])
            for l in ranked:
                c = ctx_local[l["id"]]
                nm = getattr(r, f"ctxname_{l['id']}", "") or ""
                gr = getattr(r, f"ctxgroup_{l['id']}", "") or ""
                if nm:
                    key = (nm, gr)
                    if key not in c["_pos"]:
                        c["_pos"][key] = len(c["names"])
                        c["names"].append(nm)
                        c["groups"].append(gr)
                    c["idx"].append(c["_pos"][key])
                else:
                    c["idx"].append(-1)
    for c in ctx_local.values():
        c.pop("_pos")
    obj = {
        "date": d.isoformat(),
        "sats": sats,
        "fields": FIELDS,
        "prov_names": prov_names,
        "kab_names": list(kab_local),
        "ctx_layers": [l["id"] for l in ctx_layers],  # urutan = bit
        "ctx_names": ctx_local,
        "total_nh": len(rows),
        "total_h": sum(r[4] for r in rows),
        "rows": rows,
    }
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    path = day_path(d)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return None
    path.write_text(text, encoding="utf-8")
    return str(path.relative_to(C.ROOT))


def missing_aerosol_days() -> list[date]:
    """Tanggal yang punya arsip harian tetapi belum punya citra aerosol, terbaru dulu."""
    out = []
    for f in DAY_DIR.glob("*/*.json"):
        try:
            d = date.fromisoformat(f.stem)
        except ValueError:
            continue
        if d.isoformat() >= C.AEROSOL_START and not aerosol_path(d).exists():
            out.append(d)
    return sorted(out, reverse=True)


def build_index() -> dict:
    """Daftar tanggal yang punya arsip harian, untuk slider dashboard."""
    entries = []
    for f in sorted(DAY_DIR.glob("*/*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                head = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        d = date.fromisoformat(head["date"])
        entries.append({"d": head["date"], "nh": head["total_nh"], "h": head["total_h"],
                        "sat": len(head.get("sats", [])),
                        "aer": aerosol_path(d).exists()})
    idx = {
        "first": entries[0]["d"] if entries else None,
        "last": entries[-1]["d"] if entries else None,
        "aerosol_bounds": [[C.AEROSOL_BBOX[1], C.AEROSOL_BBOX[0]],
                           [C.AEROSOL_BBOX[3], C.AEROSOL_BBOX[2]]],
        "aerosol_dir": C.AEROSOL_ARCHIVE_DIRNAME,
        "aerosol_encoding": C.AEROSOL_ENCODING,
        "days": entries,
    }
    text = json.dumps(idx, ensure_ascii=False, separators=(",", ":"))
    path = DAY_DIR / "indeks.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
    return idx
