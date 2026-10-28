"""Titik api per tahun di tiap kawasan bernama (mis. taman nasional), dihitung
dengan cara yang sama dengan angka di dashboard mode Riwayat: tiap lokasi
(sel ~400 m) dihitung sekali dalam periode, walau menyala berhari-hari atau
di beberapa bulan.

    python -m pipeline.tahunan_konteks          # perbarui (hanya tahun yang berubah)
    python -m pipeline.tahunan_konteks --ulang  # hitung ulang semua tahun

Sumber: arsip harian data/archive/harian/<tahun>/<tanggal>.json.
Hasil, satu file per lapisan bernama: data/archive/harian/tahunan_<id>.json

    names, groups   nama kawasan dan grupnya (mis. provinsi)
    years[tahun]    first, last   tanggal arsip pertama & terakhir tahun itu
                    days          jumlah hari berarsip
                    n[sat][conf]  {nama: [akumulasi akhir Jan, akhir Feb, ...]}
                                  sat  = "all" (semua satelit) | "ref" (hanya titik
                                         yang terdeteksi S-NPP)
                                  conf = "nh" (nominal + tinggi) | "h" (tinggi saja,
                                         menurut satelit mana pun)
                                  Nilai bulan ke-m = lokasi unik 1 Jan s.d. akhir
                                  bulan m; nilai terakhir = total setahun (atau
                                  s.d. tanggal terakhir untuk tahun berjalan).

Aturan sama dengan index.html (buildHistH + selectRows + dedup): baris dengan
provinsi tak dikenal dilewati; tiap sel diwakili titik dengan FRP terbesar
(urut waktu, ganti bila !(frp_baru <= frp_lama)) dan dihitung di kawasan
tempat titik wakil itu berada.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
from array import array
from collections import defaultdict
from datetime import date

from . import config as C
from . import harian as HR

log = logging.getLogger("tahunan_konteks")
VERSION = 1
PREFIX = "tahunan_"
COMBOS = (("all", "nh"), ("all", "h"), ("ref", "nh"), ("ref", "h"))


def out_path(layer_id: str):
    return HR.DAY_DIR / f"{PREFIX}{layer_id}.json"


def _year_counts(files, prov_ok: set, layer_id: str, ref_bit: int):
    """Akumulasi lokasi unik per kawasan per akhir bulan untuk satu tahun."""
    cells, flags, frps, parks, months = array("q"), array("b"), array("d"), array("h"), array("b")
    names, name_idx, groups = [], {}, {}
    first = last = None
    for p in files:  # urut tanggal
        obj = json.loads(p.read_text(encoding="utf-8"))
        f = obj["fields"]
        hi, fi, si, pi, ci = f.index("high"), f.index("frp"), f.index("sats"), f.index("prov"), f.index("cell")
        prov_names = obj["prov_names"]
        cn = (obj.get("ctx_names") or {}).get(layer_id)
        if cn is None:
            raise RuntimeError(f"{p.name} belum ditandai lapisan {layer_id}")
        m = int(p.stem[5:7]) - 1
        first = first or p.stem
        last = p.stem
        for j, r in enumerate(obj["rows"]):
            pidx = r[pi]
            if pidx is None or pidx < 0 or pidx >= len(prov_names) or prov_names[pidx] not in prov_ok:
                continue  # dashboard melewati baris tanpa provinsi yang dikenal
            k = cn["idx"][j]
            if k is not None and k >= 0:
                nm = cn["names"][k]
                if nm not in name_idx:
                    name_idx[nm] = len(names)
                    names.append(nm)
                    groups[nm] = cn["groups"][k] if k < len(cn["groups"]) else ""
                pk = name_idx[nm]
            else:
                pk = -1
            frp = r[fi]
            cells.append(int(r[ci]))
            flags.append((1 if int(r[si]) & ref_bit else 0) | (2 if int(r[hi]) else 0))
            frps.append(math.nan if frp is None else float(frp))
            parks.append(pk)
            months.append(m)
    # Hanya sel yang pernah punya titik di dalam kawasan yang perlu diikuti
    relevant = {cells[i] for i in range(len(cells)) if parks[i] >= 0}
    reps = {c: {} for c in COMBOS}    # combo -> sel -> [frp, kawasan wakil]
    snaps = {c: [] for c in COMBOS}   # combo -> [ {kawasan: lokasi unik s.d. akhir bulan m} ]
    n_months = int(last[5:7]) if last else 0

    def snapshot():
        for c in COMBOS:
            cnt = defaultdict(int)
            for _, pk in reps[c].values():
                if pk >= 0:
                    cnt[pk] += 1
            snaps[c].append(cnt)

    cur = 0
    for i in range(len(cells)):
        while months[i] > cur:  # baris pertama bulan berikutnya: tutup bulan sebelumnya
            snapshot()
            cur += 1
        c_id = cells[i]
        if c_id not in relevant:
            continue
        fl, frp, pk = flags[i], frps[i], parks[i]
        for combo in COMBOS:
            sat, conf = combo
            if sat == "ref" and not fl & 1:
                continue
            if conf == "h" and not fl & 2:
                continue
            rp = reps[combo]
            e = rp.get(c_id)
            if e is None or not (frp <= e[0]):  # sama dengan dedup() di dashboard
                rp[c_id] = [frp, pk]
    while cur < n_months:
        snapshot()
        cur += 1
    n = {}
    for sat, conf in COMBOS:
        sn = snaps[(sat, conf)]
        pks = {pk for d in sn for pk in d}
        n.setdefault(sat, {})[conf] = {names[pk]: [d.get(pk, 0) for d in sn] for pk in sorted(pks)}
    return {"first": first, "last": last, "days": len(files), "n": n, "groups": groups}


def run(ctx_layers=None, provinces=None, rebuild: bool = False) -> list[str]:
    """Perbarui data/archive/harian/tahunan_<id>.json. Kembalikan file yang berubah."""
    from . import firms as F
    from .retag_konteks import signature
    if ctx_layers is None:
        ctx_layers = F.load_context_layers()
    if provinces is None:
        provinces = F.load_provinces()
    ranked = [l for l in ctx_layers if l["ranked"]]
    changed = []
    ids = {l["id"] for l in ranked}
    for p in HR.DAY_DIR.glob(f"{PREFIX}*.json"):  # lapisan yang sudah dihapus
        if p.stem[len(PREFIX):] not in ids:
            p.unlink()
            changed.append(str(p.relative_to(C.ROOT)))
    if not ranked:
        return changed
    prov_ok = set(provinces["prov_name"])
    ref_bit = C.SATELLITES[C.REF_SATELLITE]["bit"]
    sigs = {s["id"]: s["sha1"] for s in signature(ranked)}

    by_year = defaultdict(list)
    for p in sorted(HR.DAY_DIR.glob("*/*.json")):
        try:
            date.fromisoformat(p.stem)
        except ValueError:
            continue
        by_year[p.stem[:4]].append(p)
    file_hash = {}
    for files in by_year.values():
        for p in files:
            file_hash[p] = hashlib.sha1(p.read_bytes()).digest()

    for l in ranked:
        lid = l["id"]
        path = out_path(lid)
        old = {}
        if not rebuild:
            try:
                o = json.loads(path.read_text(encoding="utf-8"))
                if o.get("v") == VERSION and o.get("sig") == sigs[lid]:
                    old = o
            except (OSError, json.JSONDecodeError):
                pass
        groups = dict(zip(old.get("names", []), old.get("groups", [])))
        years, n_new = {}, 0
        for y in sorted(by_year):
            files = by_year[y]
            h = hashlib.sha1(f"v{VERSION}|{sigs[lid]}|{sorted(prov_ok)}".encode())
            for p in files:
                h.update(p.name.encode())
                h.update(file_hash[p])
            ysig = h.hexdigest()[:12]
            prev = old.get("years", {}).get(y)
            if prev and prev.get("sig") == ysig:
                years[y] = prev
                continue
            res = _year_counts(files, prov_ok, lid, ref_bit)
            groups.update(res.pop("groups"))
            res["sig"] = ysig
            years[y] = res
            n_new += 1
        names = sorted({nm for yv in years.values() for s in yv["n"].values() for d in s.values() for nm in d})
        obj = {"v": VERSION, "layer": lid, "label": l["label"], "sig": sigs[lid],
               "names": names, "groups": [groups.get(nm, "") for nm in names], "years": years}
        text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
            changed.append(str(path.relative_to(C.ROOT)))
        log.info("tahunan %s: %d tahun, %d dihitung ulang, %d kawasan bertitik api",
                 lid, len(years), n_new, len(names))
    return changed


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ulang", action="store_true", help="hitung ulang semua tahun")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run(rebuild=args.ulang)


if __name__ == "__main__":
    main()
