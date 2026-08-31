"""Persistencia. Todo en CSV plano para que puedas abrirlo en Excel."""
from __future__ import annotations

import csv
import logging
import json
import os
from datetime import datetime, timezone

from . import config

PRED_FIELDS = [
    "issued_utc", "valid_utc", "lead_min",
    "p_final", "p_radar", "p_ir", "p_models",
    "w_radar", "w_ir", "w_models",
    "score_radar", "score_ir",
    "motion_speed_kmh", "motion_from", "motion_conf", "growth",
    "cell_eta_min", "cell_km", "cell_intensity",
    "cape", "radar_coverage",
    # Presion en el momento de emitir. No entra en el pronostico: se guarda
    # para poder responder con datos una pregunta que aparecio sola.
    #
    # La noche del granizo del 30 de agosto, el aviso de presion salio a las
    # 19:31 y la lluvia empezo a las 19:45. El aviso de lluvia -que es el que
    # deberia haber avisado- salio a las 20:02. El barometro le gano al
    # satelite por media hora larga, y tiene sentido fisico: una celda
    # convectiva hace caer la presion antes de que su tope nuboso se enfrie
    # lo suficiente para que el infrarrojo la vea.
    #
    # Un caso no es evidencia. Guardando estas columnas junto a cada
    # prediccion, en unas semanas se puede comprobar si la caida de presion
    # de verdad anticipa la lluvia AQUI, en vez de decidirlo por intuicion.
    "pres_1h", "pres_3h", "pres_nivel", "pres_fuente",
]

log = logging.getLogger(__name__)

OBS_FIELDS = ["valid_utc", "rained", "mm", "peak_score", "source"]


def _ensure(path: str, fields: list[str]) -> None:
    """Crea el archivo si falta y lo migra si le faltan columnas.

    La migracion no es un lujo: sin ella, añadir una columna corrompe en
    silencio todo el historial. El archivo conserva la cabecera vieja, las
    filas nuevas se escriben con la lista nueva de campos, y a partir de esa
    linea cada valor queda bajo el nombre equivocado. Con 14,500 pares de
    prediccion y observacion dentro -que son semanas de aprendizaje- eso no
    se recupera, y ademas no da ningun error: simplemente el sistema empieza
    a aprender de datos desplazados.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=fields).writeheader()
        return

    with open(path, newline="", encoding="utf-8") as fh:
        cabecera = next(csv.reader(fh), [])
    if cabecera == fields:
        return

    faltan = [c for c in fields if c not in cabecera]
    sobran = [c for c in cabecera if c not in fields]
    if sobran:
        # Quitar columnas destruiria datos. Se avisa y no se toca nada: es
        # preferible no escribir a escribir mal.
        log.error("%s tiene columnas que el codigo ya no conoce (%s); "
                  "no se migra para no perder datos", path, sobran)
        return
    log.info("migrando %s: se añaden las columnas %s", path, faltan)

    # Reescritura atomica: se escribe al lado y se renombra. Si el proceso
    # muere a mitad, el archivo original sigue intacto.
    tmp = path + ".migrando"
    with open(path, newline="", encoding="utf-8") as viejo_fh, \
            open(tmp, "w", newline="", encoding="utf-8") as nuevo_fh:
        lector = csv.DictReader(viejo_fh)
        escritor = csv.DictWriter(nuevo_fh, fieldnames=fields,
                                  extrasaction="ignore")
        escritor.writeheader()
        for fila in lector:
            escritor.writerow({c: fila.get(c, "") for c in fields})
    os.replace(tmp, path)


def append_predictions(rows: list[dict]) -> None:
    if not rows:
        return
    _ensure(config.PREDICTIONS_CSV, PRED_FIELDS)
    with open(config.PREDICTIONS_CSV, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=PRED_FIELDS, extrasaction="ignore")
        for row in rows:
            w.writerow(row)


def append_observations(rows: list[dict]) -> None:
    if not rows:
        return
    _ensure(config.OBSERVATIONS_CSV, OBS_FIELDS)
    existing = {r["valid_utc"] for r in read_observations()}
    fresh = [r for r in rows if r["valid_utc"] not in existing]
    if not fresh:
        return
    with open(config.OBSERVATIONS_CSV, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=OBS_FIELDS, extrasaction="ignore")
        for row in fresh:
            w.writerow(row)


def read_predictions() -> list[dict]:
    if not os.path.exists(config.PREDICTIONS_CSV):
        return []
    with open(config.PREDICTIONS_CSV, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def read_observations() -> list[dict]:
    if not os.path.exists(config.OBSERVATIONS_CSV):
        return []
    with open(config.OBSERVATIONS_CSV, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False, default=str)


def prune(max_rows: int = 60000) -> None:
    """Evita que el repo crezca sin limite. ~1 año de datos cabe de sobra."""
    rows = read_predictions()
    if len(rows) <= max_rows:
        return
    keep = rows[-max_rows:]
    with open(config.PREDICTIONS_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=PRED_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(keep)


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def round_slot(dt: datetime, minutes: int = 15) -> str:
    """Normaliza a bloques de 15 min para poder cruzar prediccion y observacion."""
    dt = dt.replace(second=0, microsecond=0)
    dt = dt.replace(minute=(dt.minute // minutes) * minutes)
    return dt.isoformat()
