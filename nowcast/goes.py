"""GOES-19 banda 13 (infrarrojo limpio, 10.3 um) desde el bucket publico de NOAA.

Esta es la fuente principal del sistema, no el respaldo. Aguascalientes cae
en un hueco de la red de radar mexicana, asi que el satelite es lo unico que
ve tu ubicacion de verdad. Es exactamente la imagen que revisas a mano.

Ventajas sobre raspar PNGs de un servidor de mapas:
  - temperatura de brillo calibrada en Kelvin, no un color del que hay que
    adivinar la temperatura
  - cada 5 minutos (sector CONUS) en vez de 10
  - ~4 minutos de retraso en vez de ~17
  - los cuadros comparten exactamente la misma rejilla fija, asi que quedan
    co-registrados sin remuestrear nada

Sin credenciales: el bucket es de AWS Open Data y se lista por HTTPS.
"""
from __future__ import annotations

import io
import logging
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import h5py
import numpy as np

from . import config, http

log = logging.getLogger(__name__)

BUCKET = "https://noaa-goes19.s3.amazonaws.com"
S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"

# CONUS: cada 5 min, ~13 MB. Full Disk: cada 10 min, mas pesado.
SECTORS = [("ABI-L2-CMIPC", "C", 5), ("ABI-L2-CMIPF", "F", 10)]

# OR_ABI-L2-CMIPC-M6C13_G19_s20262162201171_e...
_KEY_RE = re.compile(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})")


def _list_keys(prefix: str, band: str = "C13") -> list[tuple[datetime, str]]:
    """Lista objetos del bucket bajo un prefijo, filtrando por banda."""
    url = f"{BUCKET}/?list-type=2&max-keys=400&prefix={prefix}"
    raw = http.get_bytes(url)
    if not raw:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        log.warning("listado S3 ilegible: %s", exc)
        return []

    out: list[tuple[datetime, str]] = []
    for node in root.findall(f"{S3_NS}Contents"):
        key_node = node.find(f"{S3_NS}Key")
        if key_node is None or not key_node.text:
            continue
        key = key_node.text
        if band not in key:
            continue
        m = _KEY_RE.search(key)
        if not m:
            continue
        year, doy, hh, mm, ss = (int(g) for g in m.groups())
        t = (datetime(year, 1, 1, tzinfo=timezone.utc)
             + timedelta(days=doy - 1, hours=hh, minutes=mm, seconds=ss))
        out.append((t, key))
    out.sort()
    return out


def recent_keys(n: int, sector_prefix: str, step_min: int) -> list[tuple[datetime, str]]:
    """Las n imagenes mas recientes disponibles, de la mas antigua a la mas nueva."""
    now = datetime.now(timezone.utc)
    found: list[tuple[datetime, str]] = []
    # revisamos la hora actual y la anterior (y la previa si hace falta)
    for back in range(3):
        stamp = now - timedelta(hours=back)
        prefix = (f"{sector_prefix}/{stamp.year}/{stamp.timetuple().tm_yday:03d}"
                  f"/{stamp.hour:02d}/")
        found = _list_keys(prefix) + found
        if len(found) >= n + 2:
            break
    found.sort()
    return found[-n:] if found else []


# ------------------------------------------------------------------ geometria

class FixedGrid:
    """Proyeccion de la rejilla fija del satelite geoestacionario."""

    def __init__(self, proj_attrs: dict):
        self.r_eq = float(proj_attrs["semi_major_axis"])
        self.r_pol = float(proj_attrs["semi_minor_axis"])
        self.H = float(proj_attrs["perspective_point_height"]) + self.r_eq
        self.lon0 = math.radians(float(proj_attrs["longitude_of_projection_origin"]))
        self._ratio = (self.r_eq ** 2) / (self.r_pol ** 2)

    def lonlat_to_scan(self, lat_deg: float, lon_deg: float) -> tuple[float, float] | None:
        """(lat, lon) -> angulos de barrido (x, y). None si no es visible."""
        lat, lon = math.radians(lat_deg), math.radians(lon_deg)
        lat_c = math.atan(((self.r_pol ** 2) / (self.r_eq ** 2)) * math.tan(lat))
        e2 = (self.r_eq ** 2 - self.r_pol ** 2) / (self.r_eq ** 2)
        r_c = self.r_pol / math.sqrt(1.0 - e2 * (math.cos(lat_c) ** 2))

        sx = self.H - r_c * math.cos(lat_c) * math.cos(lon - self.lon0)
        sy = -r_c * math.cos(lat_c) * math.sin(lon - self.lon0)
        sz = r_c * math.sin(lat_c)

        # el punto debe estar del lado visible del globo
        if self.H * (self.H - sx) < sy ** 2 + self._ratio * sz ** 2:
            return None

        y = math.atan(sz / sx)
        x = math.asin(-sy / math.sqrt(sx ** 2 + sy ** 2 + sz ** 2))
        return x, y

    def scan_to_lonlat(self, x: float, y: float) -> tuple[float, float] | None:
        """Angulos de barrido -> (lat, lon) en grados."""
        sin_x, cos_x = math.sin(x), math.cos(x)
        sin_y, cos_y = math.sin(y), math.cos(y)

        a = sin_x ** 2 + cos_x ** 2 * (cos_y ** 2 + self._ratio * sin_y ** 2)
        b = -2.0 * self.H * cos_x * cos_y
        c = self.H ** 2 - self.r_eq ** 2
        disc = b ** 2 - 4.0 * a * c
        if disc < 0:
            return None
        r_s = (-b - math.sqrt(disc)) / (2.0 * a)

        sx = r_s * cos_x * cos_y
        sy = -r_s * sin_x
        sz = r_s * cos_x * sin_y

        lat = math.atan(self._ratio * sz / math.sqrt((self.H - sx) ** 2 + sy ** 2))
        lon = self.lon0 - math.atan(sy / (self.H - sx))
        return math.degrees(lat), math.degrees(lon)


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = (math.sin(dlat / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(h))


# ------------------------------------------------------------------ lectura

def _attr(dset, name):
    """Lee un atributo HDF5 como escalar, o None si no existe."""
    if name not in dset.attrs:
        return None
    value = dset.attrs[name]
    return float(np.ravel(value)[0])


def unpack(dset, sl=None) -> np.ndarray:
    """Lee un dataset aplicando scale_factor / add_offset / _FillValue.

    Los archivos de GOES guardan 'x', 'y' y 'CMI' como enteros empaquetados
    con factor de escala y desplazamiento. La libreria netCDF los desempaqueta
    sola; h5py NO: devuelve los enteros crudos. Olvidarlo hace que las
    coordenadas de barrido salgan como numeros sin sentido y que la
    ubicacion parezca caer fuera de la imagen.
    """
    raw = np.asarray(dset[()] if sl is None else dset[sl])

    fill = _attr(dset, "_FillValue")
    mask = (raw == fill) if fill is not None else None

    out = raw.astype(np.float64)
    scale = _attr(dset, "scale_factor")
    offset = _attr(dset, "add_offset")
    if scale is not None:
        out *= scale
    if offset is not None:
        out += offset
    if mask is not None:
        out[mask] = np.nan
    return out


def _read_window(raw: bytes, half_px: int) -> tuple[np.ndarray, float] | None:
    """Extrae una ventana centrada en la ubicacion y su escala en km/pixel.

    Solo se lee del HDF5 el recorte que interesa, no la imagen completa.
    """
    with h5py.File(io.BytesIO(raw), "r") as fh:
        if "CMI" not in fh:
            log.warning("archivo GOES sin variable CMI")
            return None

        proj = fh["goes_imager_projection"]
        grid = FixedGrid({k: _attr(proj, k)
                          for k in ("semi_major_axis", "semi_minor_axis",
                                    "perspective_point_height",
                                    "longitude_of_projection_origin")})

        scan = grid.lonlat_to_scan(config.LAT, config.LON)
        if scan is None:
            log.warning("la ubicacion no es visible desde el satelite")
            return None
        tx, ty = scan

        xs, ys = fh["x"], fh["y"]
        nx, ny = xs.shape[0], ys.shape[0]
        # la rejilla fija es exactamente regular: dos valores dan la escala
        x0, x1 = unpack(xs, slice(0, 2))
        y0, y1 = unpack(ys, slice(0, 2))
        dx, dy = x1 - x0, y1 - y0
        if not (dx and dy):
            log.warning("ejes de la rejilla degenerados")
            return None

        i = int(round((tx - x0) / dx))
        j = int(round((ty - y0) / dy))
        if not (half_px <= i < nx - half_px and half_px <= j < ny - half_px):
            log.info("la ubicacion cae fuera de este sector (i=%s j=%s de %sx%s)",
                     i, j, nx, ny)
            return None

        window = unpack(fh["CMI"],
                        np.s_[j - half_px: j + half_px,
                              i - half_px: i + half_px]).astype(np.float32)

        # banda 13 en Kelvin; fuera de este rango no es una medicion valida
        window[(window < 150.0) | (window > 350.0)] = np.nan
        valid = np.isfinite(window)
        if valid.mean() < 0.5:
            log.warning("ventana mayormente invalida (%.0f%% util)",
                        valid.mean() * 100)
            return None
        log.info("IR leido: %.1f-%.1f K, %.0f%% de pixeles validos",
                 float(np.nanmin(window)), float(np.nanmax(window)),
                 valid.mean() * 100)

        # escala real del pixel en el suelo, medida sobre la propia rejilla
        p_a = grid.scan_to_lonlat(x0 + i * dx, y0 + j * dy)
        p_b = grid.scan_to_lonlat(x0 + (i + 1) * dx, y0 + j * dy)
        km_per_px = _haversine_km(p_a, p_b) if (p_a and p_b) else 3.0

    return window, km_per_px


def fetch_ir_frames(n: int = 5):
    """Ultimos n cuadros de banda 13 recortados alrededor de tu ubicacion."""
    from .sources import Frame  # import diferido para evitar ciclo

    half_px = config.IR_WINDOW_PX // 2

    for prefix, _tag, step in SECTORS:
        keys = recent_keys(n, prefix, step)
        if not keys:
            continue

        frames: list[Frame] = []
        for t, key in keys:
            raw = http.get_bytes(f"{BUCKET}/{key}", timeout=90)
            if not raw:
                continue
            try:
                got = _read_window(raw, half_px)
            except Exception as exc:
                log.warning("no se pudo leer %s: %s", key, exc)
                continue
            if got is None:
                break  # la ubicacion no esta en este sector: probar el siguiente
            data, km_per_px = got
            frames.append(Frame(time=t, data=data, km_per_px=km_per_px,
                                center_lat=config.LAT, center_lon=config.LON,
                                kind="ir"))

        if len(frames) >= 2:
            log.info("infrarrojo %s: %s cuadros, %.2f km/pixel",
                     prefix, len(frames), frames[-1].km_per_px)
            return frames

    log.error("sin cuadros de infrarrojo")
    return []
