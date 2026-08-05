"""Seguimiento de presion atmosferica, para anticipar dias de riesgo de migrana.

Contexto y limites de esto:

La evidencia asocia caidas de 5-10 hPa en 12-24 h con mas ataques de migrana
en personas susceptibles. Pero la sensibilidad individual varia mucho -hay
quienes reaccionan a subidas, no a bajadas- y una revision sistematica de 2025
encontro resultados inconsistentes entre estudios. Esto es una senal
informativa para poder anticiparse, no un diagnostico ni un dispositivo
medico, y no sustituye la indicacion de un medico.

Detalle tecnico que importa aqui: Aguascalientes esta a 1,880 m, asi que la
presion de estacion ronda los 805 hPa. Los umbrales de la literatura estan
en presion reducida a nivel del mar, asi que TODO en este modulo usa
pressure_msl. Mezclarlos haria parecer que la ciudad vive en un huracan
permanente.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np

from . import config, http

log = logging.getLogger(__name__)


@dataclass
class PressureState:
    now_msl: float | None = None
    now_surface: float | None = None
    change_3h: float | None = None
    change_6h: float | None = None
    change_24h: float | None = None
    # peor caida en ventana de 24 h dentro del pronostico
    forecast_drop: float | None = None
    forecast_drop_at: str | None = None
    forecast_drop_in_h: float | None = None
    level: str = "sin datos"          # sin datos | tranquilo | vigilancia | alto | muy alto
    trend: str = "sin datos"          # bajando | subiendo | estable
    series: list = field(default_factory=list)

    @property
    def is_falling_now(self) -> bool:
        return (self.change_3h is not None
                and self.change_3h <= -config.PRESSURE_DROP_3H)

    @property
    def is_risky_soon(self) -> bool:
        return (self.forecast_drop is not None
                and self.forecast_drop >= config.PRESSURE_DROP_24H)


def _level_for(drop_24h: float | None) -> str:
    """Traduce una caida (hPa, positiva = cae) a un nivel legible."""
    if drop_24h is None:
        return "sin datos"
    if drop_24h >= config.PRESSURE_DROP_SEVERE:
        return "muy alto"
    if drop_24h >= config.PRESSURE_DROP_24H:
        return "alto"
    if drop_24h >= config.PRESSURE_DROP_WATCH:
        return "vigilancia"
    return "tranquilo"


def fetch() -> PressureState:
    """Presion pasada y pronosticada, y la peor caida de 24 h que viene."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={config.LAT:.4f}&longitude={config.LON:.4f}"
        "&hourly=pressure_msl,surface_pressure"
        "&past_hours=30&forecast_hours=48"
        "&timezone=UTC"
    )
    data = http.get_json(url)
    state = PressureState()
    if not data:
        log.error("Open-Meteo no respondio (presion)")
        return state

    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    msl = hourly.get("pressure_msl") or []
    sfc = hourly.get("surface_pressure") or []
    if not times or not msl:
        return state

    parsed: list[datetime] = []
    values: list[float] = []
    surface: list[float | None] = []
    for i, ts in enumerate(times):
        if i >= len(msl) or msl[i] is None:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        parsed.append(dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))
        values.append(float(msl[i]))
        surface.append(float(sfc[i]) if i < len(sfc) and sfc[i] is not None else None)

    if len(parsed) < 6:
        return state

    now = datetime.now(timezone.utc)
    arr = np.array(values)

    def at(target: datetime) -> float | None:
        """Valor mas cercano al instante pedido, si esta a menos de 90 min."""
        gaps = [abs((p - target).total_seconds()) for p in parsed]
        k = int(np.argmin(gaps))
        return values[k] if gaps[k] <= 5400 else None

    state.now_msl = at(now)
    idx_now = int(np.argmin([abs((p - now).total_seconds()) for p in parsed]))
    state.now_surface = surface[idx_now] if idx_now < len(surface) else None

    for hours, attr in ((3, "change_3h"), (6, "change_6h"), (24, "change_24h")):
        past = at(now - timedelta(hours=hours))
        if state.now_msl is not None and past is not None:
            setattr(state, attr, round(state.now_msl - past, 1))

    # ---- peor caida de 24 h en lo que viene
    # Para cada hora futura, cuanto habra bajado respecto a 24 h antes.
    worst = 0.0
    worst_at: datetime | None = None
    for i, t in enumerate(parsed):
        if t <= now or (t - now) > timedelta(hours=config.PRESSURE_LOOKAHEAD_H):
            continue
        ref = at(t - timedelta(hours=24))
        if ref is None:
            continue
        drop = ref - values[i]          # positivo = la presion baja
        if drop > worst:
            worst, worst_at = drop, t

    if worst_at is not None and worst > 0:
        state.forecast_drop = round(worst, 1)
        state.forecast_drop_at = worst_at.astimezone(config.TZ).strftime("%a %d, %H:%M")
        state.forecast_drop_in_h = round((worst_at - now).total_seconds() / 3600, 1)

    # el nivel toma lo peor entre lo que ya paso y lo que viene
    caida_actual = -state.change_24h if state.change_24h is not None else None
    candidatos = [v for v in (caida_actual, state.forecast_drop) if v is not None]
    state.level = _level_for(max(candidatos)) if candidatos else "sin datos"

    if state.change_6h is not None:
        if state.change_6h <= -1.0:
            state.trend = "bajando"
        elif state.change_6h >= 1.0:
            state.trend = "subiendo"
        else:
            state.trend = "estable"

    # ---- serie para la grafica: de -24 h a +48 h
    for t, v in zip(parsed, values):
        if -24 <= (t - now).total_seconds() / 3600 <= 48:
            state.series.append({
                "t": t.astimezone(config.TZ).strftime("%d %H:%M"),
                "iso": t.isoformat(),
                "msl": round(v, 1),
                "futuro": t > now,
            })

    log.info("presion: %.1f hPa, 24h %s, nivel %s, peor caida futura %s",
             state.now_msl or float("nan"), state.change_24h, state.level,
             state.forecast_drop)
    return state


def to_dict(state: PressureState) -> dict:
    return {
        "now_msl": state.now_msl,
        "now_surface": state.now_surface,
        "change_3h": state.change_3h,
        "change_6h": state.change_6h,
        "change_24h": state.change_24h,
        "forecast_drop": state.forecast_drop,
        "forecast_drop_at": state.forecast_drop_at,
        "forecast_drop_in_h": state.forecast_drop_in_h,
        "level": state.level,
        "trend": state.trend,
        "series": state.series,
        "umbral_24h": config.PRESSURE_DROP_24H,
    }
