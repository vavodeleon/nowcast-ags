"""Que esta pasando AHORA MISMO sobre la ciudad.

Este modulo existe por un error real: el sistema decia "esta lloviendo"
cuando solo estaba nublado. La causa es la limitacion de fondo del
infrarrojo, y vale la pena nombrarla bien.

El IR mide la temperatura del TECHO de la nube. Una nube con el tope a
-45 grados puede ser:

  a) una celda activa, que si llueve; o
  b) el yunque de una tormenta que esta a 100 km, extendido por el viento
     en altura. El yunque es igual de frio, cubre muchisimo mas territorio
     y NO moja.

Distinguirlos con un solo umbral de temperatura es imposible: son igual de
frios. Lo que si los distingue es la FORMA del campo:

  - Un yunque es amplio y liso: todo el entorno esta igual de frio.
  - Un nucleo convectivo es compacto: muy frio en el centro y bastante mas
    templado a 60-90 km de distancia.

Ese contraste es lo que se mide aqui. Y ademas se exige CORROBORACION de
una fuente independiente antes de afirmar que llueve: rayos cerca, o
precipitacion observada. El infrarrojo solo, por si mismo, ya no basta
para decir "esta lloviendo" — como mucho dice "hay nube cargada encima".
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np

from . import config

log = logging.getLogger(__name__)

# Distancia a la que se busca el anillo de comparacion
ANILLO_INTERIOR_KM = 60.0
ANILLO_EXTERIOR_KM = 95.0
ENCIMA_KM = 8.0

# Contraste minimo (K) entre el anillo y el centro para hablar de nucleo
CONTRASTE_NUCLEO_K = 14.0
# Por encima de esta fraccion de cielo frio alrededor, huele a yunque
FRACCION_YUNQUE = 0.80
# Rayos a menos de esto cuentan como corroboracion
RAYOS_CERCA_KM = 25.0


@dataclass
class Ahora:
    bt_encima: float | None = None       # temperatura de brillo sobre la ciudad
    contraste: float | None = None       # cuanto mas frio es el centro que el anillo
    fraccion_fria: float | None = None   # cuanto del entorno esta bajo 235 K
    rayos_cerca: int = 0
    precip_observada: float | None = None
    forma: str = "sin datos"             # despejado | nubes | yunque | nucleo
    lloviendo: bool = False
    corroborado_por: str = ""
    estado: str = "sin datos"            # texto para la pagina

    def to_dict(self) -> dict:
        return {
            "bt_encima": (round(self.bt_encima, 1)
                          if self.bt_encima is not None else None),
            "contraste": (round(self.contraste, 1)
                          if self.contraste is not None else None),
            "fraccion_fria": (round(self.fraccion_fria, 2)
                              if self.fraccion_fria is not None else None),
            "rayos_cerca": self.rayos_cerca,
            "precip_observada": self.precip_observada,
            "forma": self.forma,
            "lloviendo": self.lloviendo,
            "corroborado_por": self.corroborado_por,
            "estado": self.estado,
        }


def _disco(campo: np.ndarray, cy: float, cx: float, r_px: float) -> np.ndarray:
    h, w = campo.shape
    y0, y1 = int(max(0, cy - r_px)), int(min(h, cy + r_px + 1))
    x0, x1 = int(max(0, cx - r_px)), int(min(w, cx + r_px + 1))
    if y0 >= y1 or x0 >= x1:
        return np.array([])
    yy, xx = np.ogrid[y0:y1, x0:x1]
    m = (yy - cy) ** 2 + (xx - cx) ** 2 <= r_px ** 2
    vals = campo[y0:y1, x0:x1][m]
    return vals[np.isfinite(vals)]


def _anillo(campo: np.ndarray, cy: float, cx: float,
            r_int: float, r_ext: float) -> np.ndarray:
    h, w = campo.shape
    y0, y1 = int(max(0, cy - r_ext)), int(min(h, cy + r_ext + 1))
    x0, x1 = int(max(0, cx - r_ext)), int(min(w, cx + r_ext + 1))
    if y0 >= y1 or x0 >= x1:
        return np.array([])
    yy, xx = np.ogrid[y0:y1, x0:x1]
    d2 = (yy - cy) ** 2 + (xx - cx) ** 2
    m = (d2 >= r_int ** 2) & (d2 <= r_ext ** 2)
    vals = campo[y0:y1, x0:x1][m]
    return vals[np.isfinite(vals)]


def _rayos_cerca(bloques: list, lat: float, lon: float) -> int:
    """Cuenta destellos a menos de RAYOS_CERCA_KM en el bloque mas reciente."""
    if not bloques:
        return 0
    reciente = min(bloques, key=lambda b: b.get("edad_min", 999))
    if reciente.get("edad_min", 999) > 20:
        return 0
    total = 0
    for punto in reciente.get("puntos", []):
        try:
            plat, plon, n = punto[0], punto[1], punto[2]
        except (IndexError, TypeError):
            continue
        dy = (plat - lat) * 111.32
        dx = (plon - lon) * 111.32 * math.cos(math.radians(lat))
        if math.hypot(dy, dx) <= RAYOS_CERCA_KM:
            total += int(n)
    return total


def assess(ir_frame, bloques_rayos: list | None = None,
           precip_observada: float | None = None) -> Ahora:
    """Decide que esta ocurriendo sobre la ciudad en este momento."""
    a = Ahora()
    a.rayos_cerca = _rayos_cerca(bloques_rayos or [], config.LAT, config.LON)
    a.precip_observada = precip_observada

    if ir_frame is None:
        # sin satelite solo queda lo observado
        if precip_observada and precip_observada > 0.1:
            a.lloviendo, a.corroborado_por = True, "precipitación observada"
            a.estado = "Está lloviendo"
        return a

    bt = ir_frame.data
    cy, cx = ir_frame.center_px
    km = ir_frame.km_per_px or 2.45

    encima = _disco(bt, cy, cx, max(2.0, ENCIMA_KM / km))
    if encima.size == 0:
        return a
    # percentil 10: lo mas frio que hay justo encima, sin quedarse en un pixel
    a.bt_encima = float(np.percentile(encima, 10))

    anillo = _anillo(bt, cy, cx, ANILLO_INTERIOR_KM / km, ANILLO_EXTERIOR_KM / km)
    if anillo.size > 20:
        a.contraste = float(np.median(anillo) - a.bt_encima)
        a.fraccion_fria = float(np.mean(anillo < config.IR_CONVECTIVE_K))

    # ---- forma del campo
    if a.bt_encima >= config.IR_CONVECTIVE_K:
        a.forma = "despejado" if a.bt_encima > 270 else "nubes"
    elif (a.contraste is not None and a.contraste >= CONTRASTE_NUCLEO_K):
        a.forma = "nucleo"
    elif (a.fraccion_fria is not None and a.fraccion_fria >= FRACCION_YUNQUE):
        a.forma = "yunque"
    else:
        a.forma = "nubes"

    # ---- ¿llueve? Solo con corroboracion independiente.
    razones = []
    if precip_observada is not None and precip_observada > 0.1:
        razones.append("precipitación observada")
    if a.rayos_cerca > 0:
        razones.append(f"{a.rayos_cerca} rayos a menos de {RAYOS_CERCA_KM:.0f} km")
    # un nucleo convectivo muy frio y compacto se acepta por si solo
    if a.forma == "nucleo" and a.bt_encima <= config.IR_DEEP_K:
        razones.append("núcleo convectivo profundo encima")

    a.lloviendo = bool(razones)
    a.corroborado_por = " · ".join(razones)

    if a.lloviendo:
        a.estado = "Está lloviendo"
    elif a.forma == "yunque":
        a.estado = "Nublado, pero es nube alta de una tormenta lejana"
    elif a.forma == "nucleo":
        a.estado = "Nube cargada encima"
    elif a.bt_encima < config.IR_CONVECTIVE_K:
        a.estado = "Muy nublado"
    elif a.forma == "nubes":
        a.estado = "Nublado"
    else:
        a.estado = "Despejado"

    log.info("ahora: %s | BT %.0f K, contraste %s, forma %s, rayos %s",
             a.estado, a.bt_encima,
             f"{a.contraste:.0f}" if a.contraste is not None else "?",
             a.forma, a.rayos_cerca)
    return a
