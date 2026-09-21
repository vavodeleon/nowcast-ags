"""El puente que faltaba: las correcciones humanas que llegan por radio.

Los comandos `llovio` / `no llovio` de la malla LoRa escriben en la tabla
`respuestas` de `clima.db`. Hasta hoy se quedaban ahi. Este modulo las lee -en
solo lectura estricta, como el barometro- y las incorpora a `observations.csv`.

## Por que vale mas que cualquier otra observacion

`evaluar.py` dejo un hallazgo incomodo: la "verdad" contra la que se mide todo
el sistema sale del analisis de Open-Meteo, que es un producto derivado de
modelos numericos, y los modelos que el sistema evalua son de esa misma
familia. Se les califica con un examen que ellos escribieron. Eso explicaria
parte de su ventaja aparente sobre el infrarrojo (Brier 0.084 contra 0.123) sin
que sean mejores de verdad.

Una persona diciendo "llovio" es la unica verdad independiente del sistema. Y
esta, ademas, **funciona en el apagon**: sin internet no hay ntfy ni pagina,
pero la radio sigue, y es justo cuando estas parado viendo llover.

## Por que se tardo en construir

Porque escribir a ciegas en la memoria de aprendizaje de otro sistema -14,530
pares dentro- es la familia de fallos mas cara de este proyecto: una columna
mal alineada no da ningun error, solo empeora el sistema sin motivo aparente.
El esquema esta ahora confirmado en los dos lados, y `store.append_observations`
sustituye por prioridad de fuente en vez de escribir una fila de mas.

## A que instante pertenece un "llovio": preguntado y contestado

Aclarado el 20 de septiembre de 2026, y no era lo que yo habia supuesto. Los
dos comandos -`llovio` y `no llovio`- hablan del **momento en que escribes**, no
del dia. Y se usan para una cosa muy concreta: **cuando el sistema dice una cosa
y el cielo dice otra**.

De ahi salen dos consecuencias, una buena y una incomoda.

**La buena: no hay limite de antiguedad.** Mi primera version descartaba las
respuestas de mas de 12 horas por miedo a no poder asignarles una hora. Con
esta aclaracion la hora no hay que reconstruirla: `ts` *es* la hora. Esa regla
tiraba diez de las once respuestas acumuladas -el 91% del dato mas escaso del
proyecto- por un problema que no existia. Solo se comprueba que `fecha` y `ts`
hablen del mismo dia, como cordura contra una fila escrita a mano.

**La incomoda: la muestra esta enriquecida con errores por construccion.** Nadie
escribe "la app acerto". Estas filas son, por definicion, los momentos en que se
equivoco. Eso no las hace menos verdaderas -corregir una etiqueta mal puesta
siempre mejora los datos- pero si invalida usarlas para *comparar fuentes entre
si*, que era justo el plan para resolver la circularidad de Open-Meteo. Ver la
nota de `evaluar.py` en la seccion 4-ter.

Para arbitrar de verdad hace falta una muestra que **el sistema** elija, no tu:
preguntar a ratos al azar, no solo cuando algo salio mal.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone

from . import config, store

log = logging.getLogger(__name__)

_CLAVE_CURSOR = "malla_ultima_respuesta_ts"

# No hay limite de antiguedad: `ts` es la hora del hecho, no la de la captura.
# Lo unico que se rechaza es lo imposible. Un mensaje con marca en el futuro es
# un reloj mal puesto en el nodo o una fila escrita a mano, y en los dos casos
# guardarlo envenena una franja que todavia no ha pasado.
#
# Media hora de margen porque el nodo no tiene RTC con bateria: se pone en hora
# por el GPS o por el Pi, y puede irse unos minutos.
MARGEN_FUTURO_H = 0.5


def _cursor() -> int:
    try:
        return int(store.load_json(config.STATE_JSON, {}).get(_CLAVE_CURSOR, 0))
    except (TypeError, ValueError):
        return 0


def _guardar_cursor(ts: int) -> None:
    estado = store.load_json(config.STATE_JSON, {})
    estado[_CLAVE_CURSOR] = int(ts)
    store.save_json(config.STATE_JSON, estado)


def leer(desde_ts: int = 0) -> list[tuple[int, str, int, str]]:
    """Filas de `respuestas` posteriores al cursor. Nunca lanza.

    La malla es la dueña de la base; el nowcast es invitado. `mode=ro` no es
    cosmetico: evita crearla si falta y evita bloquear al servicio de
    Meshtastic si esta escribiendo.
    """
    ruta = config.CLIMA_DB
    if not ruta or not os.path.exists(ruta):
        return []
    try:
        con = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True, timeout=2.0)
        try:
            return con.execute(
                "select ts, fecha, llovio, fuente from respuestas "
                "where ts > ? order by ts", (int(desde_ts),)).fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        # Incluye el caso de que la tabla no exista todavia, que es lo normal
        # hasta que alguien usa el comando por primera vez.
        log.info("no se pudieron leer las respuestas de la malla: %s", exc)
        return []


def _instante(ts: int, fecha: str) -> datetime | None:
    """El momento al que pertenece la respuesta, o None si no se puede saber."""
    try:
        t = datetime.fromtimestamp(int(ts), timezone.utc)
    except (TypeError, ValueError, OSError):
        return None
    edad_h = (datetime.now(timezone.utc) - t).total_seconds() / 3600
    if edad_h < -MARGEN_FUTURO_H:
        log.warning("respuesta de la malla con marca en el futuro (%.1f h): "
                    "revisar la hora del nodo", -edad_h)
        return None
    # `fecha` la escribe la malla en hora local; se compara en hora local.
    # No es para filtrar respuestas viejas -las viejas valen igual, `ts` es la
    # hora del hecho- sino cordura: si los dos campos no coinciden, uno de los
    # dos esta mal y no se sabe cual.
    if fecha:
        local = t.astimezone(config.TZ).strftime("%Y-%m-%d")
        if str(fecha).strip()[:10] != local:
            log.warning("respuesta de la malla incoherente: fecha %s pero "
                        "marca de tiempo del %s; se ignora", fecha, local)
            return None
    return t


def procesar(desde: int | None = None) -> int:
    """Incorpora las respuestas nuevas de la malla. Devuelve cuantas.

    El cursor se guarda **aunque una fila se descarte**: si no, cada corrida
    volveria a leer y a rechazar la misma fila incoherente para siempre.

    'desde' fuerza el punto de partida, para releer el historico despues de
    cambiar las reglas de interpretacion. Sin eso, un arreglo como el de hoy
    -quitar el limite de 12 horas- no recuperaria nada: las filas ya estaban
    por debajo del cursor.
    """
    filas = leer(_cursor() if desde is None else desde)
    if not filas:
        return 0

    nuevas, ultimo = [], 0
    for ts, fecha, llovio, fuente in filas:
        ultimo = max(ultimo, int(ts or 0))
        if llovio is None:
            continue
        t = _instante(ts, fecha or "")
        if t is None:
            continue
        nuevas.append({
            "valid_utc": store.round_slot(t),
            "rained": 1 if int(llovio) else 0,
            "mm": "", "peak_score": "",
            # Se distingue de "manual" (pagina y ntfy) para poder medir por
            # separado si el ojo humano por radio se comporta distinto: quien
            # escribe por radio suele estar afuera mirando, y quien toca un
            # boton en la pagina puede estar contestando de memoria.
            "source": "malla",
        })

    if nuevas:
        try:
            store.append_observations(nuevas)
        except Exception as exc:
            # Sin guardar cursor: se reintenta en la corrida siguiente.
            log.error("no se pudieron guardar las respuestas de la malla: %s",
                      exc)
            return 0
        log.info("malla: %s confirmacion(es) de lluvia incorporadas",
                 len(nuevas))
    if ultimo:
        _guardar_cursor(ultimo)
    return len(nuevas)


if __name__ == "__main__":
    # `python -m nowcast.malla --todo` relee la tabla entera desde el principio.
    #
    # Es idempotente: las filas que ya estan no se duplican -el instante es la
    # clave- y las que ya tenian una observacion humana no se pisan. Asi que
    # correrlo dos veces no hace nada la segunda, que es la propiedad que hace
    # que sea seguro ejecutarlo a mano sin pensarlo mucho.
    import argparse
    import sys

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--todo", action="store_true",
                   help="releer desde el principio, ignorando el cursor")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    n = procesar(desde=0 if args.todo else None)
    print(f"{n} confirmacion(es) incorporadas")
    sys.exit(0)
