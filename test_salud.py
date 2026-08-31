"""El bucle de aprendizaje del canal de salud, de punta a punta.

Existe porque el 30 de agosto de 2026 hubo la primera confirmacion real: el
aviso de presion salio a las 19:31 y ella tuvo dolor de cabeza. Un acierto.
Pero el sistema no tenia forma de registrarlo, asi que ese dato se habria
perdido, y los umbrales habrian seguido siendo numeros de la literatura en
vez de numeros suyos.

Lo que se prueba aqui es el circuito completo: se manda un aviso con botones,
ella toca uno, el Raspberry lo recoge en la siguiente corrida, y queda una
fila con la respuesta JUNTO A la presion que habia en el momento del aviso.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

from nowcast import config, http, notify, salud, store

ok = True


def chk(nombre: str, condicion: bool, detalle: str = "") -> None:
    global ok
    print(f"  {'PASA' if condicion else 'FALLA'}  {nombre}"
          + (f"  [{detalle}]" if detalle else ""))
    if not condicion:
        ok = False


tmp = tempfile.mkdtemp()
config.SALUD_CSV = os.path.join(tmp, "salud.csv")
config.STATE_JSON = os.path.join(tmp, "state.json")
config.NTFY_TOPIC_SALUD = "avisos-de-prueba"
config.NTFY_TOPIC_RESPUESTAS = "respuestas-de-prueba"

enviados: list = []
notify.send = lambda titulo, cuerpo, **kw: (
    enviados.append({"titulo": titulo, "cuerpo": cuerpo, **kw}), True)[1]
notify._cooldown_ok_hours = lambda c, h: True
notify._mark = lambda c: None


class Estado:
    """Lo minimo de PressureState que el aviso necesita."""
    change_1h = -1.8
    change_3h = -2.5
    change_24h = -1.2
    level = "vigilancia"
    fuente = "sensor local"
    now_msl = 1018.5
    # Solo el aviso de caida rapida: si ademas fuera is_falling_now, una
    # sola llamada mandaria DOS avisos y las cuentas de esta prueba dejarian
    # de ser obvias. Que manda dos ya lo cubre test_presion.
    is_falling_now = False
    is_falling_fast = True
    is_risky_soon = False
    forecast_drop = None
    forecast_drop_at = None
    forecast_drop_in_h = None
    velocidad_hpa_h = 1.8


print("A. El aviso sale con botones")
notify.maybe_pressure_alert(Estado())
chk("se envió exactamente un aviso", len(enviados) == 1, f"{len(enviados)}")
con_botones = [e for e in enviados if e.get("actions")]
chk("lleva botones de respuesta", bool(con_botones))
acciones = con_botones[0]["actions"] if con_botones else ""
chk("hay un botón de sí", "Si me dolio" in acciones)
chk("y uno de no", ", No, " in acciones)
chk("publican en el canal de respuestas, no en el de avisos",
    "respuestas-de-prueba" in acciones and "avisos-de-prueba" not in acciones)
chk("el cuerpo del botón lleva la marca de tiempo del aviso",
    "body=dolor:si @2" in acciones)
print(f"\n     Botones de la notificación:")
for parte in acciones.split("; "):
    print(f"       {parte}")

print("\nB. Quedó registrada la presión de ESE momento")
filas = store.read_salud()
chk("una fila por aviso", len(filas) == len(enviados), f"{len(filas)} filas")
chk("con la caída de 3 h del momento", filas[0]["change_3h"] == "-2.5",
    filas[0]["change_3h"])
chk("y la fuente del dato", filas[0]["fuente"] == "sensor local",
    filas[0]["fuente"])
chk("todavía sin respuesta", filas[0]["dolor"] == "", repr(filas[0]["dolor"]))

print("\nC. Ella toca 'Sí me dolió' y el Pi lo recoge")
ts_aviso = filas[0]["ts_aviso"]
respuesta = {"id": "abc123", "time": 1788148000, "event": "message",
             "topic": "respuestas-de-prueba",
             "message": f"dolor:si @{ts_aviso}"}
http.get_text = lambda url, **k: (
    json.dumps({"id": "x", "event": "open", "topic": "t"}) + "\n"
    + json.dumps(respuesta) + "\n"
    + json.dumps({"id": "y", "event": "keepalive", "topic": "t"}))

chk("incorpora una respuesta", salud.procesar() == 1)
filas = store.read_salud()
chk("la fila quedó contestada", filas[0]["dolor"] == "si", filas[0]["dolor"])
chk("con hora de respuesta", bool(filas[0]["ts_respuesta"]),
    filas[0]["ts_respuesta"])
chk("y la presión del aviso intacta", filas[0]["change_3h"] == "-2.5")

print("\nD. No reprocesa lo mismo dos veces")
chk("la segunda pasada no incorpora nada", salud.procesar() == 0)
chk("recuerda el último mensaje visto",
    store.load_json(config.STATE_JSON, {}).get("salud_ultimo_id") == "abc123")

print("\nE. Una respuesta 'no' también cuenta")
enviados.clear()
notify.maybe_pressure_alert(Estado())
filas = store.read_salud()
pendiente = [f for f in filas if not f["dolor"]]
chk("hay un aviso pendiente nuevo", len(pendiente) == 1, f"{len(pendiente)}")
http.get_text = lambda url, **k: json.dumps(
    {"id": "def456", "time": 1788149000, "event": "message",
     "topic": "t", "message": f"dolor:no @{pendiente[0]['ts_aviso']}"})
salud.procesar()
filas = store.read_salud()
chk("queda registrada como no", filas[-1]["dolor"] == "no", filas[-1]["dolor"])

print("\nF. Basura en el canal no rompe nada")
http.get_text = lambda url, **k: "esto no es json\n{\"roto\":\n"
chk("se ignora sin lanzar", salud.procesar() == 0)
http.get_text = lambda url, **k: None
chk("sin respuesta del servidor tampoco", salud.procesar() == 0)

print("\nG. Sin canal de respuestas, todo sigue funcionando")
config.NTFY_TOPIC_RESPUESTAS = ""
chk("no se piden respuestas", salud.procesar() == 0)
chk("y el aviso sale sin botones", notify._botones_salud("x") == "")
enviados.clear()
notify.maybe_pressure_alert(Estado())
chk("el aviso de presión se manda igual", len(enviados) >= 1)
config.NTFY_TOPIC_RESPUESTAS = "respuestas-de-prueba"

print("\nH. El resumen no saca conclusiones con cuatro casos")
r = salud.resumen()
chk("cuenta las respondidas", r["respondidos"] == 2, str(r["respondidos"]))
chk("y los aciertos", r["aciertos"] == 1, str(r["aciertos"]))
chk("pero avisa de que NO son suficientes", r["suficientes"] is False,
    f"con {r['respondidos']} respuestas")
print(f"\n     Resumen actual: {r}")

# Con 30 respuestas sí se considera utilizable.
for i in range(30):
    store.append_salud({"ts_aviso": f"t{i}", "tipo": "ahora",
                        "change_1h": "-1.0", "change_3h": "-2.0",
                        "change_24h": "-1.0", "nivel": "vigilancia",
                        "fuente": "modelo", "dolor": "si" if i % 3 else "no",
                        "ts_respuesta": "x"})
r = salud.resumen()
chk("con 32 respuestas ya se considera suficiente", r["suficientes"] is True,
    f"{r['respondidos']} respuestas")
chk("compara la caída media con y sin dolor",
    r["caida_3h_con_dolor"] is not None and r["caida_3h_sin_dolor"] is not None,
    f"con {r['caida_3h_con_dolor']} / sin {r['caida_3h_sin_dolor']}")

shutil.rmtree(tmp, ignore_errors=True)
print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
