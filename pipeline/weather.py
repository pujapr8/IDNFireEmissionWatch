"""Angin (untuk animasi arah asap) dan estimasi PM2.5 kota dari Open-Meteo.
Kegagalan di sini tidak menghentikan pipeline; layer terkait hanya disembunyikan."""
from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd
import requests

from . import config as C

log = logging.getLogger("weather")


def _batched_get(url: str, coords: list[tuple[float, float]], params: dict,
                 batch: int = 100) -> list[dict]:
    out = []
    log.info("Open-Meteo: %d titik dalam %d permintaan...", len(coords), -(-len(coords) // batch))
    for k in range(0, len(coords), batch):
        part = coords[k:k + batch]
        p = dict(params)
        p["latitude"] = ",".join(f"{la:.3f}" for la, _ in part)
        p["longitude"] = ",".join(f"{lo:.3f}" for _, lo in part)
        r = requests.get(url, params=p, timeout=90)
        r.raise_for_status()
        js = r.json()
        out.extend(js if isinstance(js, list) else [js])
    return out


# ---------------------------------------------------------------------------
# Angin
# ---------------------------------------------------------------------------
def _grid():
    w, s, e, n = C.WIND_BBOX
    step = C.WIND_GRID_DEG
    lats = np.round(np.arange(n, s - 1e-9, -step), 3)
    lons = np.round(np.arange(w, e + 1e-9, step), 3)
    return lats, lons


def _uv(speed, direction):
    """Arah meteorologis (asal angin) -> komponen u (timur) dan v (utara)."""
    rad = math.radians(direction)
    return -speed * math.sin(rad), -speed * math.cos(rad)


def build_wind(now_utc: pd.Timestamp, responses: list[dict] | None = None) -> dict:
    lats, lons = _grid()
    coords = [(la, lo) for la in lats for lo in lons]
    level = C.WIND_LEVEL
    if responses is None:
        params = {"hourly": f"wind_speed_{level},wind_direction_{level}",
                  "wind_speed_unit": "ms", "forecast_days": 3, "timezone": "GMT"}
        responses = _batched_get(C.OPEN_METEO_FORECAST, coords, params)
        nulls = sum(v is None for r in responses
                    for v in r["hourly"][f"wind_speed_{level}"][:24])
        if nulls > 0.2 * 24 * len(responses):
            log.warning("angin %s banyak kosong, pakai 10 m", level)
            level = "10m"
            params["hourly"] = "wind_speed_10m,wind_direction_10m"
            responses = _batched_get(C.OPEN_METEO_FORECAST, coords, params)
    times = pd.to_datetime(responses[0]["hourly"]["time"], utc=True)
    base = now_utc.floor("h")
    frames = []
    for h in C.WIND_FRAMES_H:
        t = base + pd.Timedelta(hours=h)
        idx = int(np.argmin(np.abs((times - t).total_seconds())))
        u, v = [], []
        for r in responses:
            sp = r["hourly"][f"wind_speed_{level}"][idx]
            dr = r["hourly"][f"wind_direction_{level}"][idx]
            if sp is None or dr is None:
                uu, vv = 0.0, 0.0
            else:
                uu, vv = _uv(sp, dr)
            u.append(round(uu, 2))
            v.append(round(vv, 2))
        hdr = {"parameterCategory": 2, "parameterUnit": "m.s-1",
               "lo1": float(lons[0]), "lo2": float(lons[-1]),
               "la1": float(lats[0]), "la2": float(lats[-1]),
               "dx": C.WIND_GRID_DEG, "dy": C.WIND_GRID_DEG,
               "nx": len(lons), "ny": len(lats),
               "refTime": times[idx].isoformat()}
        frames.append({
            "offset_h": h,
            "time_utc": times[idx].isoformat(),
            "time_wib": times[idx].tz_convert(C.TIMEZONE).strftime("%Y-%m-%d %H:%M"),
            "data": [{"header": {**hdr, "parameterNumber": 2,
                                 "parameterNumberName": "eastward_wind"}, "data": u},
                     {"header": {**hdr, "parameterNumber": 3,
                                 "parameterNumberName": "northward_wind"}, "data": v}],
        })
    label = "925 hPa (~750 m)" if level == "925hPa" else "10 m"
    return {"source": "Open-Meteo (model cuaca global)", "level": label, "frames": frames}


# ---------------------------------------------------------------------------
# PM2.5 kota
# ---------------------------------------------------------------------------
def ispu_category(pm25: float | None) -> str | None:
    if pm25 is None or (isinstance(pm25, float) and math.isnan(pm25)):
        return None
    for limit, name in C.ISPU_PM25:
        if pm25 <= limit:
            return name
    return C.ISPU_PM25[-1][1]


def build_air_quality(now_utc: pd.Timestamp, responses: list[dict] | None = None) -> dict:
    coords = [(la, lo) for _, la, lo in C.CITIES]
    if responses is None:
        params = {"hourly": "pm2_5", "past_days": 1, "forecast_days": 2, "timezone": "GMT"}
        responses = _batched_get(C.OPEN_METEO_AQ, coords, params)
    cities = []
    for (name, la, lo), r in zip(C.CITIES, responses):
        times = pd.to_datetime(r["hourly"]["time"], utc=True)
        vals = pd.Series(r["hourly"]["pm2_5"], index=times, dtype="float")
        past = vals[vals.index <= now_utc]
        future = vals[(vals.index > now_utc) & (vals.index <= now_utc + pd.Timedelta(hours=24))]
        now_v = past.dropna().iloc[-1] if past.notna().any() else None
        last24 = past.iloc[-24:]
        mean24 = float(last24.mean()) if last24.notna().sum() >= 18 else None
        fmax = float(future.max()) if future.notna().any() else None
        cities.append({
            "name": name, "lat": la, "lon": lo,
            "pm25_now": None if now_v is None else round(float(now_v), 1),
            "pm25_24h": None if mean24 is None else round(mean24, 1),
            "pm25_max_next24": None if fmax is None else round(fmax, 1),
            "category": ispu_category(mean24),
        })
    return {"source": "Model CAMS (Copernicus) via Open-Meteo",
            "note": "Estimasi model, bukan hasil alat ukur. Kategori memakai ambang "
                    "ISPU PM2.5 terhadap rata-rata 24 jam.",
            "updated_utc": now_utc.isoformat(), "cities": cities}
