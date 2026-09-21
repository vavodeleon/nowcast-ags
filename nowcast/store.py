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
    # Salud de la corrida que produjo esta fila. Una corrida degradada publica
    # con menos datos -media capa de rayos, sin temperatura, a veces sin
    # archivar- y eso deberia empeorar el pronostico. "Deberia" no es una
    # medicion: con estas dos columnas se puede separar una semana mala por el
    # tiempo de una semana mala por el enlace, que hoy son indistinguibles.
    "degradado", "duracion_s",
    # El infrarrojo en sus dos versiones: la de brillo a secas y la ajustada
    # por crecimiento local y forma del campo. Se guardan LAS DOS a proposito.
    #
    # El ajuste nace de una medicion -separacion -19.3% en las correcciones
    # humanas, el infrarrojo cantando yunques- y de una explicacion fisica que
    # encaja. Las dos cosas juntas siguen sin ser una comprobacion: la unica
    # forma de saber si ayudo es comparar las dos series sobre los mismos casos,
    # y para eso hay que haberlas guardado desde el primer dia.
    "p_ir_crudo", "ir_tend", "ir_compac",
    # La deriva del nucleo, medida con descargas. Se guarda tambien cuando no
    # se usa -confianza baja- para poder medir despues cuanto discrepa del
    # infrarrojo sin tener que esperar a otra temporada.
    "deriva_desde", "deriva_kmh", "deriva_conf",
]

log = logging.getLogger(__name__)

OBS_FIELDS = ["valid_utc", "rained", "mm", "peak_score", "source"]

# Episodios de migrana. Una fila por aviso enviado; 'dolor' llega despues,
# cuando ella contesta desde la notificacion. Se guarda la presion tal como
# estaba al avisar, porque es lo que hay que poder correlacionar luego: sin
# eso solo quedaria "aviso a las 19:31" sin saber con que numeros.
SALUD_FIELDS = [
    "ts_aviso", "tipo", "change_1h", "change_3h", "change_24h",
    "nivel", "fuente", "dolor", "ts_respuesta",
]


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


# Quien gana cuando dos fuentes describen el MISMO instante.
#
# El orden no es arbitrario: es orden de independencia. `evaluar.py` descubrio
# que la "verdad" de Open-Meteo es un producto derivado de modelos numericos, y
# que los modelos que se evaluan son de esa misma familia. Se califican con un
# examen que ellos escribieron. Una persona que vio el cielo -por la pagina, por
# la notificacion o por radio- es la unica verdad independiente que existe aqui.
#
# 'manual' y 'malla' valen lo mismo a proposito: los dos son el mismo ojo humano
# por dos caminos distintos. El camino no cambia la calidad del dato.
PRIORIDAD_FUENTE = {"manual": 3, "malla": 3, "ir+openmeteo": 2, "openmeteo": 1}


def _prioridad(fuente) -> int:
    return PRIORIDAD_FUENTE.get(str(fuente or "").strip(), 0)


def append_observations(rows: list[dict]) -> None:
    """Incorpora observaciones. Una fuente mejor SUSTITUYE a una peor.

    Hasta el 20 de septiembre esto solo añadia lo que faltaba: cualquier fila
    para un instante que ya tuviera dato se descartaba en silencio. Parecia
    inofensivo porque las confirmaciones humanas suelen llegar antes que la
    verificacion de Open-Meteo, que corre horas despues.

    Pero al reves tambien pasa -confirmas por radio una tormenta de anoche, o
    contestas al aviso a la mañana siguiente- y entonces se tiraba justo el
    unico dato que no es circular, sin un solo error en ningun log. El sintoma
    habria sido que las confirmaciones "no sirven de nada", con `evaluar.py`
    diciendo que hay 35 manuales y ninguna cambiando un veredicto.
    """
    if not rows:
        return
    _ensure(config.OBSERVATIONS_CSV, OBS_FIELDS)
    actuales = {r["valid_utc"]: r for r in read_observations()}

    nuevas, mejoras = [], {}
    for row in rows:
        vieja = actuales.get(row["valid_utc"])
        if vieja is None:
            nuevas.append(row)
            actuales[row["valid_utc"]] = row
        elif _prioridad(row.get("source")) > _prioridad(vieja.get("source")):
            mejoras[row["valid_utc"]] = row
            log.info("observacion de %s sustituye a la de %s en %s",
                     row.get("source"), vieja.get("source"), row["valid_utc"])

    if mejoras:
        _reescribir_observaciones(mejoras)
    if nuevas:
        with open(config.OBSERVATIONS_CSV, "a", newline="",
                  encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=OBS_FIELDS,
                               extrasaction="ignore")
            for row in nuevas:
                w.writerow(row)


def _reescribir_observaciones(mejoras: dict[str, dict]) -> None:
    """Sustituye filas por instante, con el mismo cuidado que la migracion.

    Se escribe al lado y se renombra: si el proceso muere a mitad, el archivo
    original queda intacto. Reescribir en sitio un archivo con miles de pares
    de aprendizaje dentro no es algo que convenga hacer de otra forma.
    """
    ruta = config.OBSERVATIONS_CSV
    tmp = ruta + ".sustituyendo"
    with open(ruta, newline="", encoding="utf-8") as viejo_fh, \
            open(tmp, "w", newline="", encoding="utf-8") as nuevo_fh:
        lector = csv.DictReader(viejo_fh)
        escritor = csv.DictWriter(nuevo_fh, fieldnames=OBS_FIELDS,
                                  extrasaction="ignore")
        escritor.writeheader()
        for fila in lector:
            escritor.writerow(mejoras.get(fila.get("valid_utc", ""), fila))
    os.replace(tmp, ruta)


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


def append_salud(fila: dict) -> None:
    _ensure(config.SALUD_CSV, SALUD_FIELDS)
    with open(config.SALUD_CSV, "a", newline="", encoding="utf-8") as fh:
        csv.DictWriter(fh, fieldnames=SALUD_FIELDS,
                       extrasaction="ignore").writerow(fila)


def read_salud() -> list[dict]:
    if not os.path.exists(config.SALUD_CSV):
        return []
    with open(config.SALUD_CSV, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def responder_salud(ts_aviso: str, dolor: str, ts_respuesta: str) -> bool:
    """Rellena la respuesta del aviso mas cercano que siga sin contestar.

    Devuelve True si encontro a quien asignarsela. No se exige coincidencia
    exacta de marca de tiempo: ella puede tardar en contestar y el boton
    manda la hora del aviso, pero mas vale ser tolerante que perder el dato.
    """
    filas = read_salud()
    if not filas:
        return False
    candidatas = [f for f in filas if not f.get("dolor")]
    if not candidatas:
        return False
    exacta = [f for f in candidatas if f.get("ts_aviso") == ts_aviso]
    elegida = exacta[0] if exacta else candidatas[-1]
    elegida["dolor"] = dolor
    elegida["ts_respuesta"] = ts_respuesta

    tmp = config.SALUD_CSV + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=SALUD_FIELDS, extrasaction="ignore")
        w.writeheader()
        for f in filas:
            w.writerow(f)
    os.replace(tmp, config.SALUD_CSV)
    return True
