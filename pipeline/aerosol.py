"""Citra indeks aerosol UV Sentinel-5P (TROPOMI) untuk lapisan asap di peta.

Produk sama dengan lapisan "AER_AI_340_AND_380" di Copernicus Browser, diambil
lewat Sentinel Hub Process API di Copernicus Data Space Ecosystem (CDSE).
Pipeline meminta SATU gambar PNG transparan per pembaruan untuk seluruh
Indonesia, jadi berapa pun pengunjung dashboard, kuota CDSE tidak bertambah.

Butuh akun CDSE gratis + OAuth client (lihat README), lalu set:
    CDSE_CLIENT_ID, CDSE_CLIENT_SECRET
Tanpa keduanya, lapisan ini dilewati tanpa error.
"""
from __future__ import annotations

import logging
import math
import os
import time

import pandas as pd
import requests

from . import config as C

log = logging.getLogger("aerosol")

TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"

# Hanya aerosol penyerap (asap, debu) yang disimpan: AI < min transparan.
# Gambar berisi nilai indeks yang dikodekan ke 1..255 (lihat
# config.AEROSOL_ENCODING); dashboard yang mewarnainya. Gaya ES5 agar aman di
# mesin evalscript.
EVALSCRIPT = """//VERSION=3
function setup() {
  return { input: ["AER_AI_340_380", "dataMask"], output: { bands: 4, sampleType: "UINT8" } };
}
var MIN = __MIN__, MAX = __MAX__;
function evaluatePixel(s) {
  var v = s.AER_AI_340_380;
  if (!s.dataMask || v < MIN) return [0, 0, 0, 0];
  var c = 1 + Math.round((Math.min(v, MAX) - MIN) / (MAX - MIN) * 254);
  return [c, c, c, 255];
}
""".replace("__MIN__", repr(C.AEROSOL_ENCODING["min"])).replace("__MAX__", repr(C.AEROSOL_ENCODING["max"]))


def _merc(lon: float, lat: float) -> tuple[float, float]:
    r = 6378137.0
    x = math.radians(lon) * r
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * r
    return x, y


def _token(cid: str, secret: str) -> str:
    r = requests.post(TOKEN_URL, data={"grant_type": "client_credentials",
                                       "client_id": cid, "client_secret": secret},
                      headers={"content-type": "application/x-www-form-urlencoded"},
                      timeout=60)
    r.raise_for_status()
    return r.json()["access_token"]


def credentials() -> tuple[str, str] | None:
    cid = os.environ.get("CDSE_CLIENT_ID")
    secret = os.environ.get("CDSE_CLIENT_SECRET")
    return (cid, secret) if cid and secret else None


class Client:
    """Satu token untuk banyak permintaan; diperbarui otomatis bila kedaluwarsa."""

    def __init__(self, cid: str, secret: str):
        self.cid, self.secret = cid, secret
        self.token = None
        self.t_token = 0.0

    def _auth(self, force=False):
        if force or not self.token or time.time() - self.t_token > 240:
            self.token = _token(self.cid, self.secret)
            self.t_token = time.time()
        return {"Authorization": f"Bearer {self.token}", "Accept": "image/png"}

    def png(self, t_from: pd.Timestamp, t_to: pd.Timestamp, timeliness: str | None) -> bytes:
        w, s, e, n = C.AEROSOL_BBOX
        x1, y1 = _merc(w, s)
        x2, y2 = _merc(e, n)
        width = C.AEROSOL_WIDTH_PX
        height = round(width * (y2 - y1) / (x2 - x1))
        data_filter = {
            "timeRange": {"from": t_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
                          "to": t_to.strftime("%Y-%m-%dT%H:%M:%SZ")},
            "mosaickingOrder": "mostRecent",
        }
        if timeliness:
            data_filter["timeliness"] = timeliness
        body = {
            "input": {
                # Dalam Web Mercator supaya pas ditumpuk di peta tanpa distorsi
                "bounds": {"bbox": [x1, y1, x2, y2],
                           "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/3857"}},
                "data": [{"type": "sentinel-5p-l2", "dataFilter": data_filter,
                          "processing": {"upsampling": "BILINEAR"}}],
            },
            "output": {"width": width, "height": height,
                       "responses": [{"identifier": "default", "format": {"type": "image/png"}}]},
            "evalscript": EVALSCRIPT,
        }
        for attempt in range(3):
            r = requests.post(PROCESS_URL, json=body, timeout=180, headers=self._auth(force=attempt > 0))
            if r.status_code == 200 and r.content.startswith(b"\x89PNG"):
                return r.content
            if r.status_code == 429:  # terlalu cepat; tunggu sebentar
                time.sleep(10 * (attempt + 1))
                continue
            if r.status_code != 401:
                break
        raise RuntimeError(f"Process API HTTP {r.status_code}: {r.text[:300]}")


def build_aerosol(now_utc: pd.Timestamp, out_dir) -> dict | None:
    """Citra 24 jam terakhir untuk tampilan terkini."""
    cred = credentials()
    if not cred:
        log.info("CDSE_CLIENT_ID/CDSE_CLIENT_SECRET belum diisi; lapisan aerosol Sentinel-5P dilewati")
        return None
    w, s, e, n = C.AEROSOL_BBOX
    t_to = now_utc.floor("min")
    t_from = t_to - pd.Timedelta(hours=24)
    log.info("Sentinel-5P aerosol: meminta citra 24 jam terakhir...")
    content = Client(*cred).png(t_from, t_to, "NRTI")
    (out_dir / "aerosol.png").write_bytes(content)
    log.info("Sentinel-5P aerosol: tersimpan (%d KB)", len(content) // 1024)
    return {
        "file": "aerosol.png",
        "bounds": [[s, w], [n, e]],
        "from_utc": t_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to_utc": t_to.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "product": "Sentinel-5P TROPOMI, indeks aerosol UV 340/380 nm (NRTI)",
        "source": "Copernicus Data Space Ecosystem",
        "encoding": C.AEROSOL_ENCODING,
    }


def archive_days(dates, client: Client | None = None, overwrite: bool = False) -> list[str]:
    """Simpan citra aerosol per tanggal WIB (00.00–24.00 WIB) ke arsip.
    Memakai produk terbaik yang tersedia (RPRO/OFFL/NRTI)."""
    from .harian import aerosol_path  # hindari impor melingkar
    cred = credentials()
    if not cred:
        return []
    client = client or Client(*cred)
    saved = []
    for d in dates:
        path = aerosol_path(d)
        if path.exists() and not overwrite:
            continue
        t_from = pd.Timestamp(d.isoformat(), tz=C.TIMEZONE).tz_convert("UTC")
        t_to = t_from + pd.Timedelta(hours=24) - pd.Timedelta(seconds=1)
        try:
            content = client.png(t_from, t_to, None)
        except Exception as e:  # noqa: BLE001
            log.warning("aerosol %s gagal: %s", d, e)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        saved.append(str(path.relative_to(C.ROOT)))
        log.info("aerosol %s tersimpan (%d KB)", d, len(content) // 1024)
    return saved
