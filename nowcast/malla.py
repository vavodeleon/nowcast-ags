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

## La decision no obvia: a que instante pertenece un "llovio"

`respuestas` guarda `ts` (cuando contestaste) y `fecha` (el dia del que
hablabas). El aprendizaje del nowcast es por bloques de 15 minutos, no por dia:
"el jueves llovio" no dice nada sobre las 96 franjas del jueves, y en
Aguascalientes -500 mm en tres meses- una tarde de tormenta y una mañana seca
son el mismo dia calendario.

Asi que se usa `ts`, el momento en que escribiste, y **solo si `fecha` es el dia
de ese mismo momento**. Si contestaste hoy sobre ayer, la fila se ignora: es un
dato verdadero al que no se le puede asignar una hora, y meterlo en la franja
equivocada enseñaria una mentira con cara de verdad.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone

from . import config, store

log = logging.getLogger(__name__)

_CLAVE_CURSOR = "malla_ultima_respuesta_ts"

# Mas viejo que esto y ya no se incorpora. No es desconfianza del dato: es que
# el momento al que pertenece deja de ser reconstruible, y una franja de 15
# minutos elegida a ojo vale menos que nada.
EDAD_MAXIMA_H = 12


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
    if edad_h > EDAD_MAXIMA_H or edad_h < -0.5:
        log.info("respuesta de la malla descartada por edad (%.1f h)", edad_h)
        return None
    # `fecha` la escribe la malla en hora local; se compara en hora local.
    if fecha:
        local = t.astimezone(config.TZ).strftime("%Y-%m-%d")
        if str(fecha).strip()[:10] != local:
            log.info("respuesta de la malla sobre %s contestada el %s: "
                     "verdadera pero sin hora asignable, se ignora",
                     fecha, local)
            return None
    return t


def procesar() -> int:
    """Incorpora las respuestas nuevas de la malla. Devuelve cuantas.

    El cursor se guarda **aunque una fila se descarte**: si no, cada corrida
    volveria a leer y a rechazar la misma respuesta de anteayer para siempre.
    """
    filas = leer(_cursor())
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
