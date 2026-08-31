"""Cierra el bucle de aprendizaje del canal de salud.

La lluvia se verifica sola: horas despues se le puede preguntar a Open-Meteo
si llovio. Una migrana no tiene sensor. La unica forma de saber si un aviso
acerto es que la persona lo diga, y para que eso pase de verdad tiene que
costar un toque desde la propia notificacion.

Los avisos de presion llevan dos botones que publican en un canal de ntfy
aparte. Aqui se consulta ese canal en cada corrida y se rellenan las
respuestas en `data/salud.csv`.

## Por que esto importa mas que afinar el umbral a ojo

Los umbrales actuales -1.5 hPa en una hora, 2.5 en tres, 5 en veinticuatro-
salen de la literatura sobre migrana y presion, no de ella. La sensibilidad
individual varia mucho; hay quien reacciona a caidas que a otra persona no le
hacen nada, y hay quien reacciona a las subidas. Sin respuestas, cualquier
ajuste que yo haga es una opinion disfrazada de numero.

Con treinta o cuarenta respuestas se puede empezar a decir algo: que ventana
separa mejor los dias con dolor de los que no, y con que umbral. Es el mismo
bucle que ya tiene la lluvia, aplicado a la pregunta que de verdad importaba.

## Lo que este modulo NO hace

No diagnostica ni predice migranas. Correlaciona avisos con respuestas para
poder ajustar un umbral. La presion es uno de muchos disparadores posibles y
este sistema solo ve ese; que un dia no avise no significa nada sobre lo que
la persona vaya a sentir.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from . import config, http, store

log = logging.getLogger(__name__)

# "dolor:si @2026-08-30T19:31:00+00:00", tolerante con acentos y espacios.
_RESPUESTA = re.compile(r"dolor\s*:\s*(si|sí|no)\b\s*@?\s*([0-9T:.\-+]*)",
                        re.IGNORECASE)

_CLAVE_ULTIMO = "salud_ultimo_id"


def _ultimo_id() -> str:
    return store.load_json(config.STATE_JSON, {}).get(_CLAVE_ULTIMO, "")


def _guardar_ultimo(mid: str) -> None:
    estado = store.load_json(config.STATE_JSON, {})
    estado[_CLAVE_ULTIMO] = mid
    store.save_json(config.STATE_JSON, estado)


def _mensajes() -> list[dict]:
    """Lee el canal de respuestas desde el ultimo mensaje ya procesado.

    Se pide con `since=<id>` y no sin parametro: un poll pelado devuelve la
    cache entera del canal cada vez, que la documentacion de ntfy desaconseja
    expresamente y que ademas reprocesaria respuestas viejas.
    """
    if not config.NTFY_TOPIC_RESPUESTAS:
        return []
    desde = _ultimo_id() or "12h"
    url = (f"{config.NTFY_SERVER.rstrip('/')}/"
           f"{config.NTFY_TOPIC_RESPUESTAS}/json?poll=1&since={desde}")
    crudo = http.get_text(url)
    if not crudo:
        return []
    salida = []
    for linea in crudo.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        try:
            obj = json.loads(linea)
        except ValueError:
            continue
        # El canal manda tambien 'open' y 'keepalive'; solo interesan los
        # mensajes de verdad.
        if obj.get("event") == "message":
            salida.append(obj)
    return salida


def procesar() -> int:
    """Incorpora las respuestas nuevas. Devuelve cuantas."""
    if not config.NTFY_TOPIC_RESPUESTAS:
        return 0

    mensajes = _mensajes()
    if not mensajes:
        log.info("salud: sin respuestas nuevas")
        return 0

    incorporadas = 0
    ultimo = ""
    visto = _ultimo_id()
    for m in mensajes:
        mid = m.get("id") or ""
        # No dependemos de si `since=<id>` de ntfy incluye o excluye ese
        # mensaje: la documentacion no lo dice y de eso depende que una
        # respuesta se cuente una vez o para siempre. Se descarta aqui.
        if mid and mid == visto:
            continue
        ultimo = mid or ultimo
        cuerpo = f"{m.get('title', '')} {m.get('message', '')}"
        enc = _RESPUESTA.search(cuerpo)
        if not enc:
            log.info("salud: respuesta con formato inesperado, se ignora")
            continue
        dolor = "no" if enc.group(1).lower() == "no" else "si"
        ts_aviso = enc.group(2).strip()
        ts_resp = datetime.fromtimestamp(
            m.get("time", 0) or 0, timezone.utc).isoformat()
        if store.responder_salud(ts_aviso, dolor, ts_resp):
            incorporadas += 1
        else:
            log.info("salud: llego una respuesta sin aviso pendiente al que "
                     "asignarla")

    if ultimo:
        _guardar_ultimo(ultimo)
    if incorporadas:
        log.info("salud: %s respuesta(s) incorporadas", incorporadas)
    return incorporadas


def _misma_hora(a: str | None, b: str | None, minutos: int = 90) -> bool:
    """¿Dos avisos son del mismo episodio de presion?

    Noventa minutos: una bajada de presion sinoptica dura horas, asi que
    dos avisos separados por menos de eso casi siempre describen el mismo
    frente. Mas fino no aporta y mas grueso fusionaria episodios distintos.
    """
    if not a or not b:
        return False
    try:
        ta = datetime.fromisoformat(a)
        tb = datetime.fromisoformat(b)
    except ValueError:
        return False
    return abs((tb - ta).total_seconds()) <= minutos * 60


def resumen() -> dict:
    """Cuantos avisos, cuantos acertaron, y con que numeros.

    Sirve para dos cosas: mostrarlo en el tablero, y decidir cuando hay
    suficientes casos para tocar un umbral con fundamento.
    """
    filas = [f for f in store.read_salud()
             if f.get("dolor") and f.get("tipo") != "prueba"]
    if not filas:
        return {"respondidos": 0, "aciertos": 0, "tasa": None}

    # Un episodio, un caso. La primera noche de uso real salieron tres
    # avisos en el mismo segundo por una sola bajada de presion; contarlos
    # por separado inflaria el numero de casos por tres y cualquier umbral
    # que saliera de ahi estaria mal. Se agrupa por cercania en el tiempo.
    filas.sort(key=lambda f: f.get("ts_aviso", ""))
    episodios: list[dict] = []
    for f in filas:
        if episodios and _misma_hora(episodios[-1].get("ts_aviso"),
                                     f.get("ts_aviso")):
            # Se conserva el que si tuvo dolor: si ella contesto que si a
            # cualquiera de los avisos del episodio, el episodio cuenta.
            if f.get("dolor") == "si":
                episodios[-1] = f
            continue
        episodios.append(f)
    filas = episodios

    def num(f, c):
        try:
            return float(f.get(c) or "")
        except ValueError:
            return None

    con_dolor = [f for f in filas if f["dolor"] == "si"]
    caidas_con = [v for f in con_dolor if (v := num(f, "change_3h")) is not None]
    caidas_sin = [v for f in filas if f["dolor"] == "no"
                  and (v := num(f, "change_3h")) is not None]

    def media(xs):
        return round(sum(xs) / len(xs), 2) if xs else None

    return {
        "respondidos": len(filas),
        "aciertos": len(con_dolor),
        "tasa": round(len(con_dolor) / len(filas), 2),
        "caida_3h_con_dolor": media(caidas_con),
        "caida_3h_sin_dolor": media(caidas_sin),
        # Con menos de 30 respuestas cualquier diferencia entre esas dos
        # medias es ruido. Se dice explicitamente para que nadie -yo el
        # primero- mueva un umbral con cuatro casos.
        "suficientes": len(filas) >= 30,
        # Cuantas filas se fusionaron por ser del mismo episodio. Si este
        # numero es alto, algo esta mandando avisos de mas.
        "avisos_agrupados": len([f for f in store.read_salud()
                                 if f.get("dolor")
                                 and f.get("tipo") != "prueba"]) - len(filas),
    }
