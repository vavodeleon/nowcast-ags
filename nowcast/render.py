"""Genera la imagen del satelite lista para superponer en un mapa.

Toma la ventana de infrarrojo -que vive en la rejilla del satelite, no en
lat/lon- y la reproyecta a un rectangulo geografico regular. Sin ese paso
la imagen queda torcida sobre el mapa, porque la rejilla geoestacionaria no
es un rectangulo en coordenadas geograficas.

El color va de transparente (cielo despejado o nube baja, que no llueve) a
morado intenso (topes muy frios, conveccion profunda). La escala esta
elegida para que lo que se ve coincida con lo que importa: si hay morado
acercandose, va a llover.
"""
from __future__ import annotations

import logging

import numpy as np
from PIL import Image
from scipy import ndimage

from . import config

log = logging.getLogger(__name__)

# Escala de color por temperatura de brillo (K). Transparente arriba de
# 250 K: eso es nube baja o suelo, y no aporta informacion de tormenta.
_STOPS = [
    (250.0, (120, 160, 200, 0)),      # invisible
    (240.0, (120, 170, 220, 90)),     # nube alta incipiente
    (235.0, (90, 190, 160, 160)),     # conveccion: umbral clasico
    (225.0, (245, 210, 80, 200)),     # creciendo
    (215.0, (240, 130, 50, 225)),     # tormenta
    (205.0, (225, 60, 60, 240)),      # fuerte
    (195.0, (170, 40, 150, 250)),     # tope muy frio
    (180.0, (110, 20, 110, 255)),     # extremo
]


def colorize(bt: np.ndarray) -> np.ndarray:
    """Temperatura de brillo -> RGBA."""
    temps = np.array([s[0] for s in _STOPS])
    cols = np.array([s[1] for s in _STOPS], dtype=float)

    flat = bt.ravel()
    out = np.zeros((flat.size, 4), dtype=float)
    valid = np.isfinite(flat)
    # np.interp exige x creciente y nuestras temperaturas van al reves
    xs = temps[::-1]
    for ch in range(4):
        ys = cols[::-1, ch]
        out[valid, ch] = np.interp(flat[valid], xs, ys)
    out[~valid] = 0
    return out.reshape(bt.shape + (4,)).astype(np.uint8)


def reproject_to_latlon(frame, size: int = 700):
    """Reproyecta la ventana del satelite a una rejilla lat/lon regular.

    Devuelve (RGBA, bounds) donde bounds = [[sur, oeste], [norte, este]],
    el formato que espera Leaflet para superponer una imagen.
    """
    from .goes import FixedGrid, scan_grid_vectorized

    meta = getattr(frame, "grid_meta", None)
    if not meta:
        return None, None

    grid = FixedGrid(meta["proj"])
    x0, dx, i0 = meta["x0"], meta["dx"], meta["i0"]
    y0, dy, j0 = meta["y0"], meta["dy"], meta["j0"]
    h, w = frame.data.shape

    # extension geografica aproximada de la ventana, a partir de sus esquinas
    corners = []
    for jj, ii in ((0, 0), (0, w - 1), (h - 1, 0), (h - 1, w - 1)):
        p = grid.scan_to_lonlat(x0 + (i0 + ii) * dx, y0 + (j0 + jj) * dy)
        if p:
            corners.append(p)
    if len(corners) < 4:
        return None, None

    lats = [c[0] for c in corners]
    lons = [c[1] for c in corners]
    # margen hacia adentro: las esquinas extremas son las mas distorsionadas
    south, north = max(min(lats), -85), min(max(lats), 85)
    west, east = min(lons), max(lons)

    grid_lat = np.linspace(north, south, size)      # norte arriba
    grid_lon = np.linspace(west, east, size)
    mesh_lon, mesh_lat = np.meshgrid(grid_lon, grid_lat)

    sx, sy = scan_grid_vectorized(grid, mesh_lat, mesh_lon)
    # angulos de barrido -> indices dentro de la ventana recortada
    fi = (sx - x0) / dx - i0
    fj = (sy - y0) / dy - j0

    bad = ~np.isfinite(fi) | ~np.isfinite(fj)
    fi = np.nan_to_num(fi, nan=-1.0)
    fj = np.nan_to_num(fj, nan=-1.0)

    data = np.nan_to_num(frame.data, nan=300.0)
    sampled = ndimage.map_coordinates(data, [fj, fi], order=1,
                                      mode="constant", cval=300.0)
    sampled[bad] = np.nan
    sampled[(fi < 0) | (fi > w - 1) | (fj < 0) | (fj > h - 1)] = np.nan

    return colorize(sampled), [[south, west], [north, east]]


def render(frame, path: str, size: int = 700) -> list | None:
    """Escribe el PNG del satelite y devuelve sus limites geograficos."""
    try:
        rgba, bounds = reproject_to_latlon(frame, size)
    except Exception as exc:
        log.warning("no se pudo reproyectar el satelite: %s", exc)
        return None
    if rgba is None:
        return None
    try:
        Image.fromarray(rgba, mode="RGBA").save(path, optimize=True)
    except Exception as exc:
        log.warning("no se pudo escribir %s: %s", path, exc)
        return None
    log.info("imagen de satelite escrita en %s", path)
    return bounds
