"""El bucle de aprendizaje.

Dos cosas se aprenden de los aciertos y errores acumulados:

1. PESOS POR FUENTE. Cada fuente (radar, infrarrojo, modelos numericos) recibe
   un peso proporcional a su destreza reciente, medida con el Brier score.
   Si en Aguascalientes el infrarrojo le gana a los modelos —que es lo que tu
   experiencia sugiere— el sistema lo descubre solo y le sube el peso.

2. CALIBRACION DE PROBABILIDAD. Una regresion isotonica (PAVA) mapea el score
   crudo a una probabilidad honesta. Es la diferencia entre "el sistema dice
   70%" y "de las veces que dijo 70%, llovio el 70%".

Ambas se recalculan por separado para cada horizonte, porque la destreza a
15 minutos y a 3 horas no tienen nada que ver.
"""
from __future__ import annotations

import logging
from collections import defaultdict

import numpy as np

from . import config, store

log = logging.getLogger(__name__)

MIN_SAMPLES = 40          # por debajo de esto no hay senal, solo ruido
HALF_LIFE_DAYS = 45.0     # el pasado lejano pesa menos: el clima cambia de estacion
SOURCES = ["radar", "ir", "models"]

DEFAULT_WEIGHTS = dict(config.DEFAULT_SOURCE_WEIGHTS)


def _pava(x: np.ndarray, y: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Regresion isotonica por Pool Adjacent Violators. Devuelve (x, y ajustada)."""
    order = np.argsort(x)
    xs = x[order]
    ys = y[order].astype(float)
    ws = np.maximum(w[order].astype(float), 1e-9)

    n = len(ys)
    if n == 0:
        return xs, ys

    # cada bloque es [valor, peso, indice_inicio, indice_fin]
    blocks: list[list[float]] = []
    for k in range(n):
        blocks.append([ys[k], ws[k], k, k])
        # mientras el bloque previo viole la monotonia, fusionar
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            v2, w2, _lo2, hi2 = blocks.pop()
            v1, w1, lo1, _hi1 = blocks.pop()
            tot = w1 + w2
            blocks.append([(v1 * w1 + v2 * w2) / tot, tot, lo1, hi2])

    out = np.empty(n)
    for val, _wt, lo, hi in blocks:
        out[int(lo):int(hi) + 1] = val
    return xs, np.clip(out, 0.0, 1.0)


def _time_weights(times: np.ndarray, now: float) -> np.ndarray:
    age_days = np.maximum(0.0, (now - times) / 86400.0)
    return np.power(0.5, age_days / HALF_LIFE_DAYS)


def _brier(p: np.ndarray, y: np.ndarray, w: np.ndarray) -> float:
    if w.sum() <= 0:
        return 0.25
    return float(np.average((p - y) ** 2, weights=w))


def build_calibration() -> dict:
    """Recalcula pesos y curvas de calibracion desde el historial."""
    from datetime import datetime

    preds = store.read_predictions()
    obs = {r["valid_utc"]: r for r in store.read_observations()}
    if not preds or not obs:
        return {"weights": {}, "curves": {}, "skill": {}, "n": 0}

    now_ts = datetime.now().timestamp()
    by_lead: dict[int, list[tuple]] = defaultdict(list)

    for row in preds:
        ob = obs.get(row["valid_utc"])
        if ob is None:
            continue
        try:
            y = float(ob["rained"])
            lead = int(row["lead_min"])
            ts = datetime.fromisoformat(row["issued_utc"]).timestamp()
            probs = {s: float(row.get(f"p_{s}") or 0.0) for s in SOURCES}
            p_final = float(row.get("p_final") or 0.0)
        except (TypeError, ValueError):
            continue
        by_lead[lead].append((ts, probs, p_final, y))

    weights: dict[str, dict[str, float]] = {}
    curves: dict[str, dict] = {}
    skill: dict[str, dict] = {}
    total = 0

    for lead, rows in by_lead.items():
        key = str(lead)
        total += len(rows)
        times = np.array([r[0] for r in rows])
        y = np.array([r[3] for r in rows])
        tw = _time_weights(times, now_ts)

        # ---- destreza por fuente
        briers = {}
        for src in SOURCES:
            p = np.array([r[1].get(src, 0.0) for r in rows])
            briers[src] = _brier(p, y, tw)
        climatology = _brier(np.full_like(y, float(np.average(y, weights=tw))), y, tw)
        skill[key] = {
            "n": len(rows),
            "brier": {s: round(v, 4) for s, v in briers.items()},
            "brier_final": round(_brier(np.array([r[2] for r in rows]), y, tw), 4),
            "brier_climatology": round(climatology, 4),
        }
        if climatology > 0:
            skill[key]["skill_score"] = round(
                1.0 - skill[key]["brier_final"] / climatology, 3)

        if len(rows) < MIN_SAMPLES:
            continue

        # ---- pesos: inverso del Brier, normalizado, suavizado hacia el prior
        inv = {s: 1.0 / max(briers[s], 1e-3) for s in SOURCES}
        total_inv = sum(inv.values())
        learned = {s: inv[s] / total_inv for s in SOURCES}
        # confianza en lo aprendido crece con el numero de muestras
        alpha = min(1.0, len(rows) / 400.0)
        weights[key] = {
            s: round(alpha * learned[s] + (1 - alpha) * DEFAULT_WEIGHTS[s], 4)
            for s in SOURCES
        }

        # ---- curva de calibracion sobre la probabilidad combinada
        p_final = np.array([r[2] for r in rows])
        xs, ys = _pava(p_final, y, tw)
        # comprimir a como maximo 25 puntos para que el JSON no crezca
        if len(xs) > 25:
            idx = np.linspace(0, len(xs) - 1, 25).astype(int)
            xs, ys = xs[idx], ys[idx]
        curves[key] = {"x": [round(float(v), 4) for v in xs],
                       "y": [round(float(v), 4) for v in ys]}

    return {"weights": weights, "curves": curves, "skill": skill,
            "n": total, "updated": store.now_utc().isoformat()}


P_FLOOR, P_CEIL = 0.02, 0.97
SHRINK_N = 500.0   # muestras para confiar del todo en la curva aprendida


def apply_curve(p: float, lead: int, cal: dict) -> float:
    """Aplica la calibracion aprendida a una probabilidad cruda.

    Con dos frenos deliberados:

    - Encogimiento hacia la probabilidad cruda mientras haya pocas muestras.
      La isotonica es flexible y con 50 casos memoriza ruido; con 500 ya
      dice algo.
    - Techo y piso. Un sistema que anuncia 100% de lluvia es justo el tipo de
      sobreconfianza que hace inutiles a las apps. El clima no ofrece
      certezas y el pronostico no deberia fingirlas.
    """
    curve = (cal.get("curves") or {}).get(str(lead))
    if not curve or len(curve.get("x", [])) < 3:
        return float(np.clip(p, P_FLOOR, P_CEIL))

    mapped = float(np.interp(p, curve["x"], curve["y"]))

    n = ((cal.get("skill") or {}).get(str(lead), {}) or {}).get("n", 0)
    trust = min(1.0, float(n) / SHRINK_N)
    blended = trust * mapped + (1.0 - trust) * p

    return float(np.clip(blended, P_FLOOR, P_CEIL))


def weights_for(lead: int, cal: dict) -> dict[str, float]:
    return (cal.get("weights") or {}).get(str(lead), DEFAULT_WEIGHTS)


def refresh() -> dict:
    cal = build_calibration()
    store.save_json(config.CALIBRATION_JSON, cal)
    log.info("calibracion actualizada con %s pares prediccion/observacion",
             cal.get("n", 0))
    return cal
