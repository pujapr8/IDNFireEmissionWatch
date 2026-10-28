"""Jumlah lokasi titik api per bulan, dihitung persis seperti angka di bagian
atas dashboard saat bulan itu dibuka di mode Riwayat: tiap lokasi (sel ~400 m)
dihitung sekali per bulan walau menyala berhari-hari.

    python -m pipeline.bulanan          # perbarui (hanya bulan yang berubah)
    python -m pipeline.bulanan --ulang  # hitung ulang semua bulan

Sumber: arsip harian data/archive/harian/<tahun>/<tanggal>.json.
Hasil: data/archive/harian/bulanan.json

    areas              "__semua__" (nasional), "r:<wilayah besar>", nama provinsi
    ctx[id]            nama kawasan per lapisan konteks bernama (mis. taman nasional)
    months[YYYY-MM]    sig    sidik file harian bulan itu (untuk pembaruan bertahap)
                       days   jumlah hari berarsip
                       all    semua satelit  {"nh": [per area], "h": [per area]}
                       ref    satelit referensi (S-NPP) saja; "h" = null bila ada
                              hari berformat lama (tanpa hsats) sehingga keyakinan
                              tinggi S-NPP belum bisa dihitung tepat
                       ctx    {id: {"all": {"nh": [per kawasan], "h": [...]}, "ref": {...}}}

Aturan yang sama dengan dashboard (selectRows + dedup di index.html):
- nh = semua titik (nominal + tinggi); h = titik berkeyakinan tinggi menurut
  satelit yang dipakai (semua satelit: kolom high; S-NPP: bit S-NPP di hsats).
- Satu lokasi = satu sel; dihitung sekali per bulan untuk tiap wilayah.
- Kawasan: tiap sel diwakili titik dengan FRP terbesar (sama dengan dedup di
  dashboard), lalu dihitung di kawasan tempat titik wakil itu berada.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
from collections import defaultdict
from datetime import date

from . import config as C
from . import harian as HR

log = logging.getLogger("bulanan")
VERSION = 1
OUT = HR.DAY_DIR / "bulanan.json"
TOTAL = "__semua__"
BASES = ("all", "ref")
KEYS = ("nh", "h")


def _month_counts(files, prov_region: dict, ranked_ids: list[str], ref_bit: int) -> dict:
    """Hitung satu bulan dari daftar file harian (urut tanggal)."""
    # sets[basis][key][area] = set sel
    sets = {b: {k: defaultdict(set) for k in KEYS} for b in BASES}
    # reps[basis][key][sel] = [frp, {layer: nama}] titik wakil (FRP terbesar)
    reps = {b: {k: {} for k in KEYS} for b in BASES}
    ref_h_ok = True
    for p in files:
        obj = json.loads(p.read_text(encoding="utf-8"))
        f = obj["fields"]
        hi, fi, si, pi, ci = f.index("high"), f.index("frp"), f.index("sats"), f.index("prov"), f.index("cell")
        hsi = f.index("hsats") if "hsats" in f else None
        if hsi is None and obj["rows"]:
            ref_h_ok = False
        prov_names = obj["prov_names"]
        ctxn = {lid: (obj.get("ctx_names") or {}).get(lid) for lid in ranked_ids}
        for j, r in enumerate(obj["rows"]):
            pidx = r[pi]
            if pidx is None or pidx < 0 or pidx >= len(prov_names):
                continue
            prov = prov_names[pidx]
            if prov not in prov_region:  # dashboard melewati provinsi yang tidak dikenal
                continue
            region = "r:" + prov_region[prov]
            cell, sats, high = r[ci], int(r[si]), int(r[hi])
            # sama dengan index.html: tanpa hsats, "tinggi" dianggap berlaku untuk semua satelitnya
            hs = int(r[hsi]) if hsi is not None else (sats if high else 0)
            frp = r[fi]
            frp = math.nan if frp is None else float(frp)
            names = None
            for b in BASES:
                if b == "ref" and not sats & ref_bit:
                    continue
                is_h = bool(high) if b == "all" else bool(hs & ref_bit)
                for k in KEYS:
                    if k == "h" and not is_h:
                        continue
                    s = sets[b][k]
                    s[TOTAL].add(cell)
                    s[region].add(cell)
                    s[prov].add(cell)
                    if ranked_ids:
                        if names is None:
                            names = {}
                            for lid, cn in ctxn.items():
                                if cn is not None:
                                    li = cn["idx"][j]
                                    if li is not None and li >= 0:
                                        names[lid] = cn["names"][li]
                        rp = reps[b][k]
                        cur = rp.get(cell)
                        # dedup di dashboard: ganti wakil bila !(frp_baru <= frp_lama)
                        if cur is None or not (frp <= cur[0]):
                            rp[cell] = [frp, names]
    out = {"days": len(files), "ref_h_ok": ref_h_ok,
           "n": {b: {k: {a: len(c) for a, c in sets[b][k].items()} for k in KEYS} for b in BASES},
           "ctx": {}}
    for lid in ranked_ids:
        out["ctx"][lid] = {b: {k: {} for k in KEYS} for b in BASES}
        for b in BASES:
            for k in KEYS:
                cnt = out["ctx"][lid][b][k]
                for _, nm in reps[b][k].values():
                    if nm and lid in nm:
                        cnt[nm[lid]] = cnt.get(nm[lid], 0) + 1
    return out


def _load_old() -> dict:
    """Bulan-bulan hasil sebelumnya dalam bentuk kamus (cache)."""
    try:
        old = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if old.get("v") != VERSION:
        return {}
    areas, ctx_names, res = old["areas"], old.get("ctx", {}), {}
    for ym, m in old["months"].items():
        e = {"sig": m["sig"], "days": m["days"], "ref_h_ok": m["ref"]["h"] is not None,
             "n": {}, "ctx": {}}
        for b in BASES:
            e["n"][b] = {}
            for k in KEYS:
                arr = m[b][k] if not (b == "ref" and k == "h" and m[b][k] is None) else m["ref_h_approx"]
                e["n"][b][k] = {a: v for a, v in zip(areas, arr) if v}
        for lid, cm in m.get("ctx", {}).items():
            nms = ctx_names.get(lid, [])
            e["ctx"][lid] = {b: {k: {n: v for n, v in zip(nms, cm[b][k] or []) if v} for k in KEYS} for b in BASES}
        res[ym] = e
    return res


def run(ctx_layers=None, provinces=None, rebuild: bool = False) -> list[str]:
    """Perbarui data/archive/harian/bulanan.json. Kembalikan file yang berubah."""
    if provinces is None:
        from . import firms as F
        provinces = F.load_provinces()
    if ctx_layers is None:
        from . import firms as F
        ctx_layers = F.load_context_layers()
    prov_region = {r.prov_name: r.region for r in provinces.itertuples()}
    ranked = [l["id"] for l in ctx_layers if l["ranked"]]
    from .retag_konteks import signature
    layer_sig = ";".join(f"{s['id']}:{s['sha1']}" for s in signature([l for l in ctx_layers if l["ranked"]]))
    ref_bit = C.SATELLITES[C.REF_SATELLITE]["bit"]

    by_month = defaultdict(list)
    for p in sorted(HR.DAY_DIR.glob("*/*.json")):
        try:
            d = date.fromisoformat(p.stem)
        except ValueError:
            continue
        by_month[d.strftime("%Y-%m")].append(p)

    old = {} if rebuild else _load_old()
    base_sig = f"v{VERSION}|{layer_sig}|{sorted(prov_region.items())}"
    months, n_new = {}, 0
    for ym in sorted(by_month):
        files = by_month[ym]
        h = hashlib.sha1(base_sig.encode())
        for p in files:
            h.update(p.name.encode())
            h.update(hashlib.sha1(p.read_bytes()).digest())
        sig = h.hexdigest()[:12]
        if ym in old and old[ym]["sig"] == sig:
            months[ym] = old[ym]
            continue
        m = _month_counts(files, prov_region, ranked, ref_bit)
        m["sig"] = sig
        months[ym] = m
        n_new += 1

    # Daftar wilayah & kawasan (urutan tetap supaya file stabil)
    regions = sorted({"r:" + r for r in prov_region.values()})
    areas = [TOTAL] + regions + sorted(prov_region)
    ctx_names = {lid: sorted({n for m in months.values() for b in BASES for k in KEYS
                              for n in m["ctx"].get(lid, {}).get(b, {}).get(k, {})}) for lid in ranked}
    out_months = {}
    for ym, m in months.items():
        o = {"sig": m["sig"], "days": m["days"]}
        for b in BASES:
            o[b] = {k: [m["n"][b][k].get(a, 0) for a in areas] for k in KEYS}
        if not m["ref_h_ok"]:
            # Keyakinan tinggi S-NPP belum tepat (arsip lama); simpan perkiraannya terpisah
            o["ref_h_approx"] = o["ref"]["h"]
            o["ref"] = {"nh": o["ref"]["nh"], "h": None}
        o["ctx"] = {}
        for lid in ranked:
            cm = m["ctx"].get(lid, {})
            o["ctx"][lid] = {b: {k: [cm.get(b, {}).get(k, {}).get(n, 0) for n in ctx_names[lid]] for k in KEYS} for b in BASES}
            if not m["ref_h_ok"]:
                o["ctx"][lid]["ref"]["h"] = None
        out_months[ym] = o
    obj = {"v": VERSION, "ref_label": C.REF_LABEL, "areas": areas, "ctx": ctx_names, "months": out_months}
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    if OUT.exists() and OUT.read_text(encoding="utf-8") == text:
        log.info("bulanan: tidak berubah (%d bulan)", len(months))
        return []
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    log.info("bulanan: %d bulan, %d dihitung ulang", len(months), n_new)
    return [str(OUT.relative_to(C.ROOT))]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ulang", action="store_true", help="hitung ulang semua bulan")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run(rebuild=args.ulang)


if __name__ == "__main__":
    main()
