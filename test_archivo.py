"""El historial: que guarde, que indexe y sobre todo que pode.

Lo que mas importa aqui es la poda. Un archivo que crece y nunca se limpia
llena la memoria USB en silencio y el sistema se para meses despues, en un
momento sin relacion aparente con el codigo que lo causo.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

from nowcast import archivo, config

ok = True


def chk(nombre: str, condicion: bool, detalle: str = "") -> None:
    global ok
    print(f"  {'PASA' if condicion else 'FALLA'}  {nombre}"
          + (f"  [{detalle}]" if detalle else ""))
    if not condicion:
        ok = False


# Todo el modulo trabaja bajo archivo.RAIZ; se redirige a un temporal.
tmp = tempfile.mkdtemp()
archivo.RAIZ = os.path.join(tmp, "hist")
archivo.DIAS_JSON = os.path.join(archivo.RAIZ, "dias.json")
os.makedirs(archivo.RAIZ, exist_ok=True)

# Un PNG minimo de verdad, para que la copia sea una copia real.
PNG = os.path.join(tmp, "satelite.png")
with open(PNG, "wb") as fh:
    fh.write(bytes.fromhex(
        "89504e470d0a1a0a0000000d494844520000000100000001080600000"
        "01f15c4890000000a49444154789c6300010000050001"
        "0d0a2db40000000049454e44ae426082"))

BOUNDS = [[19.0, -105.0], [24.0, -99.0]]


def instante(dias_atras: float, hhmm: str) -> datetime:
    base = datetime.now(timezone.utc) - timedelta(days=dias_atras)
    local = base.astimezone(config.TZ).replace(
        hour=int(hhmm[:2]), minute=int(hhmm[2:]), second=0, microsecond=0)
    return local.astimezone(timezone.utc)


print("A. Guardar un cuadro")
t = instante(0, "1245")
archivo.guardar(t, PNG, BOUNDS, [[21.9, -102.3, 4]])
dia, hora = archivo._dia_hora(t)
chk("el PNG queda archivado",
    os.path.exists(os.path.join(archivo.RAIZ, dia, f"{hora}.png")))
chk("los rayos quedan archivados",
    os.path.exists(os.path.join(archivo.RAIZ, dia, f"{hora}.r.json")))

indice = json.load(open(archivo._indice_dia(dia)))
chk("el indice del dia lista el cuadro",
    any(c["t"] == hora for c in indice["cuadros"]), str(indice["cuadros"]))
chk("el indice guarda el encuadre", indice.get("bounds") == BOUNDS)
chk("cuenta los destellos",
    indice["cuadros"][0].get("r") == 4, str(indice["cuadros"][0]))
chk("el dia aparece en dias.json",
    dia in json.load(open(archivo.DIAS_JSON)))

print("\nB. Sin rayos no se escribe archivo de rayos")
t2 = instante(0, "1300")
archivo.guardar(t2, PNG, BOUNDS, [])
_, hora2 = archivo._dia_hora(t2)
chk("no hay .r.json cuando no hubo destellos",
    not os.path.exists(os.path.join(archivo.RAIZ, dia, f"{hora2}.r.json")))
chk("pero el cuadro si se guarda",
    os.path.exists(os.path.join(archivo.RAIZ, dia, f"{hora2}.png")))

print("\nC. Repetir el mismo instante no duplica")
archivo.guardar(t, PNG, BOUNDS, [[21.9, -102.3, 9]])
indice = json.load(open(archivo._indice_dia(dia)))
iguales = [c for c in indice["cuadros"] if c["t"] == hora]
chk("una sola entrada por instante", len(iguales) == 1, f"{len(iguales)}")
chk("se queda con el dato nuevo", iguales[0].get("r") == 9, str(iguales[0]))

print("\nD. Sin encuadre no se archiva nada")
antes = len(json.load(open(archivo._indice_dia(dia)))["cuadros"])
archivo.guardar(instante(0, "1315"), PNG, None, None)
despues = len(json.load(open(archivo._indice_dia(dia)))["cuadros"])
chk("un cuadro sin bounds se descarta", antes == despues, f"{antes} -> {despues}")

print("\nE. La poda")
for atras in (1, 3, 6, 8, 12, 30):
    archivo.guardar(instante(atras, "0900"), PNG, BOUNDS, None)
dias_antes = json.load(open(archivo.DIAS_JSON))
print(f"     dias guardados: {len(dias_antes)}")

borrados = archivo.podar(7)
dias_despues = json.load(open(archivo.DIAS_JSON))
chk("borra los mas viejos que el limite", borrados >= 3, f"{borrados} borrados")
chk("conserva los recientes", len(dias_despues) >= 4, str(dias_despues))

limite = (datetime.now(timezone.utc).astimezone(config.TZ)
          - timedelta(days=7)).strftime("%Y-%m-%d")
chk("ninguno de los que quedan pasa del limite",
    all(d >= limite for d in dias_despues), f"limite {limite}")
chk("las carpetas viejas ya no existen en disco",
    all(not os.path.isdir(os.path.join(archivo.RAIZ, d))
        for d in dias_antes if d < limite))

print("\nF. Carpetas huerfanas: una corrida que murio a medias")
# Se crea una carpeta vieja sin tocar el indice, como pasaria si el proceso
# muriera despues de copiar el PNG y antes de escribir el indice.
huerfana = os.path.join(archivo.RAIZ, "2020-01-01")
os.makedirs(huerfana, exist_ok=True)
shutil.copyfile(PNG, os.path.join(huerfana, "0000.png"))
archivo.podar(7)
chk("tambien se limpian las que el indice no menciona",
    not os.path.isdir(huerfana))

print("\nG. Podar dos veces seguidas no rompe nada")
chk("la segunda poda no borra nada y no falla", archivo.podar(7) == 0)

shutil.rmtree(tmp, ignore_errors=True)
print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
