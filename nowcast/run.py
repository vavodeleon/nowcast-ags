"""Orquestador. Esto es lo que corre cada 15 minutos en GitHub Actions."""
from __future__ import annotations

import argparse
import logging
import math
from datetime import datetime, timedelta, timezone

import numpy as np

from . import (archivo, calibrate, config, engine, feedback, lightning, malla,
               notify, overhead, salud,
               pressure, render, sources, store, verify)

log = logging.getLogger(__name__)


def _logistic(score: float, slope: float = 8.0, offset: float = -2.5) -> float:
    """Convierte una intensidad [0,1] en probabilidad. Prior; luego se calibra."""
    return float(1.0 / (1.0 + math.exp(-(slope * score + offset))))


def _model_probabilities(data: dict) -> tuple[dict[int, float], dict[str, float], float]:
    """Probabilidad de lluvia por horizonte segun el consenso de modelos.

    Devuelve (prob por lead, prob por modelo a +1h, CAPE).
    """
    if not data:
        return {}, {}, 0.0

    now = datetime.now(timezone.utc)
    per_lead: dict[int, list[float]] = {lead: [] for lead in config.LEAD_TIMES_MIN}
    per_model: dict[str, float] = {}
    capes: list[float] = []

    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        return {}, {}, 0.0

    parsed = []
    for ts in times:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            parsed.append(dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))
        except ValueError:
            parsed.append(None)

    for model in config.OPENMETEO_MODELS:
        key = f"precipitation_probability_{model}"
        series = hourly.get(key)
        if series is None and len(config.OPENMETEO_MODELS) == 1:
            series = hourly.get("precipitation_probability")
        if not series:
            continue

        cape_series = hourly.get(f"cape_{model}") or hourly.get("cape") or []

        for lead in config.LEAD_TIMES_MIN:
            target = now + timedelta(minutes=lead)
            best_i, best_gap = None, None
            for i, dt in enumerate(parsed):
                if dt is None or i >= len(series) or series[i] is None:
                    continue
                gap = abs((dt - target).total_seconds())
                if best_gap is None or gap < best_gap:
                    best_i, best_gap = i, gap
            if best_i is not None and best_gap is not None and best_gap <= 5400:
                per_lead[lead].append(float(series[best_i]) / 100.0)
                if lead == 60:
                    per_model[model] = round(float(series[best_i]) / 100.0, 3)

        for i, dt in enumerate(parsed):
            if dt is None or i >= len(cape_series) or cape_series[i] is None:
                continue
            if 0 <= (dt - now).total_seconds() <= 6 * 3600:
                capes.append(float(cape_series[i]))

    out = {lead: (float(np.mean(vals)) if vals else 0.0)
           for lead, vals in per_lead.items()}
    cape = float(max(capes)) if capes else 0.0
    return out, per_model, cape


def _confidence_label(radar_nc, ir_nc, cov: dict) -> str:
    """El infrarrojo pesa doble: aqui es el sensor que de verdad ve la ciudad."""
    votes = 0
    if ir_nc and ir_nc.motion.confidence > 0.4:
        votes += 2
    if radar_nc and radar_nc.motion.confidence > 0.4 and cov.get("usable"):
        votes += 1
    return {0: "baja", 1: "media", 2: "buena", 3: "alta"}[min(votes, 3)]


def build_forecast() -> dict:
    """Corre el ciclo completo y devuelve el pronostico."""
    issued = store.now_utc()

    # Presupuesto de tiempo. Ver el comentario de PRESUPUESTO_S en config.py:
    # con el enlace saturado una corrida llego a 893 s, a siete segundos del
    # limite de systemd. Pasado el presupuesto se renuncia a lo opcional en
    # vez de arriesgar que maten la corrida a media escritura.
    import time as _time
    limite = _time.monotonic() + config.PRESUPUESTO_S

    def queda(margen: float = 0.0) -> bool:
        return _time.monotonic() < limite - margen

    cal = store.load_json(config.CALIBRATION_JSON, {})

    cov = sources.radar_coverage()
    coverage = cov["home"]
    if not cov["usable"]:
        log.info("radar sin cobertura util sobre la ciudad (%.0f%%); "
                 "el infrarrojo lleva el peso", coverage * 100)

    radar_frames = sources.fetch_radar_frames(n=5) if cov["usable"] else []
    ir_frames = sources.fetch_ir_frames(n=5)

    radar_nc = engine.run_nowcast(radar_frames) if len(radar_frames) >= 2 else None
    ir_nc = engine.run_nowcast(ir_frames) if len(ir_frames) >= 2 else None

    model_data = sources.fetch_models()
    model_probs, per_model, cape = _model_probabilities(model_data)

    # presion atmosferica (seguimiento aparte, para las migranas)
    try:
        pres = pressure.fetch()
    except Exception as exc:
        log.error("seguimiento de presion fallo: %s", exc)
        pres = pressure.PressureState()

    # temperatura (seccion aparte de la pagina)
    temperatura = {}
    if queda():
        try:
            temperatura = sources.fetch_temperature()
        except Exception as exc:
            log.error("temperatura fallo: %s", exc)
    else:
        log.warning("presupuesto agotado: se omite la temperatura")

    # rayos detectados por el GLM
    bloques_rayos = []
    tormenta = None
    try:
        rayos = lightning.update(limite=limite)
        bloques_rayos = rayos.get("bloques", [])
        rayos_resumen = {"total_hora": rayos.get("total_hora", 0),
                         "bloques": len(bloques_rayos)}
        # La fase anterior hace falta para la histeresis: sin ella, una celda
        # rondando el umbral cambiaria de estado cada quince minutos.
        tormenta = lightning.evaluar(rayos, notify._fase_previa())
        rayos_resumen["fase"] = tormenta.fase
        # SIEMPRE presente, null cuando no hay dato. Antes se omitia la clave,
        # y omitir no es lo mismo que decir "no se": quien lee el JSON con un
        # `.get("dist_km", 0)` -que es lo natural- obtiene 0 km, o sea "encima
        # de ti", que es la alerta mas alarmante del sistema. Un centinela
        # numerico para "sin dato" produce falsos positivos en la peor linea
        # posible; `null` revienta ruidosamente, que es lo que hace falta.
        #
        # Y no puede confundirse con una tormenta de verdad a 0 km: el pixel de
        # GOES mide 2.44 km, asi que la distancia minima medible ronda 1 km y
        # nunca es exactamente cero.
        rayos_resumen["dist_km"] = (round(tormenta.dist_cercano_km, 1)
                                    if tormenta.dist_cercano_km is not None
                                    else None)
        rayos_resumen["dist_min_hora_km"] = (
            round(tormenta.dist_min_hora_km, 1)
            if tormenta.dist_min_hora_km is not None else None)
        # Registrar la fase ANTERIOR: sin eso no hay forma de saber por que
        # un aviso salio o no salio, porque se avisa por transicion.
        log.info("tormenta: %s (antes %s), mas cercano %s km, "
                 "%s destellos en la hora",
                 tormenta.fase, notify._fase_previa(),
                 f"{tormenta.dist_cercano_km:.0f}" if tormenta.dist_cercano_km
                 else "ninguno",
                 tormenta.destellos_hora)
    except Exception as exc:
        log.error("rayos fallaron: %s", exc)
        # Con las mismas claves que la ruta buena: un consumidor no deberia
        # tener que distinguir "fallaron los rayos" de "no hubo rayos" para
        # saber que campos existen.
        rayos_resumen = {"total_hora": 0, "bloques": 0, "fase": "despejado",
                         "dist_km": None, "dist_min_hora_km": None}

    # --- La deriva medida con rayos manda sobre la del infrarrojo
    #
    # Lo noto Alvaro mirando el cono contra las tormentas reales: apuntaba a
    # otro lado. El infrarrojo sigue el techo de la nube y con cizalladura ese
    # techo va a lo suyo. Donde hay descargas esta la celda de verdad.
    #
    # Solo se sustituye cuando hay tormenta electrica y la medida es firme.
    # La mayor parte del tiempo no hay rayos y no cambia nada; aparece
    # justamente cuando hay algo de lo que avisar.
    deriva = None
    try:
        deriva = lightning.deriva(rayos) if bloques_rayos else None
    except Exception as exc:
        log.error("no se pudo medir la deriva por rayos: %s", exc)
    # Dos filtros, no uno. La confianza mide si ESTA estimacion se sostiene
    # sola; la persistencia, si coincide con la de hace quince minutos. La
    # medicion del 21/09/2026 mostro que hacen falta las dos: habia casos con
    # mil descargas -confianza alta- cuyo rumbo saltaba noventa grados entre
    # cuadros seguidos. Confianza alta y ruido puro no son incompatibles.
    usa_deriva = (deriva is not None and deriva.bearing_deg is not None
                  and deriva.confianza >= config.DERIVA_CONFIANZA_MIN)
    if deriva is not None:
        try:
            usa_deriva = usa_deriva and lightning.deriva_persistente(deriva)
        except Exception as exc:
            log.error("no se pudo comprobar la persistencia: %s", exc)
            usa_deriva = False
    if usa_deriva and ir_frames:
        km_px = ir_frames[-1].km_per_px
        rad = math.radians(deriva.bearing_deg)
        norte_km_min = deriva.speed_kmh / 60.0 * math.cos(rad)
        este_km_min = deriva.speed_kmh / 60.0 * math.sin(rad)
        mov_rayos = engine.Motion(
            vy_px_min=-norte_km_min / km_px,   # la fila crece hacia el sur
            vx_px_min=este_km_min / km_px,
            speed_kmh=deriva.speed_kmh,
            bearing_deg=deriva.bearing_deg,
            confidence=deriva.confianza)
        for nc in (ir_nc, radar_nc):
            if nc is not None:
                engine.recolocar_celda(
                    nc, ir_frames if nc is ir_nc else radar_frames, mov_rayos)
        log.info("deriva por rayos: del %s a %.0f km/h (confianza %.2f, "
                 "%s destellos); el infrarrojo decia del %s a %.0f km/h",
                 deriva.from_direction, deriva.speed_kmh, deriva.confianza,
                 deriva.destellos,
                 (ir_nc.motion.from_direction if ir_nc else "?"),
                 (ir_nc.motion.speed_kmh if ir_nc else 0))

    # ¿Que pasa AHORA sobre la ciudad? Requiere corroboracion: el infrarrojo
    # solo no distingue una celda que llueve del yunque de una tormenta lejana.
    precip_obs = None
    try:
        precip_obs = sources.precip_hora_actual()
    except Exception as exc:
        log.debug("sin precipitacion observada: %s", exc)

    ahora = overhead.assess(ir_frames[-1] if ir_frames else None,
                            bloques_rayos, precip_obs)

    # imagen del satelite reproyectada, para el mapa
    map_bounds = None
    if ir_frames:
        try:
            import os
            os.makedirs(os.path.dirname(config.LATEST_JSON), exist_ok=True)
            ruta_png = os.path.join(os.path.dirname(config.LATEST_JSON),
                                    "satelite.png")
            map_bounds = render.render(ir_frames[-1], ruta_png)
        except Exception as exc:
            log.error("no se pudo generar la imagen del satelite: %s", exc)

        # Archivar para la animacion y el historial. Se copia el PNG que ya se
        # genero en vez de reproyectar otra vez: en un Pi 3 eso cuesta varios
        # segundos y daria exactamente lo mismo.
        if queda():
            try:
                # El bloque MAS RECIENTE por su marca de tiempo, no el
                # ultimo de la lista. El orden de `bloques` no esta
                # garantizado -`evaluar()` los ordena por su cuenta, que es
                # la pista de que alguna vez no venian en orden- y archivar
                # el bloque equivocado deja el historial sin rayos justo en
                # las tormentas, que es cuando se quiere revisar.
                recientes = sorted((b for b in bloques_rayos if b.get("puntos")),
                                   key=lambda b: b.get("t", ""))
                puntos_ahora = recientes[-1].get("puntos") if recientes else None
                archivo.guardar(issued, ruta_png, map_bounds, puntos_ahora)
                archivo.podar()
            except Exception as exc:
                log.error("no se pudo archivar el cuadro: %s", exc)
        else:
            log.warning("presupuesto agotado: no se archiva este cuadro")

    # el movimiento que reportamos es el de la fuente mas confiable
    motion = None
    for candidate in (radar_nc, ir_nc):
        if candidate and (motion is None
                          or candidate.motion.confidence > motion.confidence):
            motion = candidate.motion
    if motion is None:
        motion = engine.Motion()

    growth = (radar_nc.growth if radar_nc
              else (ir_nc.growth if ir_nc else 1.0))

    probabilities: dict[str, float] = {}
    rows: list[dict] = []

    for lead in config.LEAD_TIMES_MIN:
        w = dict(calibrate.weights_for(lead, cal))

        p_radar = _logistic(radar_nc.score_at(lead)) if radar_nc else 0.0
        # el IR ve topes nubosos frios, que no siempre llueven: pendiente menor
        p_ir = _logistic(ir_nc.score_at(lead), slope=6.5, offset=-2.6) if ir_nc else 0.0
        # La misma cuenta sin el ajuste por yunque, para poder comparar las dos
        # sobre los mismos casos. No se usa para nada mas.
        p_ir_crudo = (_logistic(ir_nc.score_crudo_at(lead), slope=6.5,
                                offset=-2.6) if ir_nc else 0.0)
        ir_tend, ir_compac = ir_nc.detalle_at(lead) if ir_nc else (0.0, 1.0)
        p_models = model_probs.get(lead, 0.0)

        # Sin datos de una fuente, su peso se reparte entre las demas.
        # "No veo tan lejos" no es lo mismo que "no va a llover": si el origen
        # del horizonte cae fuera de la imagen, esa fuente no vota.
        if radar_nc is None or not cov["usable"] or not radar_nc.sees(lead):
            w["radar"] = 0.0
        if ir_nc is None or not ir_nc.sees(lead):
            w["ir"] = 0.0
        if not model_probs:
            w["models"] = 0.0
        total = sum(w.values())
        if total <= 0:
            # ninguna fuente puede opinar de este horizonte. Decir "0%" seria
            # mentir; se reporta como desconocido y el tablero lo muestra asi.
            probabilities[str(lead)] = None
            rows.append({
                "issued_utc": issued.isoformat(),
                "valid_utc": store.round_slot(issued + timedelta(minutes=lead)),
                "lead_min": lead, "p_final": "", "p_radar": "", "p_ir": "",
                "p_models": "", "w_radar": 0, "w_ir": 0, "w_models": 0,
                "radar_coverage": round(coverage, 3),
            })
            continue
        w = {k: v / total for k, v in w.items()}

        raw = (w.get("radar", 0) * p_radar
               + w.get("ir", 0) * p_ir
               + w.get("models", 0) * p_models)
        p_final = calibrate.apply_curve(raw, lead, cal)

        probabilities[str(lead)] = round(p_final, 3)
        rows.append({
            "issued_utc": issued.isoformat(),
            "valid_utc": store.round_slot(issued + timedelta(minutes=lead)),
            "lead_min": lead,
            "p_final": round(p_final, 4),
            "p_radar": round(p_radar, 4),
            "p_ir": round(p_ir, 4),
            "p_models": round(p_models, 4),
            "w_radar": round(w.get("radar", 0), 3),
            "w_ir": round(w.get("ir", 0), 3),
            "w_models": round(w.get("models", 0), 3),
            "score_radar": round(radar_nc.score_at(lead), 4) if radar_nc else "",
            "score_ir": round(ir_nc.score_at(lead), 4) if ir_nc else "",
            "p_ir_crudo": round(p_ir_crudo, 4) if ir_nc else "",
            "ir_tend": round(ir_tend, 4) if ir_nc else "",
            "ir_compac": round(ir_compac, 3) if ir_nc else "",
            "motion_speed_kmh": round(motion.speed_kmh, 1),
            "motion_from": motion.from_direction,
            "motion_conf": round(motion.confidence, 3),
            "motion_bearing": (round(motion.bearing_deg, 1)
                               if motion.bearing_deg is not None else ""),
            "deriva_desde": deriva.from_direction if usa_deriva else "",
            "deriva_kmh": deriva.speed_kmh if usa_deriva else "",
            "deriva_conf": (deriva.confianza if deriva is not None else ""),
            "growth": round(growth, 3),
            "cell_eta_min": (radar_nc or ir_nc).nearest_cell_eta_min if (radar_nc or ir_nc) else "",
            "cell_km": (radar_nc or ir_nc).nearest_cell_km if (radar_nc or ir_nc) else "",
            "cell_intensity": (radar_nc or ir_nc).nearest_cell_intensity if (radar_nc or ir_nc) else "",
            "cape": round(cape, 0),
            "radar_coverage": round(coverage, 3),
            # ver el comentario de PRED_FIELDS en store.py
            "pres_1h": pres.change_1h if pres else "",
            "pres_3h": pres.change_3h if pres else "",
            "pres_nivel": pres.level if pres else "",
            "pres_fuente": pres.fuente if pres else "",
        })

    # el infrarrojo manda: es el sensor que ve la ciudad
    primary = ir_nc or radar_nc
    primary_frames = ir_frames or radar_frames
    km_per_px = round(primary_frames[-1].km_per_px, 2) if primary_frames else None
    result = {
        "issued_utc": issued.isoformat(),
        "issued_local": issued.astimezone(config.TZ).strftime("%Y-%m-%d %H:%M"),
        "probabilities": probabilities,
        "raining_now": round(primary.current_score, 3) if primary else 0.0,
        "ahora": ahora.to_dict(),
        # Cuando los rayos hablan, mandan ellos. El significado del campo no
        # cambia -"de donde vienen las celdas"- asi que la malla sigue
        # imprimiendolo igual; lo que cambia es que ahora es cierto. De donde
        # sale lo dice `motion_fuente`, porque un dato sin procedencia no se
        # puede auditar despues.
        "motion_speed_kmh": round(
            (deriva.speed_kmh if usa_deriva else motion.speed_kmh), 1),
        "motion_from": (deriva.from_direction if usa_deriva
                        else motion.from_direction),
        "motion_confidence": round(
            (deriva.confianza if usa_deriva else motion.confidence), 3),
        "motion_fuente": "rayos (nucleo)" if usa_deriva else "infrarrojo (topes)",
        "deriva": ({"desde": deriva.from_direction,
                    "bearing": round(deriva.bearing_deg, 1),
                    "kmh": deriva.speed_kmh,
                    "confianza": deriva.confianza,
                    "destellos": deriva.destellos,
                    "usada": usa_deriva}
                   if deriva is not None and deriva.bearing_deg is not None
                   else None),
        # El del infrarrojo se conserva SIEMPRE, aunque no se use. Sin las dos
        # series no se puede medir despues cuanto discrepan, que es justo la
        # pregunta que abrio todo esto.
        "motion_ir_from": motion.from_direction,
        "motion_ir_kmh": round(motion.speed_kmh, 1),
        "growth": round(growth, 3),
        "cell_eta_min": primary.nearest_cell_eta_min if primary else None,
        "cell_km": primary.nearest_cell_km if primary else None,
        # La celda como objeto dibujable. Va aparte de `cell_km`/`cell_eta_min`
        # -que siguen igual- porque la malla LoRa los lee y su contrato no debe
        # moverse por un cambio de la pagina.
        "celda": ({
            "lat": primary.nearest_cell_lat,
            "lon": primary.nearest_cell_lon,
            "km": primary.nearest_cell_km,
            "eta_min": primary.nearest_cell_eta_min,
            "intensidad": primary.nearest_cell_intensity,
            "radio_km": primary.nearest_cell_radio_km,
        } if primary and primary.nearest_cell_lat is not None else None),
        "cape": round(cape, 0),
        "radar_coverage": round(coverage, 3),
        "radar_usable": cov["usable"],
        "primary_sensor": "infrarrojo (GOES-19)" if not cov["usable"] else "radar + IR",
        "sources": {
            "radar_frames": len(radar_frames),
            "ir_frames": len(ir_frames),
            "km_per_px": km_per_px,
            "models": list(per_model.keys()),
        },
        "model_probs_1h": per_model,
        "confidence": _confidence_label(radar_nc, ir_nc, cov),
        "calibration_n": cal.get("n", 0),
        "lat": config.LAT,
        "lon": config.LON,
        "map_bounds": map_bounds,
        "motion_bearing": (round(deriva.bearing_deg, 1) if usa_deriva
                           else (round(motion.bearing_deg, 1)
                                 if motion.bearing_deg is not None else None)),
        "pressure": pressure.to_dict(pres),
        "temperatura": temperatura,
        "rayos": rayos_resumen,
        "duracion_s": round(_time.monotonic() - (limite - config.PRESUPUESTO_S), 1),
        "degradado": not queda(),
        "_tormenta": tormenta,
    }
    result["_rows"] = rows
    result["_pressure"] = pres
    return result


def publish(result: dict) -> None:
    """Escribe lo que consume el dashboard."""
    payload = {k: v for k, v in result.items() if not k.startswith("_")}
    payload["performance"] = verify.recent_performance(days=21)
    store.save_json(config.LATEST_JSON, payload)

    # serie de las ultimas 48 h para la grafica
    preds = store.read_predictions()
    obs = {r["valid_utc"]: r for r in store.read_observations()}
    cutoff = store.now_utc() - timedelta(hours=48)
    series = []
    for row in preds[-4000:]:
        if row.get("lead_min") != "60":
            continue
        try:
            issued = datetime.fromisoformat(row["issued_utc"])
        except (ValueError, KeyError):
            continue
        if issued < cutoff:
            continue
        ob = obs.get(row["valid_utc"])
        series.append({
            "t": row["valid_utc"],
            "p": float(row["p_final"]),
            "y": (float(ob["rained"]) if ob else None),
        })
    store.save_json(config.HISTORY_JSON, {"lead_min": 60, "series": series})


def main() -> None:
    parser = argparse.ArgumentParser(description="Nowcasting para Aguascalientes")
    parser.add_argument("--no-alert", action="store_true",
                        help="calcula pero no manda notificacion")
    parser.add_argument("--verify-only", action="store_true",
                        help="solo verificar y recalibrar")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    # 0. incorporar tus correcciones desde la pagina
    try:
        feedback.procesar()
    except Exception as exc:
        log.error("no se pudo procesar el feedback: %s", exc)

    # 0-bis. y las respuestas del canal de salud, contestadas desde la
    #        propia notificacion
    try:
        salud.procesar()
    except Exception as exc:
        log.error("no se pudieron procesar las respuestas de salud: %s", exc)

    # 0-ter. y las correcciones que llegaron por radio. Van ANTES de verify
    #        a proposito: asi la observacion humana del instante ya esta puesta
    #        cuando Open-Meteo opine sobre esa misma franja, y no depende de
    #        que la sustitucion por prioridad funcione. Que funcione igual, y
    #        haya prueba de ello, es el cinturon; esto es el tirante.
    try:
        malla.procesar()
    except Exception as exc:
        log.error("no se pudieron procesar las respuestas de la malla: %s", exc)

    # 1. verificar lo que ya paso (esto alimenta el aprendizaje)
    try:
        verify.run()
    except Exception as exc:
        log.error("verificacion fallo: %s", exc)

    # 2. recalibrar con el historial acumulado
    try:
        calibrate.refresh()
    except Exception as exc:
        log.error("calibracion fallo: %s", exc)

    if args.verify_only:
        return

    # 3. nuevo pronostico
    result = build_forecast()
    # Se marca cada fila con si la corrida salio degradada. La semana del 14 de
    # septiembre el skill cayo a +0.086 y no hay forma de saber si fue mal
    # tiempo, mala suerte o corridas a medias por el enlace saturado -que ese
    # mes llegaron a tardar 893 s de 900-. Sin la columna, esa pregunta no se
    # puede contestar nunca; con ella, se contesta sola en dos semanas.
    filas = result.pop("_rows")
    for f in filas:
        f["degradado"] = 1 if result.get("degradado") else 0
        f["duracion_s"] = result.get("duracion_s", "")
    store.append_predictions(filas)
    pres = result.pop("_pressure", None)
    tormenta = result.pop("_tormenta", None)
    store.prune()
    publish(result)

    log.info("prob 60 min: %s | viene del %s a %s km/h | confianza %s",
             result["probabilities"].get("60"),
             result["motion_from"], result["motion_speed_kmh"],
             result["confidence"])

    # 4. avisar si toca
    if not args.no_alert:
        notify.maybe_alert(result)
        if pres is not None:
            try:
                notify.maybe_pressure_alert(pres)
            except Exception as exc:
                log.error("alerta de presion fallo: %s", exc)
        if tormenta is not None:
            try:
                notify.maybe_storm_alert(tormenta)
            except Exception as exc:
                log.error("alerta de tormenta fallo: %s", exc)

        # 5. reporte matutino: se comprueba aqui y no con un cron propio,
        #    porque los cron de GitHub no se cumplen
        try:
            from . import daily
            daily.maybe_send_morning()
        except Exception as exc:
            log.error("reporte matutino fallo: %s", exc)


if __name__ == "__main__":
    main()
