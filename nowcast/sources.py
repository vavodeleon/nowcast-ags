"""Fuentes de datos. Todas públicas, gratuitas y sin API key.

- RainViewer  -> mosaico de radar (reflectividad compuesta), 10 min
- NASA GIBS   -> GOES-East ABI banda 13 (infrarrojo limpio 10.3 um), 10 min
                 esto es exactamente la imagen que revisas a mano
- Open-Meteo  -> modelos numericos (para contexto 3-12 h y para aprender
                 cual modelo acierta mas en Aguascalientes)
"""
from __future__ import annotations

import csv
import io
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
from PIL import Image

from . import config, http

log = logging.getLogger(__name__)


@dataclass
class Frame:
    """Un campo geolocalizado en un instante dado."""
    time: datetime          # UTC
    data: np.ndarray        # float32, NaN = sin dato
    km_per_px: float
    center_lat: float
    center_lon: float
    kind: str               # "radar" | "ir"
    # datos de la rejilla del satelite, para reproyectar sobre un mapa
    grid_meta: dict | None = None

    @property
    def center_px(self) -> tuple[float, float]:
        h, w = self.data.shape
        return (h / 2.0, w / 2.0)


# =====================================================================
# RainViewer: radar
# =====================================================================

_PALETTE_CACHE: tuple[np.ndarray, np.ndarray] | None = None

# Respaldo aproximado del esquema "Universal Blue" por si la tabla oficial
# no se puede descargar. Suficiente para no quedarnos ciegos.
_FALLBACK_PALETTE = [
    (0, 0, 246, 5.0), (0, 0, 200, 10.0), (0, 0, 150, 15.0),
    (0, 200, 0, 20.0), (0, 160, 0, 25.0), (0, 120, 0, 30.0),
    (255, 255, 0, 35.0), (230, 190, 0, 40.0), (255, 130, 0, 45.0),
    (255, 0, 0, 50.0), (200, 0, 0, 55.0), (150, 0, 0, 60.0),
    (255, 0, 255, 65.0), (150, 0, 180, 70.0),
]


def _load_palette() -> tuple[np.ndarray, np.ndarray]:
    """Devuelve (colores Nx3 uint8, dbz N,) del esquema Universal Blue."""
    global _PALETTE_CACHE
    if _PALETTE_CACHE is not None:
        return _PALETTE_CACHE

    text = None
    if os.path.exists(config.RV_COLORS_CACHE):
        with open(config.RV_COLORS_CACHE, "r", encoding="utf-8") as fh:
            text = fh.read()
    else:
        raw = http.get_bytes(config.RV_COLORS_CSV_URL)
        if raw:
            text = raw.decode("utf-8", errors="replace")
            os.makedirs(config.DATA_DIR, exist_ok=True)
            with open(config.RV_COLORS_CACHE, "w", encoding="utf-8") as fh:
                fh.write(text)

    colors: list[tuple[int, int, int]] = []
    dbz: list[float] = []

    if text:
        for row in csv.DictReader(io.StringIO(text)):
            keys = {k.lower().strip(): k for k in row if k}
            dkey = next((keys[k] for k in keys if "dbz" in k), None)
            ckey = next((keys[k] for k in keys
                         if "rain" in k or "color" in k), None)
            if not dkey or not ckey:
                continue
            try:
                value = float(str(row[dkey]).strip())
            except (TypeError, ValueError):
                continue
            hexs = str(row[ckey]).strip().lstrip("#")
            if len(hexs) < 6:
                continue
            try:
                rgb = (int(hexs[0:2], 16), int(hexs[2:4], 16), int(hexs[4:6], 16))
            except ValueError:
                continue
            colors.append(rgb)
            dbz.append(value)

    if not colors:
        log.warning("usando paleta de respaldo de RainViewer")
        for r, g, b, v in _FALLBACK_PALETTE:
            colors.append((r, g, b))
            dbz.append(v)

    _PALETTE_CACHE = (np.array(colors, dtype=np.int16),
                      np.array(dbz, dtype=np.float32))
    return _PALETTE_CACHE


def _decode_radar_png(raw: bytes) -> np.ndarray:
    """PNG de RainViewer -> matriz de dBZ (NaN donde no hay eco)."""
    img = Image.open(io.BytesIO(raw)).convert("RGBA")
    arr = np.asarray(img).astype(np.int16)
    rgb, alpha = arr[..., :3], arr[..., 3]

    palette, dbz_values = _load_palette()
    # distancia al cuadrado contra cada color de la paleta, vectorizado
    diff = rgb[:, :, None, :] - palette[None, None, :, :]
    idx = np.argmin(np.einsum("ijkl,ijkl->ijk", diff, diff), axis=2)

    out = dbz_values[idx].astype(np.float32)
    out[alpha < 40] = np.nan  # transparente = sin eco
    return out


def fetch_radar_frames(n: int = 4) -> list[Frame]:
    """Los ultimos n frames de radar, del mas antiguo al mas reciente."""
    meta = http.get_json("https://api.rainviewer.com/public/weather-maps.json")
    if not meta:
        log.error("RainViewer no respondio")
        return []

    host = meta.get("host", "https://tilecache.rainviewer.com")
    past = (meta.get("radar") or {}).get("past") or []
    if not past:
        log.error("RainViewer sin frames de radar")
        return []

    # metros por pixel en Web Mercator, corregido por latitud
    m_per_px = (156543.03392 * np.cos(np.radians(config.LAT))
                / (2 ** config.RV_ZOOM) * (256.0 / config.RV_SIZE))
    km_per_px = m_per_px / 1000.0

    frames: list[Frame] = []
    for entry in past[-n:]:
        url = (f"{host}{entry['path']}/{config.RV_SIZE}/{config.RV_ZOOM}"
               f"/{config.LAT:.6f}/{config.LON:.6f}"
               f"/{config.RV_COLOR_SCHEME}/{config.RV_SMOOTH}_{config.RV_SNOW}.png")
        raw = http.get_bytes(url)
        if not raw:
            continue
        try:
            data = _decode_radar_png(raw)
        except Exception as exc:  # imagen corrupta
            log.warning("no se pudo decodificar %s: %s", url, exc)
            continue
        frames.append(Frame(
            time=datetime.fromtimestamp(entry["time"], tz=timezone.utc),
            data=data, km_per_px=km_per_px,
            center_lat=config.LAT, center_lon=config.LON, kind="radar",
        ))

    log.info("radar: %s frames", len(frames))
    return frames


def radar_coverage() -> dict:
    """Cobertura de radar EN la ubicacion, no promediada sobre el dominio.

    Verificado sobre el terreno: Aguascalientes cae en un hueco de la red
    de radar mexicana. Promediando todo el dominio salia ~40% de cobertura,
    suficiente para pasar cualquier umbral razonable, cuando la cobertura
    real sobre la ciudad es cero. Medir el promedio era medir la cosa
    equivocada.

    Devuelve:
      home    - fraccion cubierta dentro de RADAR_HOME_RADIUS_KM (0-1)
      domain  - fraccion cubierta de toda la imagen, solo informativa
      usable  - True si el radar sirve para decir que pasa sobre la ciudad
    """
    url = (f"https://tilecache.rainviewer.com/v2/coverage/0/{config.RV_SIZE}"
           f"/{config.RV_ZOOM}/{config.LAT:.6f}/{config.LON:.6f}/0/0_0.png")
    raw = http.get_bytes(url)
    if not raw:
        return {"home": 0.0, "domain": 0.0, "usable": False}
    try:
        arr = np.asarray(Image.open(io.BytesIO(raw)).convert("RGBA"))
    except Exception:
        return {"home": 0.0, "domain": 0.0, "usable": False}

    # la mascara pinta de negro OPACO lo NO cubierto; transparente = cubierto
    covered = arr[..., 3] <= 40
    h, w = covered.shape
    cy, cx = h / 2.0, w / 2.0

    m_per_px = (156543.03392 * np.cos(np.radians(config.LAT))
                / (2 ** config.RV_ZOOM) * (256.0 / config.RV_SIZE))
    radius_px = max(2.0, config.RADAR_HOME_RADIUS_KM * 1000.0 / m_per_px)

    yy, xx = np.ogrid[0:h, 0:w]
    near = (yy - cy) ** 2 + (xx - cx) ** 2 <= radius_px ** 2

    home = float(covered[near].mean()) if near.any() else 0.0
    return {"home": round(home, 3),
            "domain": round(float(covered.mean()), 3),
            "usable": home >= 0.5}


# =====================================================================
# Infrarrojo: GOES-19 banda 13 (ver goes.py)
# =====================================================================

def fetch_ir_frames(n: int = 5) -> list[Frame]:
    """Ultimos n cuadros de GOES-19 banda 13 alrededor de la ciudad.

    Se probo primero NASA GIBS y resulto inservible: reporta que la capa
    existe y tiene datos, pero no entrega un solo tile por WMS ni por WMTS.
    El bucket de NOAA en S3 ademas da temperaturas calibradas de verdad,
    cada 5 minutos y con ~4 de retraso.
    """
    from . import goes
    return goes.fetch_ir_frames(n)


# =====================================================================
# Open-Meteo: modelos numericos
# =====================================================================

def fetch_models() -> dict:
    """Probabilidad de precipitacion e indices convectivos por modelo."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={config.LAT:.4f}&longitude={config.LON:.4f}"
        "&hourly=precipitation_probability,precipitation,cape"
        "&minutely_15=precipitation"
        "&current=precipitation,temperature_2m,relative_humidity_2m,"
        "cloud_cover,wind_speed_10m,wind_direction_10m"
        "&past_hours=6&forecast_hours=12"
        "&timezone=UTC"
        f"&models={','.join(config.OPENMETEO_MODELS)}"
    )
    data = http.get_json(url)
    if not data:
        log.error("Open-Meteo no respondio")
        return {}
    return data


def fetch_temperature() -> dict:
    """Temperatura actual, sensacion termica y curva de las proximas 24 h."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={config.LAT:.4f}&longitude={config.LON:.4f}"
        "&current=temperature_2m,apparent_temperature,relative_humidity_2m"
        "&hourly=temperature_2m,apparent_temperature"
        "&daily=temperature_2m_max,temperature_2m_min"
        "&forecast_days=2&forecast_hours=25"
        f"&timezone={config.TZ.key}"
    )
    data = http.get_json(url)
    if not data:
        log.error("Open-Meteo no respondio (temperatura)")
        return {}

    cur = data.get("current") or {}
    hourly = data.get("hourly") or {}
    daily = data.get("daily") or {}

    serie = []
    horas = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    sens = hourly.get("apparent_temperature") or []
    for i, ts in enumerate(horas[:25]):
        if i >= len(temps) or temps[i] is None:
            continue
        serie.append({
            "t": ts[11:16],
            "temp": round(float(temps[i]), 1),
            "sensacion": (round(float(sens[i]), 1)
                          if i < len(sens) and sens[i] is not None else None),
        })

    def _primero(clave):
        vals = daily.get(clave) or []
        return round(float(vals[0]), 1) if vals and vals[0] is not None else None

    return {
        "ahora": (round(float(cur["temperature_2m"]), 1)
                  if cur.get("temperature_2m") is not None else None),
        "sensacion": (round(float(cur["apparent_temperature"]), 1)
                      if cur.get("apparent_temperature") is not None else None),
        "humedad": (round(float(cur["relative_humidity_2m"]))
                    if cur.get("relative_humidity_2m") is not None else None),
        "maxima": _primero("temperature_2m_max"),
        "minima": _primero("temperature_2m_min"),
        "serie": serie,
    }


def fetch_observed_precip(hours_back: int = 6) -> dict:
    """Precipitacion observada reciente (best-match) para la verificacion."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={config.LAT:.4f}&longitude={config.LON:.4f}"
        "&hourly=precipitation"
        f"&past_hours={hours_back}&forecast_hours=1"
        "&timezone=UTC"
    )
    return http.get_json(url) or {}
