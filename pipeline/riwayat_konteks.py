"""Riwayat titik api harian per kawasan bernama (mis. tiap taman nasional),
untuk grafik riwayat di dashboard.

    python -m pipeline.riwayat_konteks          # perbarui (hanya hari yang berubah)
    python -m pipeline.riwayat_konteks --ulang  # hitung ulang semua hari

Sumber: arsip harian data/archive/harian/<tahun>/<tanggal>.json, yang sudah
ditandai nama kawasan (lihat retag_konteks). Hasilnya satu file per lapisan
konteks bernama, data/archive/harian/riwayat_<id>.json:

    names, groups  nama kawasan dan grupnya (mis. provinsi)
    days           [tanggal, sidik file arsip, ref]; ref = 1 bila data satelit
                   referensi (S-NPP) hari itu ada dan tidak bolong, sehingga
                   sebanding antartahun; 0 bila tidak ada/bolong; 2 bila ada
                   tetapi arsipnya format lama (tanpa kolom hsats), sehingga
                   ref_nh benar tetapi ref_h tidak bisa dihitung (diisi 0)
    counts         [hari_i, kawasan_i, nh, h, ref_nh, ref_h], hanya yang > 0
                   nh, h          titik api unik semua satelit
                                  (nominal + tinggi / tinggi saja)
                   ref_nh, ref_h  titik yang terdeteksi satelit referensi

Hari yang tidak ada di "days" belum diarsipkan. Hari yang sidiknya sama
dengan sebelumnya tidak dibaca ulang, jadi pembaruan rutin cepat.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
from datetime import date

from . import config as C
from . import firms as F
from . import harian as HR

log = logging.getLogger("riwayat_konteks")
VERSION = 2
PREFIX = "riwayat_"


def out_path(layer_id: str):
    return HR.DAY_DIR / f"{PREFIX}{layer_id}.json"


def count_day(obj: dict, layer_id: str, ref_label: str, ref_bit: int):
    """Hitungan satu hari arsip per kawasan: ({nama: [nh, h, ref_nh, ref_h]},
    {nama: grup}, ref) dengan ref seperti di docstring modul. None bila hari
    itu belum ditandai lapisan ini."""
    cn = (obj.get("ctx_names") or {}).get(layer_id)
    if cn is None or layer_id not in (obj.get("ctx_layers") or []):
        return None
    f = obj["fields"]
    hi, si = f.index("high"), f.index("sats")
    hsi = f.index("hsats") if "hsats" in f else None
    counts, groups = {}, {}
    for r, k in zip(obj["rows"], cn["idx"]):
        if k < 0:
            continue
        nm = cn["names"][k]
        c = counts.get(nm)
        if c is None:
            c = counts[nm] = [0, 0, 0, 0]
            groups[nm] = cn["groups"][k] if k < len(cn["groups"]) else ""
        high = int(r[hi])
        c[0] += 1
        c[1] += high
        if int(r[si]) & ref_bit:
            c[2] += 1
            # hsats = satelit yang menilai titik ini berkeyakinan tinggi. Tanpa
            # hsats, "high" bisa berasal dari satelit lain, jadi tidak dihitung.
            if hsi is not None and int(r[hsi]) & ref_bit:
                c[3] += 1
    if ref_label not in HR.effective_sats_obj(obj):
        ref = 0
    else:
        ref = 1 if hsi is not None else 2
    return counts, groups, ref


def _load_old(layer_id: str, sig: str):
    """Hasil sebelumnya sebagai cache: {tanggal: (sidik, ref, {nama: hitungan})}, {nama: grup}."""
    path = out_path(layer_id)
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, {}
    if old.get("v") != VERSION or old.get("sig") != sig:
        return {}, {}
    names, groups = old["names"], old["groups"]
    cache = {d: (h, ref, {}) for d, h, ref in old["days"]}
    days = [d for d, _, _ in old["days"]]
    for di, ni, *vals in old["counts"]:
        cache[days[di]][2][names[ni]] = vals
    return cache, dict(zip(names, groups))


def run(ctx_layers=None, rebuild: bool = False) -> list[str]:
    """Perbarui data/archive/harian/riwayat_<id>.json untuk tiap lapisan konteks
    bernama. Kembalikan file yang berubah."""
    from .retag_konteks import signature
    ctx_layers = ctx_layers if ctx_layers is not None else F.load_context_layers()
    ranked = [l for l in ctx_layers if l["ranked"]]
    changed = []
    # Lapisan yang sudah dihapus dari config: buang riwayatnya
    ids = {l["id"] for l in ranked}
    for p in HR.DAY_DIR.glob(f"{PREFIX}*.json"):
        if p.stem[len(PREFIX):] not in ids:
            p.unlink()
            changed.append(str(p.relative_to(C.ROOT)))
    if not ranked:
        return changed

    sig = {s["id"]: s["sha1"] for s in signature(ranked)}
    ref_label, ref_bit = C.REF_LABEL, C.SATELLITES[C.REF_SATELLITE]["bit"]
    olds = {l["id"]: ({}, {}) if rebuild else _load_old(l["id"], sig[l["id"]]) for l in ranked}
    acc = {l["id"]: {"days": [], "counts": {}, "groups": dict(olds[l["id"]][1]), "skip": 0} for l in ranked}
    n_read = 0
    for p in sorted(HR.DAY_DIR.glob("*/*.json")):
        try:
            d = date.fromisoformat(p.stem).isoformat()
        except ValueError:
            continue
        data = p.read_bytes()
        h = hashlib.sha1(data).hexdigest()[:12]
        obj = None
        for l in ranked:
            lid, a = l["id"], acc[l["id"]]
            e = olds[lid][0].get(d)
            if e and e[0] == h:
                ref_ok, counts = e[1], e[2]
            else:
                if obj is None:
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        log.warning("%s rusak, dilewati", p.name)
                        break
                    n_read += 1
                res = count_day(obj, lid, ref_label, ref_bit)
                if res is None:
                    a["skip"] += 1
                    continue
                counts, groups, ref_ok = res
                a["groups"].update(groups)
            a["days"].append((d, h, int(ref_ok)))
            a["counts"][d] = counts

    for l in ranked:
        lid, a = l["id"], acc[l["id"]]
        if a["skip"]:
            log.warning("%s: %d hari arsip belum ditandai lapisan ini (jalankan "
                        "python -m pipeline.retag_konteks --paksa); dilewati", lid, a["skip"])
        names = sorted({nm for c in a["counts"].values() for nm in c})
        ni = {nm: i for i, nm in enumerate(names)}
        rows = []
        for di, (d, _, _) in enumerate(a["days"]):
            for nm, v in a["counts"][d].items():
                if v[0]:
                    rows.append([di, ni[nm], *v])
        rows.sort(key=lambda r: (r[1], r[0]))
        obj = {
            "v": VERSION,
            "layer": lid,
            "label": l["label"],
            "sig": sig[lid],
            "ref_label": ref_label,
            "fields": ["hari", "kawasan", "nh", "h", "ref_nh", "ref_h"],
            "names": names,
            "groups": [a["groups"].get(nm, "") for nm in names],
            "days": [list(x) for x in a["days"]],
            "counts": rows,
        }
        text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        path = out_path(lid)
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            changed.append(str(path.relative_to(C.ROOT)))
        log.info("riwayat %s: %d hari, %d kawasan bertitik api (%d hari dibaca ulang)",
                 lid, len(a["days"]), len(names), n_read)
    return changed


def no_ref_days() -> list[date]:
    """Tanggal arsip yang data satelit referensinya (S-NPP) tidak ada atau bolong,
    menurut riwayat yang sudah dibangun (lapisan bernama mana pun)."""
    out = set()
    for p in HR.DAY_DIR.glob(f"{PREFIX}*.json"):
        try:
            days = json.loads(p.read_text(encoding="utf-8"))["days"]
        except (OSError, json.JSONDecodeError, KeyError):
            continue
        out |= {date.fromisoformat(d) for d, _, ref in days if ref == 0}
    return sorted(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ulang", action="store_true", help="hitung ulang semua hari")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run(rebuild=args.ulang)


if __name__ == "__main__":
    main()
