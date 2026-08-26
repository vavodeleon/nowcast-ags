"""Notificaciones push al telefono via ntfy.sh (gratis, sin cuenta)."""
from __future__ import annotations

import logging

from . import config, http, store

log = logging.getLogger(__name__)


def send(title: str, message: str, *, priority: str = "default",
         tags: str = "", topic: str | None = None) -> bool:
    topic = topic if topic is not None else config.NTFY_TOPIC
    if not topic:
        log.warning("canal ntfy no configurado; no se envia nada")
        return False
    headers = {
        "Title": title.encode("utf-8").decode("latin-1", errors="replace"),
        "Priority": priority,
    }
    if tags:
        headers["Tags"] = tags
    url = f"{config.NTFY_SERVER.rstrip('/')}/{topic}"
    ok = http.post(url, message.encode("utf-8"), headers)
    if ok:
        log.info("notificacion enviada: %s", title)
    return ok


def _cooldown_ok_hours(key: str, hours: float) -> bool:
    return _cooldown_ok(key, minutes=hours * 60.0)


def maybe_pressure_alert(state) -> bool:
    """Avisa a su canal si viene o esta ocurriendo una caida de presion.

    El tono es deliberadamente sereno y concreto. Una alerta de salud que
    suena alarmante genera ansiedad sin aportar nada; lo util es el dato y
    el margen de tiempo para actuar.
    """
    if not config.NTFY_TOPIC_SALUD:
        # Avisar solo cuando de verdad habia algo que decir. Sin esto, el
        # canal sin configurar es indistinguible de "no pasaba nada", y una
        # alerta de salud que se pierde en silencio es el peor caso posible.
        if getattr(state, "is_risky_soon", False) or getattr(state, "is_falling_now", False):
            log.warning("habia una alerta de presion que dar, pero "
                        "NTFY_TOPIC_SALUD esta vacio: no se envio a nadie")
        return False

    sent = False

    # ---- aviso anticipado, a partir del pronostico
    if state.is_risky_soon and _cooldown_ok_hours(
            "presion_previo", config.PRESSURE_ALERT_COOLDOWN_H):
        cuando = state.forecast_drop_at or "en las proximas horas"
        horas = state.forecast_drop_in_h
        margen = f" (en ~{horas:.0f} h)" if horas else ""
        cuerpo = [
            f"Se espera una caida de {state.forecast_drop:.0f} hPa "
            f"en 24 horas, con el punto mas bajo el {cuando}{margen}.",
            "",
            f"Nivel: {state.level}.",
        ]
        if state.now_msl:
            cuerpo.append(f"Ahora: {state.now_msl:.0f} hPa a nivel del mar.")
        cuerpo.append("")
        cuerpo.append("Buen momento para tener a la mano lo que te funcione.")
        if send("Presion en descenso mañana", "\n".join(cuerpo),
                priority="default", tags="chart_with_downwards_trend",
                topic=config.NTFY_TOPIC_SALUD):
            _mark("presion_previo")
            sent = True

    # ---- confirmacion cuando la caida ya esta ocurriendo
    if state.is_falling_now and _cooldown_ok_hours(
            "presion_ahora", config.PRESSURE_LIVE_COOLDOWN_H):
        cuerpo = [
            f"La presion bajo {abs(state.change_3h):.1f} hPa en las ultimas "
            "3 horas.",
        ]
        if state.change_24h is not None:
            cuerpo.append(f"En 24 horas: {state.change_24h:+.1f} hPa.")
        if state.now_msl:
            cuerpo.append(f"Ahora: {state.now_msl:.0f} hPa a nivel del mar.")
        if send("La presion esta bajando ahora", "\n".join(cuerpo),
                priority="high", tags="arrow_down_small",
                topic=config.NTFY_TOPIC_SALUD):
            _mark("presion_ahora")
            sent = True

    return sent


def _cooldown_ok(key: str, minutes: float | None = None) -> bool:
    state = store.load_json(config.STATE_JSON, {})
    last = state.get(f"alert_{key}")
    if not last:
        return True
    from datetime import datetime
    try:
        prev = datetime.fromisoformat(last)
    except ValueError:
        return True
    age_min = (store.now_utc() - prev).total_seconds() / 60.0
    return age_min >= (minutes if minutes is not None
                       else config.ALERT_COOLDOWN_MIN)


def _mark(key: str) -> None:
    state = store.load_json(config.STATE_JSON, {})
    state[f"alert_{key}"] = store.now_utc().isoformat()
    store.save_json(config.STATE_JSON, state)


def maybe_alert(result: dict) -> bool:
    """Decide si vale la pena molestarte y manda la alerta."""
    probs = result.get("probabilities") or {}
    eta = result.get("cell_eta_min")
    speed = result.get("motion_speed_kmh") or 0
    origen = result.get("motion_from") or "?"

    # la ventana relevante: probabilidad maxima dentro del plazo de alerta
    inside = {int(k): float(v) for k, v in probs.items()
              if v is not None and int(k) <= config.ALERT_MAX_ETA_MIN}
    if not inside:
        return False
    best_lead = max(inside, key=lambda k: inside[k])
    best_p = inside[best_lead]

    if best_p < config.ALERT_PROB_THRESHOLD:
        return False

    key = "storm"
    if not _cooldown_ok(key):
        log.info("alerta suprimida por cooldown")
        return False

    if eta is not None and eta <= config.ALERT_MAX_ETA_MIN:
        cuando = f"llega en ~{int(eta)} min"
    else:
        cuando = f"en ~{best_lead} min"

    intensidad = "Tormenta" if best_p >= 0.75 else "Lluvia probable"
    title = f"{intensidad} — {cuando}"
    lines = [
        f"Probabilidad: {best_p * 100:.0f}%",
        f"Viene del {origen} a {speed:.0f} km/h",
    ]
    if result.get("cell_km"):
        lines.append(f"Celda mas cercana: {result['cell_km']:.0f} km")
    conf = result.get("confidence")
    if conf:
        lines.append(f"Confianza del sistema: {conf}")
    trend = result.get("growth")
    if trend:
        if trend > 1.15:
            lines.append("La celda esta creciendo")
        elif trend < 0.85:
            lines.append("La celda se esta disipando")

    ok = send(title, "\n".join(lines),
              priority="high" if best_p >= 0.75 else "default",
              tags="cloud_with_lightning_and_rain" if best_p >= 0.75 else "umbrella")
    if ok:
        _mark(key)
    return ok


def _fase_previa() -> str:
    return store.load_json(config.STATE_JSON, {}).get("fase_tormenta", "despejado")


def _guardar_fase(fase: str) -> None:
    state = store.load_json(config.STATE_JSON, {})
    state["fase_tormenta"] = fase
    store.save_json(config.STATE_JSON, state)


def maybe_storm_alert(t) -> bool:
    """Avisa de tormenta electrica cercana, para dar tiempo a prepararse.

    Se avisa por TRANSICION de fase, no por estado: mientras la tormenta
    siga encima no se repite el aviso. Lo que se notifica es el cambio, que
    es lo unico que aporta informacion nueva.

    El tono evita el alarmismo a proposito. Quien recibe esto no necesita
    que le digan que una tormenta es peligrosa; necesita saber cuanto
    tiempo tiene y cuando puede bajar la guardia.
    """
    previa = _fase_previa()
    fase = t.fase
    if fase == previa:
        return False

    if not config.NTFY_TOPIC_SALUD:
        if fase in ("acercandose", "encima"):
            log.warning("habia un aviso de tormenta que dar, pero "
                        "NTFY_TOPIC_SALUD esta vacio: no se envio a nadie")
        _guardar_fase(fase)
        return False

    enviado = False
    dist = t.dist_cercano_km if t.dist_cercano_km is not None else t.dist_min_hora_km

    # --- se acerca: el aviso con margen para preparar al gato
    if fase == "acercandose" and previa in ("despejado", "vigilando") \
            and dist is not None \
            and _cooldown_ok_hours("tormenta_lejos", config.RAYOS_COOLDOWN_H):
        cuerpo = [f"Hay rayos a unos {dist:.0f} km y la actividad viene hacia aca."]
        if t.tendencia_km is not None:
            # Los bloques son de 15 min, asi que la tendencia por bloque
            # multiplicada por 4 da la velocidad de aproximacion por hora.
            kmh = abs(t.tendencia_km) * 4
            if kmh > 5:
                minutos = dist / kmh * 60
                cuerpo.append(f"A este ritmo se oiria en {minutos:.0f}-"
                              f"{minutos * 1.5:.0f} minutos.")
        cuerpo += ["", "Puede que se desvie y no llegue.",
                   "Buen momento para preparar el rincon tranquilo."]
        if send("Tormenta electrica acercandose", "\n".join(cuerpo),
                priority="default", tags="zap", topic=config.NTFY_TOPIC_SALUD):
            _mark("tormenta_lejos")
            enviado = True

    # --- ya esta encima: los truenos se oyen
    elif fase == "encima" and previa != "encima" and dist is not None \
            and _cooldown_ok_hours("tormenta_cerca", config.RAYOS_COOLDOWN_H):
        cuerpo = [f"Rayos a {dist:.0f} km. A esta distancia los truenos ya se oyen."]
        if t.destellos_cerca:
            cuerpo.append(f"{t.destellos_cerca} destellos cerca en los "
                          "ultimos 15 minutos.")
        cuerpo += ["", "Te aviso cuando pase."]
        if send("Tormenta electrica encima", "\n".join(cuerpo),
                priority="high", tags="zap", topic=config.NTFY_TOPIC_SALUD):
            _mark("tormenta_cerca")
            enviado = True

    # --- ya paso: la parte que suele faltar en estos sistemas
    elif fase == "despejado" and previa in ("encima", "acercandose"):
        minutos = t.minutos_sin_actividad or config.RAYOS_DESPEJADO_MIN
        if send("Ya paso la tormenta",
                f"Sin rayos cerca desde hace {minutos} minutos.\n"
                "El gato puede volver a la normalidad.",
                priority="low", tags="white_check_mark",
                topic=config.NTFY_TOPIC_SALUD):
            enviado = True

    _guardar_fase(fase)
    return enviado
