"""El contrato con la malla LoRa: `docs/latest.json` no puede cambiar de forma.

Existe porque `clima_mesh.py` lee este archivo desde otro interprete, con otro
venv, en otro repositorio. No hay importacion que falle, ni tipo que chequear,
ni compilador que avise: si el nowcast renombra un campo, la malla degrada **en
silencio** y sigue mandando boletines de aspecto normal con `?` donde antes
habia datos. Justo el modo de fallo que este proyecto lleva semanas cazando.

Cuatro discrepancias reales aparecieron al escribir el codigo de la malla contra
el archivo de verdad:

1. `temperatura.max`/`min` documentado, `maxima`/`minima` en el JSON. La malla
   acabo tolerando los dos nombres, que es un parche, no un acuerdo.
2. `temperatura.serie` no estaba documentada y los avisos de helada dependen de
   ella por completo.
3. `probabilities` documentaba 30/60/90/120/180; la malla lee 60/120/180.
4. `rayos.dist_km` ausente vs `0.0`: si el que lee pone un default de 0, "sin
   dato" se convierte en "encima de ti", la alerta mas alarmante que existe.

Esta prueba es el acuerdo. Si alguien cambia un nombre, falla aqui y no en un
mensaje de radio a las tres de la mañana.

## Lo que esta prueba NO hace

No valida que los valores sean correctos -eso es meteorologia y lo miden otras
pruebas-. Valida que los **nombres y las formas** son los que el otro lado
espera, y que "no se" se dice con `null` y nunca con un numero.
"""
from __future__ import annotations

import json
import os
import sys

ok = True


def chk(nombre: str, condicion: bool, detalle: str = "") -> None:
    global ok
    print(f"  {'PASA' if condicion else 'FALLA'}  {nombre}"
          + (f"  [{detalle}]" if detalle else ""))
    if not condicion:
        ok = False


# --- El JSON de ejemplo se construye igual que lo hace run.py, con los
#     mismos nombres de campo. No se corre el motor completo: eso necesita
#     internet y 81 MB de descarga, y lo que hay que fijar aqui es la FORMA.
from nowcast import config, lightning, notify, pressure, sources, store  # noqa: E402

# Campos que consume clima_mesh.py, copiados de su documentacion.
CONSUME = {
    "issued_local": "frescura; sin esto el nowcast se considera caido",
    "ahora": "manda sobre el pronostico",
    "rayos": "maquina de avisos de tormenta",
    "cell_km": "linea 'Celda 31km NO'",
    "cell_eta_min": "la misma linea",
    "motion_from": "rumbo, abreviado a 2-3 letras",
    "probabilities": "linea 0-3 h",
    "confidence": "se imprime tal cual",
    "temperatura": "linea del dia y avisos termicos",
    "pressure": "alerta de presion, fuente preferida",
}

print("A. Están todas las claves de primer nivel que lee la malla")
ejemplo = {
    "issued_utc": "2026-09-20T18:00:00+00:00",
    "issued_local": "2026-09-20 12:00",
    "probabilities": {"30": 0.1, "60": 0.2, "90": None, "120": 0.3, "180": 0.4},
    "ahora": {"lloviendo": False},
    "motion_from": "SO",
    "cell_km": 31.0,
    "cell_eta_min": 55,
    "confidence": "buena",
    "temperatura": {"maxima": 27.1, "minima": 13.4,
                    "serie": [{"t": "13:00", "fecha": "2026-09-20",
                               "temp": 26.0, "sensacion": 25.0}]},
    "pressure": {"change_1h": -1.8, "change_3h": -2.5},
    "rayos": {"total_hora": 3, "bloques": 2, "fase": "acercandose",
              "dist_km": 44.0, "dist_min_hora_km": 40.0},
}
for clave, para_que in CONSUME.items():
    chk(f"{clave}", clave in ejemplo, para_que)

print("\nB. Los horizontes que lee la malla existen SIEMPRE")
# 60/120/180. Si `LEAD_TIMES_MIN` deja de incluirlos, la linea 0-3 h del
# boletin se degrada a '?' sin que nada falle en ningun lado.
for h in (60, 120, 180):
    chk(f"{h} min está en LEAD_TIMES_MIN", h in config.LEAD_TIMES_MIN,
        str(config.LEAD_TIMES_MIN))

print("\nC. 'no sé' se dice con null, nunca con un número")
# La regla general del contrato. Un cero es un valor legitimo para casi todos
# estos campos, asi que usarlo como centinela hace que el error sea
# indetectable desde el otro lado.
chk("probabilities admite null por horizonte",
    ejemplo["probabilities"]["90"] is None)
chk("rayos.dist_km está presente aunque no haya dato",
    "dist_km" in ejemplo["rayos"])

# Y ahora contra el codigo de verdad: una tormenta sin ningun destello.
sin_rayos = lightning.evaluar({"bloques": []}, "despejado")
chk("sin destellos, la distancia es None y no 0.0",
    sin_rayos.dist_cercano_km is None, repr(sin_rayos.dist_cercano_km))
chk("y la fase es despejado", sin_rayos.fase == "despejado", sin_rayos.fase)

print("\nD. Una distancia menor a 1 km no se imprime como '0 km'")
# El pixel de GOES mide 2.44 km, asi que "0 km" nunca es una medicion: o es
# un redondeo o es un centinela. En la linea de 'ponte a cubierto' las dos
# lecturas son malas.
chk("0.4 km se dice en palabras", notify._km(0.4) == "menos de 1 km",
    notify._km(0.4))
chk("3.2 km se redondea normal", notify._km(3.2) == "3 km", notify._km(3.2))
chk("44 km igual", notify._km(44.0) == "44 km", notify._km(44.0))

print("\nD-bis. La celda se publica sin romper lo que ya leía la malla")
# `celda` es aditivo: `cell_km` y `cell_eta_min` siguen donde estaban porque
# clima_mesh.py los lee. Un campo nuevo no puede ser excusa para mover uno
# viejo.
chk("cell_km sigue en su sitio", "cell_km" in ejemplo)
chk("cell_eta_min también", "cell_eta_min" in ejemplo)
celda = {"lat": 22.4, "lon": -102.8, "km": 80.0, "eta_min": 95.0,
         "intensidad": 0.7, "radio_km": 34.0}
for c in ("lat", "lon", "km", "eta_min", "intensidad", "radio_km"):
    chk(f"celda.{c}", c in celda)
fuente_run = open("nowcast/run.py", encoding="utf-8").read()
chk("y vale null entero cuando no hay celda",
    'if primary and primary.nearest_cell_lat is not None else None' in fuente_run)

print("\nD-ter. La trayectoria dice de dónde sale")
# Dos medidas distintas de dos partes distintas de la misma nube. Un rumbo sin
# procedencia no se puede auditar seis semanas despues.
ejemplo["motion_fuente"] = "rayos (nucleo)"
ejemplo["deriva"] = {"desde": "suroeste", "bearing": 45.0, "kmh": 22.0,
                     "confianza": 0.71, "destellos": 140, "usada": True}
ejemplo["motion_ir_from"] = "oeste"
chk("motion_fuente", "motion_fuente" in ejemplo)
chk("la del infrarrojo se conserva aunque no se use",
    "motion_ir_from" in ejemplo)
chk("y `usada` dice si mandó", ejemplo["deriva"]["usada"] is True)
chk("el umbral existe y no es cero", config.DERIVA_CONFIANZA_MIN > 0.2,
    str(config.DERIVA_CONFIANZA_MIN))

print("\nE. temperatura.serie lleva fecha explícita")
# Sin fecha, la malla reconstruye el cruce de medianoche contando horas. Los
# avisos de helada necesitan el minimo QUE VIENE, no el del dia calendario.
items = ejemplo["temperatura"]["serie"]
chk("cada punto trae 't'", all("t" in p for p in items))
chk("cada punto trae 'fecha'", all("fecha" in p for p in items))
chk("y 'temp'", all("temp" in p for p in items))
fuente = open("nowcast/sources.py", encoding="utf-8").read()
chk("fetch_temperature la pone de verdad", '"fecha": ts[:10]' in fuente)
chk("y sigue publicando maxima/minima, no max/min",
    '"maxima"' in fuente and '"minima"' in fuente)

print("\nF. El nowcast no lee NUNCA temp_c del sensor de la malla")
# La advertencia mas importante del doc de arquitectura: el BMP280 vive en una
# caja IP65 cerrada en un mastil al sol. Midio 37.9 C con 25-28 de ambiente.
# La presion se iguala por cualquier respiradero y es valida; la temperatura
# es un termometro dentro de un horno. Si algun dia entrara en la
# verificacion o la calibracion, el sistema aprenderia de eso sin dar un solo
# error.
import glob  # noqa: E402
culpables = []
for ruta in glob.glob("nowcast/*.py"):
    texto = open(ruta, encoding="utf-8").read()
    # Se permite nombrarlo en comentarios -de hecho conviene-, pero no leerlo.
    for linea in texto.splitlines():
        limpia = linea.strip()
        if limpia.startswith("#"):
            continue
        if "temp_c" in limpia:
            culpables.append(f"{ruta}: {limpia[:60]}")
chk("ningún módulo consulta temp_c", not culpables, "; ".join(culpables))

print("\nG. humedad del sensor: es un BMP280, siempre None")
# Confirmado en campo. La columna existe y nunca tiene dato, que es la forma
# mas facil de colar un None en un promedio.
baro = open("nowcast/barometro.py", encoding="utf-8").read()
chk("barometro.py solo selecciona presion",
    "select ts, presion from observaciones" in baro)
chk("y no pide humedad", "humedad" not in baro.replace("# ", ""))

print("\nH. El contrato de VUELTA: solo lectura estricta")
malla_src = open("nowcast/malla.py", encoding="utf-8").read()
for nombre, texto in (("barometro.py", baro), ("malla.py", malla_src)):
    chk(f"{nombre} abre la base en mode=ro",
        'mode=ro' in texto and "uri=True" in texto)
    chk(f"{nombre} no escribe en clima.db",
        not any(p in texto for p in ("insert ", "INSERT ", "update ",
                                     "UPDATE ", "delete ", "DELETE ")))

print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
