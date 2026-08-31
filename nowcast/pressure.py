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

from . import barometro, config, http

log = logging.getLogger(__name__)


@dataclass
class PressureState:
    now_msl: float | None = None
    now_surface: float | None = None
    change_1h: float | None = None
    change_3h: float | None = None
    change_6h: float | None = None
    change_24h: float | None = None
    # peor caida en ventana de 24 h dentro del pronostico
    forecast_drop: float | None = None
    forecast_drop_at: str | None = None
    forecast_drop_in_h: float | None = None
    fuente: str = "modelo"            # modelo | sensor local
    sensor: dict = field(default_factory=dict)   # diagnostico de la fuente local
    level: str = "sin datos"          # sin datos | tranquilo | vigilancia | alto | muy alto
    daily_cycle_amplitude: float = 0.0   # cuanto oscila la presion cada dia por si sola
    trend: str = "sin datos"          # bajando | subiendo | estable
    series: list = field(default_factory=list)

    @property
    def is_falling_now(self) -> bool:
        return (self.change_3h is not None
                and self.change_3h <= -config.PRESSURE_DROP_3H)

    @property
    def is_falling_fast(self) -> bool:
        """Caida rapida, del tipo que precede a un frente de tormenta.

        Existe porque la ventana de 3 h diluye lo veloz: una bajada de
        2 hPa en cuarenta minutos es fisiologicamente notoria y se sentia
        antes de que el sistema la marcara, porque repartida en tres horas
        no llegaba al umbral. Lo que parece importar no es solo cuanto baja
        sino que tan rapido.
        """
        return (self.change_1h is not None
                and self.change_1h <= -config.PRESSURE_DROP_1H)

    @property
    def velocidad_hpa_h(self) -> float | None:
        """Ritmo de caida en hPa por hora, para decirlo en el aviso."""
        return -self.change_1h if self.change_1h is not None else None

    @property
    def is_risky_soon(self) -> bool:
        return (self.forecast_drop is not None
                and self.forecast_drop >= config.PRESSURE_DROP_24H)


def remove_daily_cycle(parsed: list[datetime],
                       values: list[float]) -> tuple[np.ndarray, np.ndarray]:
    """Quita la marea atmosferica y el ciclo termico diario.

    Sin esto el sistema seria inservible. En los datos reales de
    Aguascalientes la presion oscila ~7 hPa TODOS los dias: pico hacia las
    08:00, valle hacia las 16:00, pico otra vez a las 22:00. Es la marea
    atmosferica sumada al calentamiento diurno sobre el altiplano, y no
    tiene nada que ver con el tiempo.

    Con un umbral de 2.5 hPa en 3 h, ese ciclo disparaba una alerta cada
    tarde. Un aviso que suena siempre a la misma hora se ignora en tres
    dias, y entonces tampoco sirve el dia que de verdad importa.

    Se ajustan por minimos cuadrados un armonico de 24 h y otro de 12 h
    (mas media y tendencia lineal) y se resta SOLO la parte ciclica. La
    tendencia sinoptica -la que si interesa- se conserva intacta.
    """
    v = np.asarray(values, dtype=float)
    if len(v) < 12:
        return v, np.zeros_like(v)

    horas = np.array([(p - parsed[0]).total_seconds() / 3600.0 for p in parsed])
    # hora local del dia: es la fase que gobierna la marea
    hod = np.array([p.astimezone(config.TZ).hour
                    + p.astimezone(config.TZ).minute / 60.0 for p in parsed])

    A = np.column_stack([
        np.ones_like(horas), horas,                       # media y tendencia
        np.cos(2 * np.pi * hod / 24), np.sin(2 * np.pi * hod / 24),   # diurno
        np.cos(2 * np.pi * hod / 12), np.sin(2 * np.pi * hod / 12),   # semidiurno
    ])
    try:
        coef, *_ = np.linalg.lstsq(A, v, rcond=None)
    except np.linalg.LinAlgError:
        return v, np.zeros_like(v)

    ciclo = A[:, 2:] @ coef[2:]
    return v - ciclo, ciclo


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

    # Todos los cambios se miden sobre la serie SIN el ciclo diario; si no,
    # la marea atmosferica se confunde con un frente.
    limpia, ciclo = remove_daily_cycle(parsed, values)
    state.daily_cycle_amplitude = round(float(np.ptp(ciclo)), 1) if len(ciclo) else 0.0

    def at(target: datetime, serie=None) -> float | None:
        """Valor mas cercano al instante pedido, si esta a menos de 90 min."""
        serie = values if serie is None else serie
        gaps = [abs((p - target).total_seconds()) for p in parsed]
        k = int(np.argmin(gaps))
        return float(serie[k]) if gaps[k] <= 5400 else None

    def limpio(target: datetime) -> float | None:
        return at(target, limpia)

    state.now_msl = at(now)
    idx_now = int(np.argmin([abs((p - now).total_seconds()) for p in parsed]))
    state.now_surface = surface[idx_now] if idx_now < len(surface) else None

    ahora_limpio = limpio(now)
    for hours, attr in ((1, "change_1h"), (3, "change_3h"),
                        (6, "change_6h"), (24, "change_24h")):
        past = limpio(now - timedelta(hours=hours))
        if ahora_limpio is not None and past is not None:
            setattr(state, attr, round(ahora_limpio - past, 1))

    # ---- el barometro fisico, si la malla lo esta alimentando
    # Sustituye al modelo para el PRESENTE y los cambios recientes, que es
    # donde gana: mide aqui, cada minuto, sin depender de internet. El
    # pronostico se queda en Open-Meteo, porque el sensor sabe que esta
    # pasando pero no que va a pasar.
    try:
        _aplicar_sensor(state, now)
    except Exception as exc:
        log.warning("el barometro local fallo, se sigue con el modelo: %s", exc)

    # ---- peor caida de 24 h en lo que viene
    # Para cada hora futura, cuanto habra bajado respecto a 24 h antes.
    worst = 0.0
    worst_at: datetime | None = None
    for i, t in enumerate(parsed):
        if t <= now or (t - now) > timedelta(hours=config.PRESSURE_LOOKAHEAD_H):
            continue
        ref = limpio(t - timedelta(hours=24))
        if ref is None:
            continue
        drop = ref - float(limpia[i])   # positivo = la presion baja
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
    # Se guardan ambas: la medida (lo que marca un barometro) y la limpia
    # (sin el ciclo diario), que es sobre la que se decide.
    for t, v, lim in zip(parsed, values, limpia):
        if -24 <= (t - now).total_seconds() / 3600 <= 48:
            state.series.append({
                "t": t.astimezone(config.TZ).strftime("%d %H:%M"),
                "iso": t.isoformat(),
                "msl": round(v, 1),
                "limpia": round(float(lim), 1),
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
        "fuente": state.fuente,
        "sensor": state.sensor,
        "change_1h": state.change_1h,
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
        "ciclo_diario": state.daily_cycle_amplitude,
    }


def _aplicar_sensor(state: PressureState, now: datetime) -> None:
    """Reemplaza presente y cambios recientes con el sensor de la malla."""
    serie = barometro.leer(horas=30)
    state.sensor = barometro.resumen(serie)
    if not barometro.utilizable(serie):
        if state.sensor.get("muestras"):
            log.info("barometro local presente pero no utilizable: %s", state.sensor)
        return

    factor = barometro.factor_a_nivel_del_mar(serie, state.now_msl)
    if factor is None:
        return
    state.sensor["factor"] = round(factor, 4)

    # La marea tambien esta en la serie del sensor: hay que quitarla igual
    # que en la del modelo, o el umbral de 1 hora se dispara cada tarde.
    tiempos = [t for t, _ in serie]
    valores = [v for _, v in serie]
    try:
        limpia_s, ciclo_s = remove_daily_cycle(tiempos, valores)
    except Exception as exc:
        log.info("no se pudo quitar la marea del sensor: %s", exc)
        return
    serie_limpia = list(zip(tiempos, [float(v) for v in limpia_s]))
    state.sensor["amplitud_diaria"] = round(float(np.ptp(ciclo_s)) * factor, 2)

    ahora_s = barometro.valor_en(serie_limpia, now)
    if ahora_s is None:
        return

    cambios = {}
    for horas, attr in ((1, "change_1h"), (3, "change_3h"), (6, "change_6h"),
                        (24, "change_24h")):
        pasado = barometro.valor_en(serie_limpia, now - timedelta(hours=horas))
        if pasado is None:
            continue          # sin historia suficiente: se deja la del modelo
        cambios[attr] = round((ahora_s - pasado) * factor, 1)

    if not cambios:
        return
    for attr, valor in cambios.items():
        setattr(state, attr, valor)

    crudo_ahora = barometro.valor_en(serie, now)
    if crudo_ahora is not None:
        state.now_msl = round(crudo_ahora * factor, 1)
        state.now_surface = round(crudo_ahora, 1)
    state.fuente = "sensor local"
    log.info("presion del sensor de la malla: %s muestras cada %ss, "
             "1h %s hPa, 3h %s hPa",
             state.sensor["muestras"], state.sensor["cadencia_s"],
             state.change_1h, state.change_3h)
