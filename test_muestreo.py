"""Las preguntas al azar: que sean al azar de verdad, y que lleguen por dos vías.

La propiedad que no se puede romper es la A: el momento de preguntar no depende
del pronóstico. En cuanto dependa, la muestra deja de valer para comparar
fuentes, que es lo único para lo que existe. Y es fácil de romper sin darse
cuenta: basta con que a alguien le parezca buena idea "preguntar más cuando
parece que va a llover".
"""
from __future__ import annotations

import json
import os
import random
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

from nowcast import config, http, malla, muestreo, notify, salud, store

ok = True


def chk(nombre: str, condicion: bool, detalle: str = "") -> None:
    global ok
    print(f"  {'PASA' if condicion else 'FALLA'}  {nombre}"
          + (f"  [{detalle}]" if detalle else ""))
    if not condicion:
        ok = False


tmp = tempfile.mkdtemp()
config.STATE_JSON = os.path.join(tmp, "state.json")
config.MUESTREO_CSV = os.path.join(tmp, "muestreo.csv")
config.OBSERVATIONS_CSV = os.path.join(tmp, "obs.csv")
config.CLIMA_DB = os.path.join(tmp, "clima.db")
config.NTFY_TOPIC = "lluvia-de-prueba"
config.NTFY_TOPIC_RESPUESTAS = "respuestas-de-prueba"
config.MUESTREO_ACTIVO = True

enviados: list = []
notify.send = lambda titulo, cuerpo, **kw: (
    enviados.append({"titulo": titulo, "cuerpo": cuerpo, **kw}), True)[1]

# 13:00 hora local, dentro del horario
MEDIODIA = datetime(2026, 9, 28, 13, 0, tzinfo=config.TZ).astimezone(timezone.utc)


def limpiar():
    for f in (config.STATE_JSON, config.MUESTREO_CSV, config.OBSERVATIONS_CSV):
        if os.path.exists(f):
            os.remove(f)
    enviados.clear()


print("A. El pronóstico NO decide cuándo se pregunta")
# Misma semilla, pronósticos opuestos: tienen que salir exactamente las mismas
# decisiones. Si algún día alguien hace que p60 influya, esto falla.
def decisiones(p60):
    limpiar()
    azar = random.Random(42)
    salen = []
    t = MEDIODIA
    for _ in range(400):
        d = muestreo.decidir(t, p60=p60, azar=azar)
        salen.append(d is not None and d.get("preguntado_utc") == t.isoformat())
        # cerrar para que la siguiente pueda abrirse
        e = store.load_json(config.STATE_JSON, {}).get("muestreo") or {}
        if e:
            e["respondida"] = True
            todo = store.load_json(config.STATE_JSON, {})
            todo["muestreo"] = e
            store.save_json(config.STATE_JSON, todo)
    return salen

seco, lluvioso = decisiones(0.02), decisiones(0.95)
chk("con lluvia segura o cielo despejado se pregunta en los mismos momentos",
    seco == lluvioso, f"{sum(seco)} y {sum(lluvioso)} preguntas")
pi = muestreo.probabilidad_por_corrida()
esperadas = 400 * pi
chk("y con la frecuencia prevista", abs(sum(seco) - esperadas) < 4 * esperadas ** 0.5,
    f"{sum(seco)} de ~{esperadas:.0f}")
filas = muestreo.leer()
chk("la probabilidad queda guardada con cada pregunta",
    all(abs(float(f["pi"]) - pi) < 1e-5 for f in filas), f"π={pi:.4f}")

print("\nB. De noche no se pregunta")
limpiar()
noche = datetime(2026, 9, 28, 3, 0, tzinfo=config.TZ).astimezone(timezone.utc)
siempre = random.Random(0)
siempre.random = lambda: 0.0          # el dado siempre diría que sí
chk("a las 3 de la mañana, nada", muestreo.decidir(noche, azar=siempre) is None)
chk("ni se manda notificación", not enviados)

print("\nC. Una pregunta abierta sale por ntfy con sus botones")
limpiar()
q = muestreo.decidir(MEDIODIA, p60=0.4, azar=siempre)
chk("se abre", q is not None and q["id"] == 1, str(q))
chk("se manda una notificación", len(enviados) == 1, str(len(enviados)))
botones = enviados[0].get("actions", "") if enviados else ""
chk("con botón de sí", "body=muestra:1:si" in botones)
chk("y de no", "body=muestra:1:no" in botones)
chk("que publican en el canal de respuestas",
    "respuestas-de-prueba" in botones)
chk("el texto pide no adivinar", "ignórala" in enviados[0]["cuerpo"])
chk("queda registrada como abierta",
    muestreo.leer()[0]["estado"] == "abierta")
chk("y guarda el pronóstico del momento, solo para diagnosticar",
    muestreo.leer()[0]["p60"] == "0.4")

print("\nD. Mientras está abierta no se abre otra, y luego vence")
chk("la siguiente corrida devuelve la misma",
    muestreo.decidir(MEDIODIA + timedelta(minutes=15), azar=siempre)["id"] == 1)
chk("sin mandar otra notificación", len(enviados) == 1)
chk("a los 40 minutos ya no está vigente",
    muestreo.vigente(MEDIODIA + timedelta(minutes=40)) is None)

print("\nE. Se contesta por ntfy: entra como verdad 'muestra'")
resp_t = MEDIODIA + timedelta(minutes=6)
http.get_text = lambda url, **k: json.dumps({
    "id": "m1", "event": "message", "topic": "t",
    "time": int(resp_t.timestamp()), "message": "muestra:1:si"})
chk("salud.procesar la recoge", salud.procesar() == 1)
f = muestreo.leer()[0]
chk("respondida por ntfy", f["estado"] == "respondida" and f["canal"] == "ntfy",
    f"{f['estado']} / {f['canal']}")
obs = store.read_observations()
chk("y va a las observaciones como 'muestra'",
    len(obs) == 1 and obs[0]["source"] == "muestra" and obs[0]["rained"] == "1",
    str(obs))
chk("la pregunta deja de estar vigente", muestreo.vigente(resp_t) is None)

print("\nF. La primera respuesta gana, venga por donde venga")
chk("una segunda por la malla se rechaza",
    muestreo.registrar(1, False, "malla", resp_t + timedelta(minutes=2))
    == "repetida")
chk("sin tocar la primera", muestreo.leer()[0]["respuesta"] == "si")
chk("ni duplicar la observación", len(store.read_observations()) == 1)

print("\nG. Tarde no cuenta")
limpiar()
muestreo.decidir(MEDIODIA, azar=siempre)
chk("contestar a la hora se marca 'tarde'",
    muestreo.registrar(1, True, "ntfy", MEDIODIA + timedelta(hours=1))
    == "tarde")
chk("y no entra en las observaciones", not store.read_observations())

print("\nH. Las que nadie contesta se guardan como tales")
limpiar()
muestreo.decidir(MEDIODIA, azar=siempre)
chk("cerrar_vencidas las marca", muestreo.cerrar_vencidas(
    MEDIODIA + timedelta(hours=1)) == 1)
chk("como 'sin_respuesta'", muestreo.leer()[0]["estado"] == "sin_respuesta")

print("\nI. Por la malla: la tabla 'respuestas' con fuente 'muestra:<id>'")
limpiar()
ahora = datetime.now(timezone.utc).replace(microsecond=0)
muestreo.decidir(ahora, azar=siempre) if 8 <= ahora.astimezone(config.TZ).hour < 22 \
    else None
# Si la prueba corre de noche, se abre a mano una pregunta en la hora actual.
if not muestreo.leer():
    e = {"id": 1, "ultimo_id": 1, "preguntado_utc": ahora.isoformat(),
         "vence_utc": (ahora + timedelta(minutes=30)).isoformat(),
         "respondida": False}
    todo = store.load_json(config.STATE_JSON, {}); todo["muestreo"] = e
    store.save_json(config.STATE_JSON, todo)
    muestreo._añadir({"id": 1, "preguntado_utc": e["preguntado_utc"],
                      "vence_utc": e["vence_utc"], "pi": 0.03, "p60": "",
                      "respuesta": "", "canal": "", "respondido_utc": "",
                      "estado": "abierta"})
con = sqlite3.connect(config.CLIMA_DB)
con.execute("create table if not exists respuestas (ts INTEGER PRIMARY KEY, "
            "fecha TEXT, llovio INTEGER, fuente TEXT)")
t_radio = ahora + timedelta(minutes=3)
con.execute("insert into respuestas values (?,?,?,?)",
            (int(t_radio.timestamp()),
             t_radio.astimezone(config.TZ).strftime("%Y-%m-%d"), 0, "muestra:1"))
con.commit()
con.close()
malla.procesar(desde=0)
f = muestreo.leer()[0]
chk("la respuesta por radio se registra", f["estado"] == "respondida"
    and f["canal"] == "malla" and f["respuesta"] == "no",
    f"{f['estado']} / {f['canal']} / {f['respuesta']}")
obs = store.read_observations()
chk("una sola observación, como 'muestra' y no como 'malla'",
    len(obs) == 1 and obs[0]["source"] == "muestra", str(obs))

print("\nJ. Una respuesta al azar sustituye a Open-Meteo en su franja")
limpiar()
muestreo.decidir(MEDIODIA, azar=siempre)
slot = store.round_slot(MEDIODIA + timedelta(minutes=5))
store.append_observations([{"valid_utc": slot, "rained": 0, "mm": "",
                            "peak_score": "", "source": "openmeteo"}])
muestreo.registrar(1, True, "ntfy", MEDIODIA + timedelta(minutes=5))
o = [x for x in store.read_observations() if x["valid_utc"] == slot]
chk("gana la persona", len(o) == 1 and o[0]["source"] == "muestra"
    and o[0]["rained"] == "1", str(o))

print("\nJ-bis. Una pregunta de prueba recorre el circuito y no cuenta")
limpiar()
config.LATEST_JSON = os.path.join(tmp, "latest.json")
store.save_json(config.LATEST_JSON, {"issued_local": "x", "muestreo": None})
q = muestreo.forzar(MEDIODIA)
chk("se abre y se envía", q is not None and len(enviados) == 1)
chk("se publica en latest.json para la malla",
    (store.load_json(config.LATEST_JSON, {}) or {}).get("muestreo", {}).get("id")
    == q["id"])
chk("queda marcada como prueba", muestreo.leer()[0]["pi"] == ""
    and muestreo.leer()[0]["estado"] == "prueba")
chk("la respuesta se anota como de prueba",
    muestreo.registrar(q["id"], True, "malla", MEDIODIA + timedelta(minutes=2))
    == "prueba")
chk("y no entra en las observaciones", not store.read_observations())

print("\nK. Apagado, no pregunta nada")
limpiar()
config.MUESTREO_ACTIVO = False
chk("ni con el dado a favor", muestreo.decidir(MEDIODIA, azar=siempre) is None)
config.MUESTREO_ACTIVO = True

print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
