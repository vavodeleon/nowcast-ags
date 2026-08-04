"""Verificacion: que paso en realidad.

Sin esto no hay aprendizaje posible. Cada vez que corre, mira las ultimas
horas y decide, para cada bloque de 15 minutos, si llovio o no sobre tus
coordenadas.

Se usan dos testigos independientes y gana el que sea afirmativo:
  1. El propio radar/IR: si sobre tu pixel hubo eco de lluvia.
  2. Open-Meteo: precipitacion observada (analisis, no pronostico).

Es deliberadamente generoso con el "si llovio": para tu caso de uso, no
avisarte de una lluvia real es un error mas caro que avisarte de mas.
"""
from __future__ import annotations

import logging
from datetime import timedelta

import numpy as np

from . import config, engine, sources, store

log = logging.getLogger(__name__)

RAIN_MM_THRESHOLD = 0.15   # mm en una hora; por debajo es rocio o ruido
RAIN_SCORE_THRESHOLD = 0.30  # score de radar/IR equivalente a ~29 dBZ


def observe() -> list[dict]:
    """Genera filas de observacion para los bloques recientes."""
    rows: dict[str, dict] = {}

    # --- testigo 1: sensores remotos, bloque por bloque
    # Sobre Aguascalientes el radar no ve nada, asi que preguntarle "¿llovio?"
    # daria siempre que no y el sistema aprenderia una mentira. Solo se usa si
    # de verdad cubre la ciudad.
    frames: list = []
    if sources.radar_coverage()["usable"]:
        frames = sources.fetch_radar_frames(n=12)
    if len(frames) < 2:
        frames = sources.fetch_ir_frames(n=12)

    for frame in frames:
        sig = engine.to_signal(frame)
        cy, cx = frame.center_px
        # disco de 5 km: la lluvia sobre la ciudad no es un solo pixel
        radius_px = max(2.0, 5.0 / frame.km_per_px)
        peak = engine._sample_disc(sig, cy, cx, radius_px)
        slot = store.round_slot(frame.time)
        prev = rows.get(slot)
        if prev is not None and peak <= float(prev["peak_score"] or 0):
            continue

        if frame.kind == "radar":
            # el eco de radar SI es lluvia medida: sirve como verdad
            rained = 1 if peak >= RAIN_SCORE_THRESHOLD else 0
        else:
            # Un tope nuboso frio no es lluvia. Un cirrus denso pasando por
            # encima dispararia un falso "llovio" y el sistema aprenderia
            # basura. El infrarrojo se guarda como dato, no como veredicto:
            # la verdad la pone Open-Meteo mas abajo.
            rained = None

        rows[slot] = {"valid_utc": slot, "rained": rained, "mm": "",
                      "peak_score": round(peak, 4), "source": frame.kind}

    # --- testigo 2: precipitacion observada de Open-Meteo
    data = sources.fetch_observed_precip(hours_back=6)
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    precip = hourly.get("precipitation") or []
    for ts, mm in zip(times, precip):
        if mm is None:
            continue
        try:
            base = _parse(ts)
        except ValueError:
            continue
        # una hora observada cubre cuatro bloques de 15 min
        for k in range(4):
            slot = store.round_slot(base + timedelta(minutes=15 * k))
            wet = 1 if float(mm) >= RAIN_MM_THRESHOLD else 0
            row = rows.get(slot)
            if row is None:
                rows[slot] = {"valid_utc": slot, "rained": wet,
                              "mm": round(float(mm), 2), "peak_score": "",
                              "source": "openmeteo"}
            else:
                row["mm"] = round(float(mm), 2)
                if row["rained"] is None:
                    row["rained"] = wet
                    row["source"] = f"{row['source']}+openmeteo"
                elif wet:
                    row["rained"] = 1
                    row["source"] = f"{row['source']}+openmeteo"

    now = store.now_utc()
    out = [r for r in rows.values()
           # solo bloques ya pasados y con un veredicto real: un bloque que
           # nadie pudo observar se descarta en vez de inventarle un cero
           if _parse(r["valid_utc"]) < now and r["rained"] is not None]
    out.sort(key=lambda r: r["valid_utc"])
    return out


def _parse(ts: str):
    from datetime import datetime, timezone
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def run() -> int:
    rows = observe()
    store.append_observations(rows)
    log.info("verificacion: %s bloques observados", len(rows))
    return len(rows)


def recent_performance(days: int = 14) -> dict:
    """Resumen legible de que tan bien lo esta haciendo el sistema."""
    from datetime import datetime, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    obs = {r["valid_utc"]: r for r in store.read_observations()}
    preds = store.read_predictions()

    buckets: dict[int, list[tuple[float, float]]] = {}
    for row in preds:
        ob = obs.get(row["valid_utc"])
        if not ob:
            continue
        try:
            issued = _parse(row["issued_utc"])
            if issued < cutoff:
                continue
            lead = int(row["lead_min"])
            buckets.setdefault(lead, []).append(
                (float(row["p_final"]), float(ob["rained"])))
        except (TypeError, ValueError):
            continue

    out: dict[str, dict] = {}
    for lead, pairs in sorted(buckets.items()):
        p = np.array([a for a, _ in pairs])
        y = np.array([b for _, b in pairs])
        if len(p) < 5:
            continue
        brier = float(np.mean((p - y) ** 2))
        base = float(y.mean())
        clim = float(np.mean((base - y) ** 2))
        # acierto binario con umbral 0.5
        hits = float(np.mean((p >= 0.5) == (y >= 0.5)))
        out[str(lead)] = {
            "n": len(p),
            "brier": round(brier, 4),
            "skill_vs_climatologia": round(1 - brier / clim, 3) if clim > 0 else None,
            "acierto_binario": round(hits, 3),
            "frecuencia_lluvia": round(base, 3),
        }
    return out
