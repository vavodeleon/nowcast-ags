"""Reporte matutino: el panorama del dia y que tal va aprendiendo el sistema."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import numpy as np

from . import config, notify, sources, store, verify

log = logging.getLogger(__name__)


def _potencial_convectivo(cape: float) -> str:
    if cape >= 2500:
        return "muy alto"
    if cape >= 1500:
        return "alto"
    if cape >= 800:
        return "moderado"
    if cape >= 300:
        return "bajo"
    return "practicamente nulo"


def build_report() -> str:
    data = sources.fetch_models()
    hourly = (data or {}).get("hourly") or {}
    times = hourly.get("time") or []

    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=18)

    # consenso de modelos hora por hora para el resto del dia
    per_hour: dict[str, list[float]] = {}
    cape_max = 0.0
    spread_by_hour: dict[str, list[float]] = {}

    for model in config.OPENMETEO_MODELS:
        probs = hourly.get(f"precipitation_probability_{model}")
        capes = hourly.get(f"cape_{model}") or []
        if not probs:
            continue
        for i, ts in enumerate(times):
            if i >= len(probs) or probs[i] is None:
                continue
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if not (now <= dt <= end):
                continue
            local = dt.astimezone(config.TZ).strftime("%H:%M")
            per_hour.setdefault(local, []).append(float(probs[i]) / 100.0)
            spread_by_hour.setdefault(local, []).append(float(probs[i]) / 100.0)
            if i < len(capes) and capes[i] is not None:
                cape_max = max(cape_max, float(capes[i]))

    lines: list[str] = []

    if per_hour:
        best = max(per_hour.items(), key=lambda kv: np.mean(kv[1]))
        peak_hour, peak_vals = best
        peak = float(np.mean(peak_vals))
        disagreement = float(np.std(peak_vals))

        lines.append(f"Maxima probabilidad hoy: {peak * 100:.0f}% cerca de las {peak_hour}")
        if disagreement > 0.20:
            lines.append(
                f"Los modelos no se ponen de acuerdo (dispersion {disagreement * 100:.0f} pts). "
                "Fiate del nowcast en tiempo real, no de esta cifra.")
        elif peak >= 0.5:
            lines.append("Los modelos coinciden: dia con lluvia probable.")

        ventana = [h for h, v in sorted(per_hour.items())
                   if float(np.mean(v)) >= 0.35]
        if ventana:
            lines.append(f"Ventana de riesgo: {ventana[0]} a {ventana[-1]}")
    else:
        lines.append("Sin datos de modelos esta manana.")

    lines.append(f"Potencial convectivo (CAPE {cape_max:.0f} J/kg): "
                 f"{_potencial_convectivo(cape_max)}")

    # como va aprendiendo
    perf = verify.recent_performance(days=21)
    if perf:
        lines.append("")
        lines.append("Desempeno de las ultimas 3 semanas:")
        for lead in ("30", "60", "120"):
            row = perf.get(lead)
            if not row:
                continue
            skill = row.get("skill_vs_climatologia")
            skill_txt = f", {skill * 100:+.0f}% vs climatologia" if skill is not None else ""
            lines.append(f"  +{lead} min: acierto {row['acierto_binario'] * 100:.0f}%"
                         f"{skill_txt} (n={row['n']})")
    else:
        lines.append("")
        lines.append("Aun sin historial suficiente para medir el desempeno.")

    cal = store.load_json(config.CALIBRATION_JSON, {})
    n = cal.get("n", 0)
    if n:
        w = (cal.get("weights") or {}).get("60")
        if w:
            lines.append("")
            lines.append(f"Pesos aprendidos a 60 min: radar {w['radar']:.0%}, "
                         f"IR {w['ir']:.0%}, modelos {w['models']:.0%}")
        lines.append(f"Casos acumulados: {n}")

    return "\n".join(lines)


def build_pressure_report() -> tuple[str, str] | None:
    """Resumen diario de presion para su canal. None si no hay nada que decir."""
    from . import pressure

    st = pressure.fetch()
    if st.now_msl is None:
        return None

    lineas = [f"Presion ahora: {st.now_msl:.0f} hPa (nivel del mar)"]
    if st.change_24h is not None:
        lineas.append(f"Ultimas 24 h: {st.change_24h:+.1f} hPa")

    if st.forecast_drop and st.forecast_drop >= config.PRESSURE_DROP_WATCH:
        lineas.append("")
        lineas.append(f"Se espera una bajada de {st.forecast_drop:.0f} hPa "
                      f"hacia el {st.forecast_drop_at}.")
    else:
        lineas.append("")
        lineas.append("Sin bajadas notables previstas para hoy.")

    lineas.append("")
    lineas.append(f"Nivel: {st.level}")
    return f"Presion — {datetime.now(config.TZ).strftime('%a %d %b')}", "\n".join(lineas)


def send_all() -> None:
    """Manda el reporte de clima y, si esta configurado, el de presion."""
    body = build_report()
    fecha = datetime.now(config.TZ).strftime("%a %d %b")
    notify.send(f"Clima Aguascalientes — {fecha}", body,
                priority="low", tags="sunny")
    print(body)

    if config.NTFY_TOPIC_SALUD:
        try:
            got = build_pressure_report()
            if got:
                titulo, cuerpo = got
                notify.send(titulo, cuerpo, priority="low",
                            tags="bar_chart", topic=config.NTFY_TOPIC_SALUD)
                print("\n" + cuerpo)
        except Exception as exc:
            log.error("reporte de presion fallo: %s", exc)


def maybe_send_morning() -> bool:
    """Envia el reporte matutino si toca y no se ha mandado hoy.

    Lo llama el ciclo del nowcast en cada pasada, en vez de depender de un
    cron propio. Motivo: el cron diario de GitHub no disparo ni una sola vez
    en el primer dia del sistema. Como el ciclo corre cada 15 minutos, basta
    con que compruebe la hora.

    La ventana es amplia (6:30 a 10:00) a proposito: si GitHub se retrasa una
    hora, el reporte igual sale, aunque sea tarde. El registro por fecha en
    state.json evita que se mande dos veces.
    """
    from . import store

    ahora = datetime.now(config.TZ)
    if ahora.hour > config.MORNING_WINDOW_END_HOUR:
        return False
    if ahora.hour < config.MORNING_HOUR:
        return False
    if ahora.hour == config.MORNING_HOUR and ahora.minute < config.MORNING_MINUTE:
        return False

    hoy = ahora.strftime("%Y-%m-%d")
    state = store.load_json(config.STATE_JSON, {})
    if state.get("reporte_matutino") == hoy:
        return False

    log.info("enviando reporte matutino de %s", hoy)
    send_all()
    state["reporte_matutino"] = hoy
    store.save_json(config.STATE_JSON, state)
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    body = build_report()
    fecha = datetime.now(config.TZ).strftime("%a %d %b")
    notify.send(f"Clima Aguascalientes — {fecha}", body,
                priority="low", tags="sunny")
    print(body)

    # resumen de presion a su canal, si esta configurado
    if config.NTFY_TOPIC_SALUD:
        try:
            got = build_pressure_report()
            if got:
                titulo, cuerpo = got
                notify.send(titulo, cuerpo, priority="low",
                            tags="bar_chart", topic=config.NTFY_TOPIC_SALUD)
                print("\n" + cuerpo)
        except Exception as exc:
            log.error("reporte de presion fallo: %s", exc)


if __name__ == "__main__":
    main()
