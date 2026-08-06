"""Rayos detectados por el GLM de GOES-19.

El GLM (Geostationary Lightning Mapper) va a bordo del mismo satelite del que
ya bajamos el infrarrojo, y publica en el mismo bucket publico de NOAA. Es
deteccion optica real de relampagos desde el espacio, no una estimacion a
partir de la nube.

Numeros que hacen esto viable: cada archivo cubre 20 segundos y pesa ~360 KB,
con unos 30 segundos de retraso. Quince minutos son 45 archivos, ~16 MB:
cuatro veces mas ligero que las imagenes de infrarrojo que ya descargamos.

Se mantiene una hora de historia en cuatro bloques de 15 minutos. El bloque
mas reciente se dibuja brillante y los viejos se van apagando, de modo que
se ve hacia donde avanza la actividad electrica sin necesidad de controles.
"""
from __future__ import annotations

import io
import logging
import re
from datetime import datetime, timedelta, timezone

import h5py
import numpy as np

from . import config, http, store
from .goes import BUCKET, S3_NS

log = logging.getLogger(__name__)

_KEY_RE = re.compile(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})")

# Los rayos se agrupan en una rejilla para que el JSON no crezca sin control:
# en una tormenta activa el GLM ve miles de destellos en 15 minutos.
GRID_DEG = 0.02          # ~2 km
MAX_PUNTOS_POR_BLOQUE = 1200


def _list_glm(hora: datetime) -> list[tuple[datetime, str]]:
    """Archivos GLM de una hora concreta."""
    import xml.etree.ElementTree as ET

    prefix = (f"GLM-L2-LCFA/{hora.year}/{hora.timetuple().tm_yday:03d}"
              f"/{hora.hour:02d}/")
    salida: list[tuple[datetime, str]] = []
    token = ""
    # una hora son 180 archivos: hay que paginar
    for _ in range(3):
        url = f"{BUCKET}/?list-type=2&max-keys=1000&prefix={prefix}{token}"
        raw = http.get_bytes(url)
        if not raw:
            break
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            break
        for node in root.findall(f"{S3_NS}Contents"):
            k = node.find(f"{S3_NS}Key")
            if k is None or not k.text:
                continue
            m = _KEY_RE.search(k.text)
            if not m:
                continue
            year, doy, hh, mm, ss = (int(g) for g in m.groups())
            t = (datetime(year, 1, 1, tzinfo=timezone.utc)
                 + timedelta(days=doy - 1, hours=hh, minutes=mm, seconds=ss))
            salida.append((t, k.text))
        nxt = root.find(f"{S3_NS}NextContinuationToken")
        if nxt is None or not nxt.text:
            break
        token = f"&continuation-token={nxt.text}"
    return salida


def _flashes(raw: bytes) -> list[tuple[float, float]]:
    """Extrae (lat, lon) de los destellos dentro del dominio."""
    fuera = config.LIGHTNING_RADIUS_DEG
    try:
        with h5py.File(io.BytesIO(raw), "r") as fh:
            if "flash_lat" not in fh or "flash_lon" not in fh:
                return []
            lat = np.asarray(fh["flash_lat"][()], dtype=np.float64)
            lon = np.asarray(fh["flash_lon"][()], dtype=np.float64)
    except Exception as exc:
        log.debug("archivo GLM ilegible: %s", exc)
        return []

    if lat.size == 0:
        return []
    cerca = ((np.abs(lat - config.LAT) <= fuera)
             & (np.abs(lon - config.LON) <= fuera))
    return list(zip(lat[cerca].tolist(), lon[cerca].tolist()))


def fetch_recent(minutos: int = 15) -> list[tuple[float, float]]:
    """Destellos de los ultimos N minutos alrededor de la ciudad."""
    ahora = datetime.now(timezone.utc)
    desde = ahora - timedelta(minutes=minutos)

    claves: list[tuple[datetime, str]] = []
    for h in {desde.replace(minute=0, second=0, microsecond=0),
              ahora.replace(minute=0, second=0, microsecond=0)}:
        claves.extend(_list_glm(h))

    ventana = [(t, k) for t, k in claves if desde <= t <= ahora]
    ventana.sort()
    if not ventana:
        log.info("sin archivos GLM en la ventana")
        return []

    puntos: list[tuple[float, float]] = []
    fallos = 0
    for _t, key in ventana:
        raw = http.get_bytes(f"{BUCKET}/{key}", timeout=30)
        if not raw:
            fallos += 1
            continue
        puntos.extend(_flashes(raw))

    log.info("rayos: %s destellos cerca en %s archivos (%s fallaron)",
             len(puntos), len(ventana), fallos)
    return puntos


def _agrupar(puntos: list[tuple[float, float]]) -> list[list]:
    """Agrupa en rejilla y devuelve [lat, lon, cuantos]."""
    if not puntos:
        return []
    cubos: dict[tuple[int, int], int] = {}
    for lat, lon in puntos:
        clave = (int(round(lat / GRID_DEG)), int(round(lon / GRID_DEG)))
        cubos[clave] = cubos.get(clave, 0) + 1
    ordenado = sorted(cubos.items(), key=lambda kv: -kv[1])[:MAX_PUNTOS_POR_BLOQUE]
    return [[round(a * GRID_DEG, 4), round(b * GRID_DEG, 4), n]
            for (a, b), n in ordenado]


def update() -> dict:
    """Añade el bloque actual y descarta lo que ya pasó de una hora."""
    ahora = datetime.now(timezone.utc)
    previo = store.load_json(config.LIGHTNING_JSON, {}) or {}
    bloques = previo.get("bloques", [])

    etiqueta = store.round_slot(ahora, minutes=15)
    if any(b.get("t") == etiqueta for b in bloques):
        log.info("el bloque %s ya estaba registrado", etiqueta)
    else:
        try:
            puntos = fetch_recent(15)
        except Exception as exc:
            log.error("descarga de rayos fallo: %s", exc)
            puntos = []
        bloques.append({"t": etiqueta, "puntos": _agrupar(puntos),
                        "total": len(puntos)})

    # conservar solo la ultima hora
    corte = ahora - timedelta(minutes=config.LIGHTNING_HISTORY_MIN)
    vivos = []
    for b in bloques:
        try:
            t = datetime.fromisoformat(b["t"])
        except (ValueError, KeyError):
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        if t >= corte:
            b["edad_min"] = int((ahora - t).total_seconds() / 60)
            vivos.append(b)
    vivos.sort(key=lambda b: b["t"])

    datos = {
        "actualizado": ahora.isoformat(),
        "bloques": vivos,
        "total_hora": sum(b.get("total", 0) for b in vivos),
    }
    store.save_json(config.LIGHTNING_JSON, datos)
    return datos
