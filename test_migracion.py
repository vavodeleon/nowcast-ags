"""Añadir una columna no puede corromper el historial de aprendizaje.

Este archivo existe por un fallo que estuve a punto de meter. Al añadir las
columnas de presion a predictions.csv, el archivo existente conservaba la
cabecera vieja mientras las filas nuevas se escribian con la lista nueva de
campos. A partir de esa linea, cada valor quedaba bajo el nombre equivocado.

Lo grave no es el desorden: es que NO DA ERROR. El sistema habria seguido
corriendo, calibrandose con datos desplazados, y el unico sintoma habria sido
que las predicciones empeoraran poco a poco sin motivo aparente. Con 14,500
pares acumulados -semanas de temporada de lluvias- eso no se recupera.
"""
from __future__ import annotations

import csv
import os
import shutil
import sys
import tempfile

from nowcast import config, store

ok = True


def chk(nombre: str, condicion: bool, detalle: str = "") -> None:
    global ok
    print(f"  {'PASA' if condicion else 'FALLA'}  {nombre}"
          + (f"  [{detalle}]" if detalle else ""))
    if not condicion:
        ok = False


tmp = tempfile.mkdtemp()
config.PREDICTIONS_CSV = os.path.join(tmp, "predictions.csv")

# Un historial "viejo": las columnas de antes de añadir la presion.
VIEJAS = [c for c in store.PRED_FIELDS if not c.startswith("pres_")]
HISTORIAL = 200

with open(config.PREDICTIONS_CSV, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=VIEJAS)
    w.writeheader()
    for i in range(HISTORIAL):
        w.writerow({c: f"{c}-{i}" for c in VIEJAS})

print("A. El historial previo sobrevive")
store.append_predictions([{c: f"nuevo-{c}" for c in store.PRED_FIELDS}])
filas = store.read_predictions()
chk("no se pierde ninguna fila", len(filas) == HISTORIAL + 1,
    f"{len(filas)} de {HISTORIAL + 1}")

print("\nB. Los valores viejos siguen bajo su propio nombre")
# Esto es lo que fallaba: sin migracion, 'p_final' de una fila vieja acababa
# leyendose como si fuera otra columna.
desalineadas = [i for i, f in enumerate(filas[:HISTORIAL])
                if any(f.get(c) != f"{c}-{i}" for c in VIEJAS)]
chk("ninguna fila vieja quedó desplazada", not desalineadas,
    f"{len(desalineadas)} filas mal" if desalineadas else "")
chk("p_final de la primera fila es el suyo",
    filas[0].get("p_final") == "p_final-0", str(filas[0].get("p_final")))

print("\nC. Las columnas nuevas quedan vacías en lo viejo, no inventadas")
chk("pres_1h vacío en las filas antiguas",
    all(f.get("pres_1h") in ("", None) for f in filas[:HISTORIAL]),
    str(filas[0].get("pres_1h")))
chk("y con valor en la nueva",
    filas[-1].get("pres_1h") == "nuevo-pres_1h", str(filas[-1].get("pres_1h")))

print("\nD. La cabecera del archivo quedó al día")
with open(config.PREDICTIONS_CSV, newline="", encoding="utf-8") as fh:
    cabecera = next(csv.reader(fh))
chk("la cabecera es la nueva", cabecera == store.PRED_FIELDS,
    f"{len(cabecera)} columnas")

print("\nE. Migrar dos veces no hace nada la segunda")
antes = open(config.PREDICTIONS_CSV, encoding="utf-8").read()
store._ensure(config.PREDICTIONS_CSV, store.PRED_FIELDS)
chk("el archivo no cambia", open(config.PREDICTIONS_CSV, encoding="utf-8").read() == antes)

print("\nF. Si el archivo tiene columnas que el código ya no conoce, no se toca")
# Quitar columnas destruiría datos. Es preferible no escribir a escribir mal.
raro = os.path.join(tmp, "raro.csv")
with open(raro, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=store.PRED_FIELDS + ["columna_desconocida"])
    w.writeheader()
    w.writerow({c: "x" for c in store.PRED_FIELDS + ["columna_desconocida"]})
copia = open(raro, encoding="utf-8").read()
store._ensure(raro, store.PRED_FIELDS)
chk("se deja intacto", open(raro, encoding="utf-8").read() == copia)

print("\nG. Un archivo que no existe se crea con la cabecera nueva")
nuevo = os.path.join(tmp, "nuevo.csv")
store._ensure(nuevo, store.PRED_FIELDS)
with open(nuevo, newline="", encoding="utf-8") as fh:
    chk("cabecera correcta", next(csv.reader(fh)) == store.PRED_FIELDS)

print("\nH. Lo mismo vale para las observaciones")
config.OBSERVATIONS_CSV = os.path.join(tmp, "observations.csv")
with open(config.OBSERVATIONS_CSV, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=store.OBS_FIELDS[:-1])   # sin 'source'
    w.writeheader()
    w.writerow({c: f"{c}-0" for c in store.OBS_FIELDS[:-1]})
store.append_observations([{c: "nueva" for c in store.OBS_FIELDS}])
obs = store.read_observations()
chk("la observación vieja conserva su valid_utc",
    obs[0].get("valid_utc") == "valid_utc-0", str(obs[0].get("valid_utc")))
chk("y la nueva trae la columna añadida",
    obs[-1].get("source") == "nueva", str(obs[-1].get("source")))

shutil.rmtree(tmp, ignore_errors=True)
print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
