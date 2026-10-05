"""Un campo de movimiento, para trayectorias que no son rectas.

## El problema

Álvaro, 5 de octubre de 2026: «cuando las nubes tienen una trayectoria no
lineal, por ejemplo estas estaban girando en forma circular, no se podía
calcular ninguna trayectoria».

El motor describía el movimiento con UN vector: una mediana sobre todo el
dominio, o desde el 22/09 un vector por celda medido a su alrededor. Los dos
suponen que la nube va en línea recta. Con un sistema que gira eso falla de dos
formas, las dos comprobadas con una tormenta sintética:

- **La mediana global inventa una traslación.** Seis celdas girando a 69 km/h
  alrededor de un centro, sin moverse el conjunto, daban «19 km/h del oeste».
  Los vectores de lados opuestos del giro no se cancelan limpiamente: la
  mediana se queda con lo que sobra, que no describe nada.
- **La recta de cada celda se sale de la curva.** La celda que iba a pasar
  sobre la ciudad en 25 minutos salía «a 40 minutos»; con un giro más cerrado
  la recta ya ni pasa cerca, y no sale ninguna trayectoria.

## La solución

Medir un vector por zona -teselas solapadas de ~100 km- y no uno para todo. El
conjunto de vectores es un campo, y una trayectoria se obtiene **integrándolo**:
avanzar cinco minutos con la velocidad del sitio donde se está, volver a mirar
la velocidad del sitio nuevo, avanzar otros cinco. Si el campo gira, la
trayectoria gira sola. Es la advección semilagrangiana que usan los sistemas
operativos de nowcasting (pysteps, por ejemplo), sin sus partes caras.

## Lo que NO resuelve

Un campo medido con los últimos cuadros describe el giro de AHORA. Si el giro
se acelera o se deshace, la trayectoria se equivoca igual que la recta. Y
dentro de una tesela se sigue suponiendo traslación: un giro más pequeño que
una tesela (~100 km) no se ve. Es mejor que una recta, no adivinación.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np

from . import config

log = logging.getLogger(__name__)


@dataclass
class Campo:
    """Vectores de movimiento en una rejilla gruesa sobre la imagen."""
    ys: np.ndarray          # filas de los centros de tesela (px)
    xs: np.ndarray          # columnas de los centros de tesela (px)
    vy: np.ndarray          # px/min, positivo hacia el sur (fila creciente)
    vx: np.ndarray          # px/min, positivo hacia el este
    validas: int            # teselas con señal propia, no rellenadas
    total: int

    @property
    def util(self) -> bool:
        """¿Hay suficientes zonas con nubes como para describir algo?"""
        return self.validas >= config.CAMPO_MIN_TESELAS


def medir(frames, tesela: int | None = None, paso: int | None = None) -> Campo | None:
    """Mide un vector por tesela con correlación de fase.

    Cada tesela se mide en todos los pares de cuadros consecutivos y se queda
    con la mediana ponderada por confianza, igual que el vector global: lo
    único que cambia es que ahora hay muchos.

    Las teselas sin nubes no tienen nada que seguir. Se rellenan con la media
    de las vecinas válidas, ponderada por distancia; donde no hay ninguna
    cerca, con la mediana de todas las válidas.

    La primera versión las dejaba QUIETAS, con el argumento de que el cielo
    despejado no tiene velocidad. Era falso: lo que cruza el hueco es la
    celda, y la celda lleva la suya. Una tormenta sola a 45 km de la ciudad
    avanzaba hasta el borde de su zona y se paraba ahí; el selftest lo cazó
    (ETA «nunca» donde la verdad era 100 min).
    """
    from .engine import _phase_correlate, _weighted_median, to_signal

    if len(frames) < 2:
        return None
    tesela = tesela or config.CAMPO_TESELA_PX
    paso = paso or config.CAMPO_PASO_PX
    señales = [to_signal(f) for f in frames]
    h, w = señales[-1].shape
    if h < tesela or w < tesela:
        return None

    mitad = tesela // 2
    ys = np.arange(mitad, h - mitad + 1, paso)
    xs = np.arange(mitad, w - mitad + 1, paso)
    vy = np.full((len(ys), len(xs)), np.nan)
    vx = np.full((len(ys), len(xs)), np.nan)

    for iy, cy in enumerate(ys):
        for ix, cx in enumerate(xs):
            sl = (slice(cy - mitad, cy + mitad), slice(cx - mitad, cx + mitad))
            est = []
            for i in range(len(frames) - 1):
                dt = (frames[i + 1].time - frames[i].time).total_seconds() / 60.0
                if dt <= 0:
                    continue
                a, b = señales[i][sl], señales[i + 1][sl]
                # Una tesela casi vacía da un pico de correlación que es ruido.
                if (a > 0.05).mean() < 0.03 or (b > 0.05).mean() < 0.03:
                    continue
                dy, dx, conf = _phase_correlate(a, b)
                if conf <= 0.01:
                    continue
                est.append((dy / dt, dx / dt, conf * (i + 1) / len(frames)))
            if est:
                arr = np.array(est)
                vy[iy, ix] = _weighted_median(arr[:, 0], arr[:, 2])
                vx[iy, ix] = _weighted_median(arr[:, 1], arr[:, 2])

    validas = int(np.isfinite(vy).sum())
    if validas == 0:
        return Campo(ys, xs, np.zeros_like(vy), np.zeros_like(vx), 0, vy.size)

    # Velocidades no físicas para convección: se recortan, igual que el global.
    km = frames[-1].km_per_px
    tope = 120.0 / (km * 60.0)           # 120 km/h en px/min
    rapidez = np.hypot(vy, vx)
    escala = np.where(rapidez > tope, tope / np.maximum(rapidez, 1e-9), 1.0)
    vy, vx = vy * escala, vx * escala

    vy, vx = _rellenar(vy, vx, ys, xs, radio=2.5 * paso)
    vy, vx = _quitar_locas(vy, vx)
    return Campo(ys, xs, vy, vx, validas, vy.size)


def _rellenar(vy, vx, ys, xs, radio):
    """Huecos: media de las válidas cercanas; sin vecinas, la mediana de todas."""
    fy, fx = vy.copy(), vx.copy()
    malas = np.argwhere(~np.isfinite(vy))
    buenas = np.argwhere(np.isfinite(vy))
    if len(buenas):
        fondo_y = float(np.median(vy[buenas[:, 0], buenas[:, 1]]))
        fondo_x = float(np.median(vx[buenas[:, 0], buenas[:, 1]]))
    else:
        fondo_y = fondo_x = 0.0
    for iy, ix in malas:
        if not len(buenas):
            fy[iy, ix] = fx[iy, ix] = 0.0
            continue
        d = np.hypot(ys[buenas[:, 0]] - ys[iy], xs[buenas[:, 1]] - xs[ix])
        cerca = d <= radio
        if not cerca.any():
            fy[iy, ix], fx[iy, ix] = fondo_y, fondo_x
            continue
        p = 1.0 / np.maximum(d[cerca], 1.0)
        sel = buenas[cerca]
        fy[iy, ix] = float(np.average(vy[sel[:, 0], sel[:, 1]], weights=p))
        fx[iy, ix] = float(np.average(vx[sel[:, 0], sel[:, 1]], weights=p))
    return fy, fx


def _quitar_locas(vy, vx):
    """Sustituye SOLO las teselas que discrepan mucho de sus vecinas.

    La primera versión pasaba una mediana 3x3 por todo el campo, y en la
    prueba con un giro duplicaba el error a una hora (15.8 km contra 7.6 sin
    suavizar): una mediana mezcla los vectores de lados opuestos del giro,
    que es exactamente lo que el campo existe para no hacer. Pero sin ningún
    filtro pasa la tesela loca que da una correlación sin estructura.

    El término medio: cada tesela se compara con la mediana de sus vecinas, y
    solo se reemplaza si se aleja más de lo que las vecinas se alejan entre
    sí (tres veces su dispersión), con un mínimo para no tocar campos casi
    quietos. Un giro real cambia de forma suave entre teselas vecinas y pasa;
    un salto aislado no.
    """
    fy, fx = vy.copy(), vx.copy()
    h, w = vy.shape
    for i in range(h):
        for j in range(w):
            vec = [(vy[a, b], vx[a, b])
                   for a in range(max(0, i - 1), min(h, i + 2))
                   for b in range(max(0, j - 1), min(w, j + 2))
                   if (a, b) != (i, j)]
            if len(vec) < 3:
                continue
            ny = np.array([v[0] for v in vec])
            nx = np.array([v[1] for v in vec])
            my, mx = np.median(ny), np.median(nx)
            disp = np.median(np.hypot(ny - my, nx - mx))
            if math.hypot(vy[i, j] - my, vx[i, j] - mx) > max(3 * disp, 0.15):
                fy[i, j], fx[i, j] = my, mx
    return fy, fx


def velocidad(campo: Campo, y: float, x: float) -> tuple[float, float]:
    """Velocidad (vy, vx) en px/min en un punto, interpolada bilinealmente."""
    def _idx(arr, v):
        if v <= arr[0]:
            return 0, 0, 0.0
        if v >= arr[-1]:
            n = len(arr) - 1
            return n, n, 0.0
        j = int(np.searchsorted(arr, v)) - 1
        return j, j + 1, (v - arr[j]) / (arr[j + 1] - arr[j])
    i0, i1, ty = _idx(campo.ys, y)
    j0, j1, tx = _idx(campo.xs, x)
    def _bil(m):
        a = m[i0, j0] * (1 - tx) + m[i0, j1] * tx
        b = m[i1, j0] * (1 - tx) + m[i1, j1] * tx
        return float(a * (1 - ty) + b * ty)
    return _bil(campo.vy), _bil(campo.vx)


def trayectoria(campo: Campo, y: float, x: float, minutos: float,
                paso_min: float = 5.0, sentido: int = 1) -> list[tuple[float, float, float]]:
    """Camino de una parcela siguiendo el campo: [(minuto, fila, columna), ...].

    `sentido=+1` va hacia el futuro (a dónde irá esta celda); `-1` hacia el
    pasado (de dónde viene lo que estará aquí). Se usa el punto medio de cada
    paso -Runge-Kutta de orden 2- en vez de la velocidad del inicio: en un
    giro, avanzar con la velocidad del principio de cada paso abre la curva
    hacia fuera, y con pasos de cinco minutos eso se nota.
    """
    camino = [(0.0, float(y), float(x))]
    t = 0.0
    while t < minutos - 1e-9:
        h = min(paso_min, minutos - t) * sentido
        vy1, vx1 = velocidad(campo, y, x)
        ym, xm = y + vy1 * h / 2, x + vx1 * h / 2
        vy2, vx2 = velocidad(campo, ym, xm)
        y, x = y + vy2 * h, x + vx2 * h
        t += abs(h)
        camino.append((t, y, x))
    return camino


def es_giro(campo: Campo) -> bool:
    """¿El campo gira más de lo que se traslada? Solo para describirlo bien.

    Compara la vorticidad media con la velocidad media. No decide nada del
    pronóstico; sirve para que la página no diga «vienen del oeste» cuando lo
    que hay es un sistema dando vueltas.
    """
    if campo is None or not campo.util:
        return False
    dvx_dy = np.gradient(campo.vx, campo.ys, axis=0)
    dvy_dx = np.gradient(campo.vy, campo.xs, axis=1)
    vort = float(np.mean(np.abs(dvx_dy - dvy_dx)))     # 1/min
    rapidez = float(np.mean(np.hypot(campo.vy, campo.vx)))  # px/min
    media = float(np.hypot(campo.vy.mean(), campo.vx.mean()))
    # Mucha rapidez local pero poca neta, con vorticidad apreciable.
    return rapidez > 0.05 and media < 0.5 * rapidez and vort > 0.002
