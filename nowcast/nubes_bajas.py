"""Nubes bajas: las que llueven sin verse en el infrarrojo de tormentas.

## El problema

Álvaro, 5/10/2026: «ya van varias veces que está lloviendo pero no se ve
ninguna nube en la app, pero eran nubes bajas».

Todo el sistema mira la banda 13 buscando topes FRÍOS: la señal IR ignora lo
que pase de 235 K y el mapa pintaba transparente todo lo que pasara de 250 K.
Es lo correcto para convección profunda, y es ciego por construcción a una
capa de estratocúmulos o nimbostratos con topes a 270-285 K, que en
temporada llueven -poco, pero llueven-.

## Por qué no basta un umbral

Una nube baja no es fría en términos absolutos: es más fría QUE EL SUELO. Con
el suelo a 305 K a mediodía, una nube a 285 K destaca por 20 K; con el suelo a
288 K de madrugada, la misma nube casi no se distingue. Así que se compara
cada píxel con una referencia de cielo despejado sacada de la propia ventana
(el percentil 97: lo más caliente que hay, que es suelo sin nubes).

La consecuencia, que hay que decir claro: **de noche esto es poco fiable.** El
suelo se enfría hasta la temperatura de la nube y el contraste desaparece. Por
eso cada medida lleva `de_dia`, y la evaluación las mira por separado.

Y si la ventana entera está cubierta, la referencia es una nube y no se
detecta nada. Falla del lado de no ver, nunca de inventar.

## Lo que NO hace (todavía)

No entra en el pronóstico. Se dibuja en el mapa y se guarda en cada fila
(`nb_frac`, `nb_contraste`, `nb_dia`). La sección 9 de evaluar.py mide si
separa lluvia de no lluvia en los casos donde el pronóstico dijo que no. Solo
si gana entra; es la misma regla que tumbó el ajuste del yunque.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np

from . import config


def referencia(bt: np.ndarray) -> float | None:
    """Temperatura del cielo despejado en la ventana, o None si no hay.

    None cuando lo más caliente ya es frío para ser suelo: la ventana entera
    está cubierta y no hay con qué comparar.
    """
    v = bt[np.isfinite(bt)]
    if v.size < 100:
        return None
    ref = float(np.percentile(v, 97))
    return ref if ref >= config.NUBE_BAJA_REF_MIN_K else None


def contraste(bt: np.ndarray, ref: float | None) -> np.ndarray:
    """Cuánto más fría que el suelo está cada píxel (K), solo en la franja de
    nube baja/media; 0 fuera de ella. Lo usa también el mapa."""
    if ref is None:
        return np.zeros_like(bt, dtype=float)
    c = ref - bt
    franja = (bt > config.NUBE_ALTA_K) & (c >= config.NUBE_BAJA_CONTRASTE_K)
    return np.where(franja & np.isfinite(bt), c, 0.0)


def de_dia(cuando: datetime) -> bool:
    """Horas con sol bien alto en Aguascalientes (UTC-6, sin horario de verano
    desde 2022). Es una ventana conservadora, no un cálculo solar: lo que
    importa es dejar fuera madrugada y anochecer, donde el contraste muere."""
    local = cuando.astimezone(timezone.utc) - timedelta(hours=6)
    return config.NUBE_BAJA_DIA_DESDE_H <= local.hour < config.NUBE_BAJA_DIA_HASTA_H


def medir(frame) -> dict:
    """Señal sobre la ciudad: {frac, contraste_k, de_dia}. Nunca lanza.

    `frac` es la fracción del círculo de NUBE_BAJA_RADIO_KM alrededor de la
    ciudad cubierta por nube baja; `contraste_k` la mediana del contraste en
    los píxeles cubiertos. Sin referencia o sin datos, los números son None:
    "no sé" se dice con null, nunca con un 0 que parezca "despejado".
    """
    vacio = {"frac": None, "contraste_k": None, "de_dia": None}
    if frame is None or getattr(frame, "data", None) is None:
        return vacio
    bt = np.asarray(frame.data, dtype=float)
    dia = de_dia(frame.time) if getattr(frame, "time", None) else None
    ref = referencia(bt)
    if ref is None:
        return dict(vacio, de_dia=dia)
    h, w = bt.shape
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    r = config.NUBE_BAJA_RADIO_KM / frame.km_per_px
    yy, xx = np.mgrid[0:h, 0:w]
    dentro = ((yy - cy) ** 2 + (xx - cx) ** 2 <= r * r) & np.isfinite(bt)
    if not dentro.any():
        return dict(vacio, de_dia=dia)
    c = contraste(bt, ref)[dentro]
    cubiertos = c > 0
    frac = float(cubiertos.mean())
    med = float(np.median(c[cubiertos])) if cubiertos.any() else 0.0
    return {"frac": round(frac, 3), "contraste_k": round(med, 1), "de_dia": dia}


def filas(señal: dict) -> dict:
    """Las tres columnas de predictions.csv; vacías si no se sabe."""
    def _v(x):
        return "" if x is None or (isinstance(x, float) and math.isnan(x)) else x
    dia = señal.get("de_dia")
    return {"nb_frac": _v(señal.get("frac")),
            "nb_contraste": _v(señal.get("contraste_k")),
            "nb_dia": "" if dia is None else int(bool(dia))}
