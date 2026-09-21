"""El bucle de aprendizaje.

Dos cosas se aprenden de los aciertos y errores acumulados:

1. PESOS POR FUENTE. Cada fuente (radar, infrarrojo, modelos numericos) recibe
   un peso proporcional a su capacidad de SEPARAR: cuanto mas alto dice cuando
   llueve que cuando no. Si en Aguascalientes el infrarrojo le gana a los
   modelos —que es lo que tu experiencia sugiere— el sistema lo descubre solo y
   le sube el peso. Con los datos de septiembre de 2026 descubrio lo contrario,
   que es justamente para lo que sirve medir.

   Antes se repartia por el inverso del Brier y era casi un empate permanente;
   el porque esta explicado donde se calcula, que es donde hace falta leerlo.

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
# Separacion minima para que una fuente cuente. Por debajo de un punto
# porcentual no se distingue de cero, y cerca de cero el signo lo decide el
# redondeo, no la meteorologia.
SEPARACION_MINIMA = 0.01
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


def _separacion(p: np.ndarray, y: np.ndarray, w: np.ndarray) -> float:
    """Cuanto mas alto dice cuando llueve que cuando no.

    La pregunta anterior a cualquier calibracion: ¿esta fuente distingue algo?
    Si la media de lo que dice es la misma llueva o no, no hay nada que
    calibrar -ninguna transformacion de un numero constante produce
    informacion- y da igual lo bueno que parezca su Brier.

    Se devuelve 0.0 cuando no se puede medir, que para el reparto de pesos
    significa "no cuenta". Es lo prudente: una fuente sin casos de lluvia
    todavia no ha demostrado nada.
    """
    con, sin = y == 1, y == 0
    if not con.any() or not sin.any():
        return 0.0
    wc, ws = w[con], w[sin]
    if wc.sum() <= 0 or ws.sum() <= 0:
        return 0.0
    return float(np.average(p[con], weights=wc) - np.average(p[sin], weights=ws))


def _recomponer(probs: dict, usables: dict, pesos: dict) -> float:
    """La mezcla que saldria HOY de unas probabilidades ya guardadas.

    Replica lo que hace `run.py`: una fuente que no podia opinar no vota, y su
    peso se reparte entre las demas. "No veo tan lejos" no es "no va a llover".
    """
    w = {s: (pesos.get(s, 0.0) if usables.get(s) else 0.0) for s in SOURCES}
    total = sum(w.values())
    if total <= 0:
        return 0.0
    return float(sum(probs.get(s, 0.0) * w[s] / total for s in SOURCES))


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
        # Los pesos APLICADOS en su momento. Un cero significa "esta fuente no
        # podia opinar de este horizonte" (fuera de imagen, sin cobertura), y
        # eso hay que respetarlo al recomponer la mezcla mas abajo: no es lo
        # mismo que la fuente dijera 0%.
        usables = {s: float(row.get(f"w_{s}") or 0.0) > 0 for s in SOURCES}
        by_lead[lead].append((ts, probs, p_final, y, usables))

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
        briers, separaciones = {}, {}
        for src in SOURCES:
            p = np.array([r[1].get(src, 0.0) for r in rows])
            briers[src] = _brier(p, y, tw)
            separaciones[src] = _separacion(p, y, tw)
        climatology = _brier(np.full_like(y, float(np.average(y, weights=tw))), y, tw)
        skill[key] = {
            "n": len(rows),
            "brier": {s: round(v, 4) for s, v in briers.items()},
            "separacion": {s: round(v, 4) for s, v in separaciones.items()},
            "brier_final": round(_brier(np.array([r[2] for r in rows]), y, tw), 4),
            "brier_climatology": round(climatology, 4),
        }
        if climatology > 0:
            skill[key]["skill_score"] = round(
                1.0 - skill[key]["brier_final"] / climatology, 3)

        if len(rows) < MIN_SAMPLES:
            continue

        # ---- pesos: por capacidad de DISTINGUIR, no por Brier
        #
        # Hasta el 21 de septiembre de 2026 esto era `1/Brier` normalizado, y
        # medido resulto casi inutil. Dos razones, las dos de fondo:
        #
        # 1. El Brier no tiene recorrido. Su techo para un pronostico inutil es
        #    la tasa base -0.125 aqui- y su piso practico ronda 0.084. Todo el
        #    rango entre "no sirve de nada" y "es lo mejor que tenemos" es un
        #    factor de 1.5, asi que el inverso reparte 0.44 / 0.30 / 0.26 pase
        #    lo que pase. El radar, con separacion medida de 0.0% -es decir,
        #    literalmente ninguna informacion- se llevaba un cuarto del voto.
        #
        # 2. El Brier mezcla dos cosas: si acierta y si esta bien calibrado. Y
        #    la calibracion la arregla despues la isotonica sobre la MEZCLA.
        #    Penalizar aqui a una fuente por estar mal calibrada es castigarla
        #    por un defecto que el siguiente paso corrige de todas formas.
        #
        # Lo que hace falta de una fuente antes de calibrar es que SEPARE: que
        # diga numeros distintos cuando llueve y cuando no. Eso es la
        # separacion, y su recorrido es honesto: 0.0% el radar, 18.6% el
        # infrarrojo, 33.5% los modelos. Una fuente que no separa pesa cero, y
        # eso es lo correcto: no es que valga poco, es que no aporta nada.
        #
        # El minimo no es cosmetico. Una fuente constante no separa exactamente
        # cero: separa -2.8e-17, polvo de coma flotante, y el SIGNO de ese polvo
        # decidia entre "vuelvo al prior" y "un tercio para cada una". Dos
        # repartos muy distintos a merced del orden en que numpy sumo unos
        # flotantes. Lo encontro una prueba que fallaba a veces, que es la peor
        # forma de encontrarlo y la unica que habia.
        #
        # Con un umbral en 1% ademas se dice algo cierto: medio punto de
        # separacion no es una senal debil, es ruido con formato de senal.
        utiles = {s: (separaciones[s] if separaciones[s] >= SEPARACION_MINIMA
                      else 0.0) for s in SOURCES}
        suma = sum(utiles.values())
        if suma <= 0:
            # Ninguna fuente distingue nada en este horizonte. Repartir por
            # separacion seria dividir entre cero; se vuelve al prior y ya.
            learned = dict(DEFAULT_WEIGHTS)
        else:
            learned = {s: utiles[s] / suma for s in SOURCES}
        # confianza en lo aprendido crece con el numero de muestras
        alpha = min(1.0, len(rows) / 400.0)
        weights[key] = {
            s: round(alpha * learned[s] + (1 - alpha) * DEFAULT_WEIGHTS[s], 4)
            for s in SOURCES
        }

        # ---- curva de calibracion sobre la mezcla RECOMPUESTA
        #
        # No sobre el `p_final` que se guardo: ese se calculo con los pesos que
        # habia ese dia. Si hoy los pesos cambian, la curva quedaria ajustada a
        # una mezcla que ya no se produce, y con vida media de 45 dias esa
        # contaminacion dura meses. Peor: el sintoma seria que el sistema
        # empeora justo despues de mejorar los pesos, que es la clase de
        # observacion que hace desandar un cambio bueno.
        #
        # Recomponerla es posible porque cada fila guarda las tres
        # probabilidades por separado. Asi la curva describe siempre la mezcla
        # de HOY.
        raw = np.array([_recomponer(r[1], r[4], weights[key]) for r in rows])
        xs, ys = _pava(raw, y, tw)
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
