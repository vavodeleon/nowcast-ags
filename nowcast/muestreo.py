"""Preguntar a ratos al azar: la única verdad sin sesgo que puede tener esto.

## Por qué hace falta

Todas las observaciones independientes que tenía el sistema tienen un dueño que
decide cuándo existen. Las confirmaciones por la página, por la notificación y
por radio las da una persona cuando ella quiere, y Álvaro lo dijo claro el
20/09/2026: el comando se usa «cuando la app dice que no llueve pero sí está
lloviendo». Nadie escribe nunca «la app acertó». Esa muestra no está sesgada
hacia el error: es la muestra de los errores. Sirve para corregir etiquetas,
pero no para comparar fuentes, porque castiga a la que dominaba el pronóstico
que falló.

Y la verdad de Open-Meteo es un análisis hecho con los mismos modelos que se
evalúan. Se califican con un examen que ellos escribieron.

La salida es que el MOMENTO de preguntar lo elija el sistema, al azar, sin
mirar el pronóstico. Treinta respuestas a preguntas no provocadas valen más
que trescientas correcciones espontáneas.

## Las tres reglas, y qué rompería cada una

1. **Probabilidad fija por corrida.** Nunca depende del pronóstico, de las
   alertas ni de nada que tenga que ver con el tiempo. Si se preguntara más
   cuando «parece que va a llover», volvería exactamente el sesgo que esto
   existe para quitar. La probabilidad se guarda con cada pregunta: si algún
   día se quiere preguntar más en temporada, se puede, ponderando por 1/π.

2. **Se guardan también las que nadie contesta.** Si se contesta más cuando
   llueve -uno se asoma más, o solo ve el teléfono cuando está bajo techo- la
   muestra respondida se sesga igual. Con las no contestadas registradas se
   puede medir: el análisis de Open-Meteo existe para todos los ratos, así que
   se compara la lluvia en los contestados contra la de los no contestados.

3. **La primera respuesta gana, y las tardías no cuentan.** La pregunta es
   «¿está lloviendo AHORA?». Una respuesta dos horas después describe otro
   momento. Pasada la vigencia se anota como tardía y no entra en nada.

## Los dos canales

Álvaro, 28/09/2026: «no siempre tengo internet y no siempre tengo cobertura en
el mesh». La misma pregunta sale por los dos, con el mismo número, y vale la
respuesta que llegue primero por cualquiera.

- **ntfy**: una notificación con dos botones, que publican en el canal de
  respuestas `muestra:<id>:si` o `muestra:<id>:no`. La recoge `salud.procesar`,
  que es el único lector de ese canal: dos lectores con un solo cursor se
  pisarían.
- **Malla LoRa**: la pregunta vigente se publica en `latest.json` bajo
  `muestreo`. La malla la emite por radio y guarda la respuesta en su tabla
  `respuestas` con `fuente = "muestra:<id>"`. La recoge `malla.procesar`. El
  nowcast decide y la malla transporta, igual que con todo lo demás.
"""
from __future__ import annotations

import csv
import logging
import os
import random
from datetime import datetime, timedelta, timezone

from . import config, store

log = logging.getLogger(__name__)

CAMPOS = ["id", "preguntado_utc", "vence_utc", "pi", "p60",
          "respuesta", "canal", "respondido_utc", "estado"]

_CLAVE = "muestreo"


# ---------------------------------------------------------------- estado

def _estado() -> dict:
    return store.load_json(config.STATE_JSON, {}).get(_CLAVE) or {}


def _guardar_estado(e: dict) -> None:
    todo = store.load_json(config.STATE_JSON, {})
    todo[_CLAVE] = e
    store.save_json(config.STATE_JSON, todo)


def _utc(txt: str | None) -> datetime | None:
    if not txt:
        return None
    try:
        t = datetime.fromisoformat(str(txt).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- registro

def leer() -> list[dict]:
    if not os.path.exists(config.MUESTREO_CSV):
        return []
    with open(config.MUESTREO_CSV, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _añadir(fila: dict) -> None:
    store._ensure(config.MUESTREO_CSV, CAMPOS)
    with open(config.MUESTREO_CSV, "a", newline="", encoding="utf-8") as fh:
        csv.DictWriter(fh, fieldnames=CAMPOS, extrasaction="ignore").writerow(fila)


def _reescribir(filas: list[dict]) -> None:
    """Escribe al lado y renombra: si el proceso muere a mitad, no se pierde nada."""
    tmp = config.MUESTREO_CSV + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CAMPOS, extrasaction="ignore")
        w.writeheader()
        for f in filas:
            w.writerow(f)
    os.replace(tmp, config.MUESTREO_CSV)


# ---------------------------------------------------------------- preguntar

def probabilidad_por_corrida() -> float:
    """π: la probabilidad de preguntar en una corrida dentro del horario.

    Sale de cuántas preguntas al día se quieren y de cuántas corridas caben en
    el horario. Es la misma para todas las corridas: esa igualdad es lo que
    hace que la muestra no necesite ponderarse.
    """
    horas = max(1, config.MUESTREO_HASTA_H - config.MUESTREO_DESDE_H)
    corridas = horas * 60 / 15
    return min(1.0, config.MUESTREO_POR_DIA / corridas)


def vigente(ahora: datetime | None = None) -> dict | None:
    """La pregunta abierta ahora mismo, o None. Es lo que va a latest.json."""
    ahora = ahora or datetime.now(timezone.utc)
    e = _estado()
    vence = _utc(e.get("vence_utc"))
    if not e.get("id") or e.get("respondida") or vence is None or ahora > vence:
        return None
    return {
        "id": int(e["id"]),
        "pregunta": "¿Está lloviendo ahora mismo donde estás?",
        "preguntado_utc": e["preguntado_utc"],
        "vence_utc": e["vence_utc"],
        "vence_local": vence.astimezone(config.TZ).strftime("%H:%M"),
    }


def decidir(ahora: datetime | None = None, p60: float | None = None,
            azar: random.Random | None = None) -> dict | None:
    """Tira el dado. Si toca, abre una pregunta, la envía por ntfy y la devuelve.

    `p60` solo se GUARDA, para poder revisar después si la gente contesta más
    cuando se esperaba lluvia. No interviene en la decisión, y así tiene que
    seguir: en el momento en que el pronóstico influya en cuándo preguntar, la
    muestra deja de ser al azar.
    """
    if not config.MUESTREO_ACTIVO:
        return None
    ahora = ahora or datetime.now(timezone.utc)
    azar = azar or random.Random()

    hora = ahora.astimezone(config.TZ).hour
    if not (config.MUESTREO_DESDE_H <= hora < config.MUESTREO_HASTA_H):
        return vigente(ahora)
    if vigente(ahora) is not None:
        return vigente(ahora)

    pi = probabilidad_por_corrida()
    if azar.random() >= pi:
        return None

    e = _estado()
    nuevo = int(e.get("ultimo_id", 0)) + 1
    vence = ahora + timedelta(minutes=config.MUESTREO_VIGENCIA_MIN)
    e = {"id": nuevo, "ultimo_id": nuevo,
         "preguntado_utc": ahora.isoformat(), "vence_utc": vence.isoformat(),
         "respondida": False}
    _guardar_estado(e)
    _añadir({"id": nuevo, "preguntado_utc": ahora.isoformat(),
             "vence_utc": vence.isoformat(), "pi": round(pi, 6),
             "p60": "" if p60 is None else round(float(p60), 4),
             "respuesta": "", "canal": "", "respondido_utc": "",
             "estado": "abierta"})
    log.info("muestreo: pregunta #%s abierta hasta las %s", nuevo,
             vence.astimezone(config.TZ).strftime("%H:%M"))

    try:
        from . import notify
        notify.enviar_muestra(nuevo, vence)
    except Exception as exc:
        # La malla todavía puede llevarla: no se pierde la pregunta por esto.
        log.error("muestreo: no se pudo enviar por ntfy: %s", exc)
    return vigente(ahora)


# ---------------------------------------------------------------- responder

def registrar(ident: int, llovio: bool, canal: str,
              cuando: datetime | None = None) -> str:
    """Anota una respuesta. Devuelve qué pasó con ella, en palabras.

    'aceptada', 'repetida' (ya había una: la primera gana), 'tarde' (pasó la
    vigencia) o 'desconocida' (no existe esa pregunta).
    """
    cuando = cuando or datetime.now(timezone.utc)
    filas = leer()
    for f in filas:
        if str(f.get("id")) != str(ident):
            continue
        if f.get("respuesta"):
            log.info("muestreo: #%s ya tenía respuesta; gana la primera", ident)
            return "repetida"
        vence = _utc(f.get("vence_utc"))
        f["canal"] = canal
        f["respondido_utc"] = cuando.isoformat()
        f["respuesta"] = "si" if llovio else "no"
        if vence is not None and cuando > vence:
            f["estado"] = "tarde"
            _reescribir(filas)
            log.info("muestreo: #%s contestada tarde por %s; no cuenta",
                     ident, canal)
            return "tarde"
        prueba = not f.get("pi")
        f["estado"] = "prueba_respondida" if prueba else "respondida"
        _reescribir(filas)

        e = _estado()
        if str(e.get("id")) == str(ident):
            e["respondida"] = True
            _guardar_estado(e)

        if prueba:
            # Una pregunta forzada a mano no es al azar, y quien prueba el
            # circuito contesta lo que sea. Se anota para ver que el camino
            # funciona y no entra en nada mas.
            log.info("muestreo: #%s era de prueba; respuesta por %s anotada "
                     "y descartada", ident, canal)
            return "prueba"

        # Y como verdad del instante en que se miró el cielo. La respuesta
        # describe el momento en que se contestó, no el de la pregunta: con
        # la vigencia corta, los dos caen casi siempre en la misma franja.
        store.append_observations([{
            "valid_utc": store.round_slot(cuando),
            "rained": 1 if llovio else 0,
            "mm": "", "peak_score": "", "source": "muestra",
        }])
        log.info("muestreo: #%s contestada por %s: %s", ident, canal,
                 "llueve" if llovio else "no llueve")
        return "aceptada"
    log.info("muestreo: respuesta a #%s, que no existe", ident)
    return "desconocida"


def cerrar_vencidas(ahora: datetime | None = None) -> int:
    """Marca como 'sin respuesta' las que vencieron sin que nadie contestara.

    No es limpieza: es el dato que permite medir si contestar depende del
    tiempo. Una pregunta sin respuesta cuenta tanto como una respondida.
    """
    ahora = ahora or datetime.now(timezone.utc)
    filas = leer()
    cambio = 0
    for f in filas:
        vence = _utc(f.get("vence_utc"))
        if f.get("estado") == "abierta" and vence is not None and ahora > vence:
            f["estado"] = "sin_respuesta"
            cambio += 1
    if cambio:
        _reescribir(filas)
    return cambio


def forzar(ahora: datetime | None = None) -> dict:
    """Abre una pregunta YA, para probar los dos canales de punta a punta.

    Se marca como prueba -`pi` vacio- porque no es al azar: la eligio una
    persona. Sus respuestas se anotan para confirmar que el circuito funciona,
    pero no entran en las observaciones ni en la evaluacion. Si entraran, una
    tarde de pruebas contaminaria justo la muestra que tiene que ser limpia.

    Escribe ademas la pregunta en latest.json, para que la malla la vea sin
    esperar a la siguiente corrida.
    """
    ahora = ahora or datetime.now(timezone.utc)
    e = _estado()
    nuevo = int(e.get("ultimo_id", 0)) + 1
    vence = ahora + timedelta(minutes=config.MUESTREO_VIGENCIA_MIN)
    _guardar_estado({"id": nuevo, "ultimo_id": nuevo,
                     "preguntado_utc": ahora.isoformat(),
                     "vence_utc": vence.isoformat(), "respondida": False})
    _añadir({"id": nuevo, "preguntado_utc": ahora.isoformat(),
             "vence_utc": vence.isoformat(), "pi": "", "p60": "",
             "respuesta": "", "canal": "", "respondido_utc": "",
             "estado": "prueba"})
    try:
        from . import notify
        notify.enviar_muestra(nuevo, vence)
    except Exception as exc:
        log.error("muestreo: no se pudo enviar por ntfy: %s", exc)
    q = vigente(ahora)
    d = store.load_json(config.LATEST_JSON, None)
    if isinstance(d, dict):
        d["muestreo"] = q
        store.save_json(config.LATEST_JSON, d)
    return q


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Preguntas al azar")
    ap.add_argument("--probar", action="store_true",
                    help="abrir una pregunta de PRUEBA ahora (no cuenta)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    if args.probar:
        # Fuera de systemd nadie carga ~/.nowcast.env, y sin el no hay canales
        # de ntfy. El 28/09/2026 la primera prueba abrio una pregunta que solo
        # podia contestarse por radio -y la malla aun no la emite-, o sea una
        # pregunta que no le llegaba a nadie. Mejor negarse con el comando
        # exacto que abrir una a medias.
        if not config.NTFY_TOPIC or not config.NTFY_TOPIC_RESPUESTAS:
            print("No están cargados los canales de ntfy: la pregunta no le")
            print("llegaría a nadie. Fuera de systemd hay que cargar el entorno:")
            print()
            print("  set -a; . ~/.nowcast.env; set +a")
            print("  ./.venv/bin/python -m nowcast.muestreo --probar")
            sys.exit(1)
        q = forzar()
        print(f"Pregunta de prueba #{q['id']} abierta hasta las "
              f"{q['vence_local']}. Contesta por ntfy o por radio "
              f"(si {q['id']} / no {q['id']}).")
    else:
        for f in leer()[-10:]:
            print(f"  #{f['id']:>4} {f['preguntado_utc'][:16]}  "
                  f"{f['estado']:<18} {f['respuesta'] or '—':<3} "
                  f"{f['canal'] or ''}")
    sys.exit(0)
