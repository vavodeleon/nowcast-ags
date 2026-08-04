"""Notificaciones push al telefono via ntfy.sh (gratis, sin cuenta)."""
from __future__ import annotations

import logging

from . import config, http, store

log = logging.getLogger(__name__)


def send(title: str, message: str, *, priority: str = "default",
         tags: str = "") -> bool:
    if not config.NTFY_TOPIC:
        log.warning("NTFY_TOPIC no configurado; no se envia nada")
        return False
    headers = {
        "Title": title.encode("utf-8").decode("latin-1", errors="replace"),
        "Priority": priority,
    }
    if tags:
        headers["Tags"] = tags
    url = f"{config.NTFY_SERVER.rstrip('/')}/{config.NTFY_TOPIC}"
    ok = http.post(url, message.encode("utf-8"), headers)
    if ok:
        log.info("notificacion enviada: %s", title)
    return ok


def _cooldown_ok(key: str) -> bool:
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
    return age_min >= config.ALERT_COOLDOWN_MIN


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
