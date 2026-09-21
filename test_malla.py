"""El puente de las correcciones por radio, y el fallo que lo hacia inutil.

La malla lleva semanas guardando `llovio` / `no llovio` en su tabla
`respuestas`. Esta prueba comprueba que ahora llegan al aprendizaje del
nowcast, y sobre todo que **sustituyen** al veredicto de Open-Meteo en vez de
perderse.

El fallo que se descubrio escribiendo esto es el de siempre en este proyecto:
`append_observations` descartaba, sin un solo mensaje de error, cualquier fila
cuyo instante ya tuviera dato. El sintoma habria sido que las confirmaciones
"no sirven de nada" -35 manuales registradas y ningun veredicto cambiado-, y
habria costado semanas encontrarlo porque no hay nada que mirar.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

from nowcast import config, store

ok = True


def chk(nombre: str, condicion: bool, detalle: str = "") -> None:
    global ok
    print(f"  {'PASA' if condicion else 'FALLA'}  {nombre}"
          + (f"  [{detalle}]" if detalle else ""))
    if not condicion:
        ok = False


tmp = tempfile.mkdtemp()
config.OBSERVATIONS_CSV = os.path.join(tmp, "obs.csv")
config.STATE_JSON = os.path.join(tmp, "state.json")
config.CLIMA_DB = os.path.join(tmp, "clima.db")

# Se construye una base con el esquema REAL de la malla, copiado del doc de
# arquitectura. Si el esquema cambiara, esta prueba es donde se nota.
con = sqlite3.connect(config.CLIMA_DB)
con.execute("create table respuestas (ts INTEGER PRIMARY KEY, fecha TEXT, "
            "llovio INTEGER, fuente TEXT)")
con.execute("create table observaciones (ts INTEGER PRIMARY KEY, nodo TEXT, "
            "temp_c REAL, humedad REAL, presion REAL)")
con.commit()

from nowcast import malla  # noqa: E402  (despues de apuntar config.CLIMA_DB)

ahora = datetime.now(timezone.utc)


def mete(t: datetime, llovio: int, fecha: str | None = None,
         fuente: str = "!Bcafe") -> None:
    if fecha is None:
        fecha = t.astimezone(config.TZ).strftime("%Y-%m-%d")
    con.execute("insert or replace into respuestas values (?,?,?,?)",
                (int(t.timestamp()), fecha, llovio, fuente))
    con.commit()


print("A. Una confirmación por radio entra en el aprendizaje")
hace_20 = ahora - timedelta(minutes=20)
mete(hace_20, 1)
chk("se incorpora una", malla.procesar() == 1)
filas = store.read_observations()
chk("queda una observación", len(filas) == 1, f"{len(filas)}")
chk("marcada como llovió", filas and filas[0]["rained"] == "1")
chk("con origen 'malla'", filas and filas[0]["source"] == "malla",
    filas[0]["source"] if filas else "")
chk("en la franja de 15 min del mensaje",
    filas and filas[0]["valid_utc"] == store.round_slot(hace_20),
    filas[0]["valid_utc"] if filas else "")

print("\nB. No se reprocesa lo mismo dos veces")
chk("la segunda pasada no incorpora nada", malla.procesar() == 0)
chk("y sigue habiendo una sola fila", len(store.read_observations()) == 1)

print("\nC. Una respuesta VIEJA entra igual: `ts` es la hora del hecho")
# La primera version las descartaba pasadas 12 horas, y eso tiraba diez de las
# once respuestas acumuladas. El limite protegia de un problema inexistente:
# los comandos hablan del momento en que escribes, asi que la hora no hay que
# reconstruirla.
hace_5_dias = ahora - timedelta(days=5)
mete(hace_5_dias, 1)
chk("se incorpora una de hace cinco días", malla.procesar(desde=0) >= 1)
vieja = [f for f in store.read_observations()
         if f["valid_utc"] == store.round_slot(hace_5_dias)]
chk("en su propia franja, no en la de hoy", len(vieja) == 1, f"{len(vieja)}")
chk("marcada como llovió", vieja and vieja[0]["rained"] == "1")

print("\nD. Lo que sí se rechaza: lo imposible")
# `fecha` y `ts` que no coinciden: uno de los dos está mal y no se sabe cuál.
chk("fecha incoherente con la marca de tiempo",
    malla._instante(int((ahora - timedelta(hours=3)).timestamp()),
                    "1999-01-01") is None)
# Marca en el futuro: reloj del nodo mal puesto. Guardarlo envenenaría una
# franja que todavía no ha pasado.
chk("marca en el futuro",
    malla._instante(int((ahora + timedelta(hours=6)).timestamp()), "") is None)
chk("pero unos minutos de deriva se toleran",
    malla._instante(int((ahora + timedelta(minutes=10)).timestamp()),
                    (ahora + timedelta(minutes=10)).astimezone(config.TZ)
                    .strftime("%Y-%m-%d")) is not None)

print("\nE. Un 'no llovió' vale igual que un 'sí'")
# La tasa base en Aguascalientes es ~14%: los 'no' son la mayoria de los datos
# y son los que impiden que el sistema aprenda a decir siempre que si.
# Posterior a todo lo anterior a proposito. El cursor es el `ts` mas alto ya
# visto, asi que una fila con marca ANTERIOR no se vuelve a leer nunca. En la
# malla eso no pasa -`ts` solo crece-, y para el caso en que si importe existe
# `procesar(desde=0)`, que es lo que se usa tras cambiar una regla.
hace_10 = ahora - timedelta(minutes=2)
mete(hace_10, 0)
chk("se incorpora", malla.procesar() == 1)
ultima = [f for f in store.read_observations()
          if f["valid_utc"] == store.round_slot(hace_10)]
chk("queda como no llovió", ultima and ultima[0]["rained"] == "0")

print("\nF. EL FALLO: una corrección humana SUSTITUYE a Open-Meteo")
# Aqui estaba el problema. La verificacion de Open-Meteo corre horas despues,
# asi que lo normal es que la confirmacion llegue antes. Pero al reves tambien
# pasa -contestas por radio una tormenta de hace un rato que ya se verifico- y
# entonces se tiraba justo el unico dato independiente que existe.
slot = store.round_slot(ahora - timedelta(hours=2))
store.append_observations([{"valid_utc": slot, "rained": 0, "mm": "",
                           "peak_score": "", "source": "openmeteo"}])
antes = [f for f in store.read_observations() if f["valid_utc"] == slot]
chk("Open-Meteo dice que no llovió",
    antes and antes[0]["rained"] == "0" and antes[0]["source"] == "openmeteo")

store.append_observations([{"valid_utc": slot, "rained": 1, "mm": "",
                           "peak_score": "", "source": "manual"}])
despues = [f for f in store.read_observations() if f["valid_utc"] == slot]
chk("sigue habiendo UNA fila para ese instante", len(despues) == 1,
    f"{len(despues)}")
chk("y ahora dice que sí llovió", despues and despues[0]["rained"] == "1",
    despues[0]["rained"] if despues else "")
chk("con origen manual", despues and despues[0]["source"] == "manual",
    despues[0]["source"] if despues else "")

print("\nG. Y al revés NO: Open-Meteo no pisa a una persona")
store.append_observations([{"valid_utc": slot, "rained": 0, "mm": "",
                           "peak_score": "", "source": "openmeteo"}])
final = [f for f in store.read_observations() if f["valid_utc"] == slot]
chk("la fila humana sobrevive",
    final and final[0]["source"] == "manual" and final[0]["rained"] == "1",
    f"{final[0]['source']} / {final[0]['rained']}" if final else "")

print("\nH. La sustitución no estropea el resto del archivo")
todas = store.read_observations()
chk("no hay instantes duplicados",
    len({f["valid_utc"] for f in todas}) == len(todas),
    f"{len(todas)} filas, {len({f['valid_utc'] for f in todas})} instantes")
chk("todas las filas tienen las columnas del esquema",
    all(set(f.keys()) == set(store.OBS_FIELDS) for f in todas))
chk("no quedó ningún archivo temporal",
    not any(n.endswith((".sustituyendo", ".migrando")) for n in os.listdir(tmp)),
    str(os.listdir(tmp)))

print("\nI. Sin base de datos de la malla, todo sigue funcionando")
config.CLIMA_DB = os.path.join(tmp, "no-existe.db")
chk("procesar no lanza y devuelve 0", malla.procesar() == 0)
config.CLIMA_DB = ""
chk("con la ruta vacía tampoco", malla.procesar() == 0)

print("\nJ. Una base sin la tabla 'respuestas' tampoco rompe nada")
# Es el estado normal hasta que alguien usa el comando por primera vez.
vacia = os.path.join(tmp, "vacia.db")
sqlite3.connect(vacia).close()
config.CLIMA_DB = vacia
chk("se ignora en silencio", malla.procesar() == 0)

print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
