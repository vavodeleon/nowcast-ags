"""Quita de data/salud.csv las filas que metió la suite de pruebas.

    python limpiar_salud_pruebas.py            # solo muestra lo que quitaría
    python limpiar_salud_pruebas.py --aplicar  # lo quita, con respaldo

## Qué pasó

Hasta el 28/09/2026, `test_presion.py` y `test_barometro.py` disparaban un aviso
de presión sin redirigir el archivo de salud. Cada corrida de `pruebas.sh`
añadía dos filas falsas al registro real de migrañas: en el Pi, y de ahí al
repositorio. Salió a la luz porque el Mac empezó a correr la suite y chocó en
git con ese archivo.

Ya está arreglado de tres formas (las pruebas redirigen, `pruebas.sh` manda los
datos a un temporal, y una guardia falla si algo toca `data/`). Esto limpia lo
que entró antes.

## Cómo reconoce una fila falsa

Por su huella exacta, que es la misma en cada corrida porque sale de datos fijos
de las pruebas, y solo si **nadie la contestó**:

- test_presion:   rápida, −1.5 / −3.1 / −4.0, «muy alto», fuente «modelo»
- test_barometro: rápida, −2.3 / −2.2 /  0.0, «tranquilo», fuente «sensor local»

Una fila con respuesta no se toca nunca: si alguien contestó, era un aviso real.

Aparte enseña, sin borrarlas, las parejas de filas sin respuesta separadas por
menos de 5 segundos. Es la otra firma de la suite -las dos pruebas corren una
tras otra- y sirve para cazar filas de versiones viejas de las pruebas, con
otros números. Esas se revisan a mano.
"""
from __future__ import annotations

import csv
import os
import shutil
import sys
from datetime import datetime

from nowcast import config, store

HUELLAS = [
    {"tipo": "rapida", "change_1h": -1.5, "change_3h": -3.1,
     "change_24h": -4.0, "nivel": "muy alto", "fuente": "modelo"},
    {"tipo": "rapida", "change_1h": -2.3, "change_3h": -2.2,
     "change_24h": 0.0, "nivel": "tranquilo", "fuente": "sensor local"},
]


def _n(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def es_de_prueba(f: dict) -> bool:
    if f.get("dolor") or f.get("ts_respuesta"):
        return False              # alguien contestó: era real
    for h in HUELLAS:
        if all((abs(_n(f.get(k)) - v) < 1e-6 if isinstance(v, float)
                and _n(f.get(k)) is not None else f.get(k) == v)
               for k, v in h.items()):
            return True
    return False


def _t(f):
    try:
        return datetime.fromisoformat(f.get("ts_aviso", ""))
    except ValueError:
        return None


def main() -> int:
    aplicar = "--aplicar" in sys.argv
    ruta = config.SALUD_CSV
    if not os.path.exists(ruta):
        print(f"No existe {ruta}. Nada que limpiar.")
        return 0
    filas = store.read_salud()
    falsas = [f for f in filas if es_de_prueba(f)]
    buenas = [f for f in filas if not es_de_prueba(f)]

    print(f"{len(filas)} filas en {ruta}")
    print(f"{len(falsas)} con la huella exacta de las pruebas:")
    for f in falsas[:15]:
        print(f"   {f['ts_aviso']}  {f['nivel']:<10} {f['fuente']}")
    if len(falsas) > 15:
        print(f"   ... y {len(falsas) - 15} más")

    # Parejas sospechosas que NO casan con la huella: se enseñan, no se tocan.
    sin_resp = sorted((f for f in buenas if not f.get("dolor")),
                      key=lambda f: f.get("ts_aviso", ""))
    parejas = []
    for a, b in zip(sin_resp, sin_resp[1:]):
        ta, tb = _t(a), _t(b)
        if ta and tb and abs((tb - ta).total_seconds()) < 5:
            parejas.append((a, b))
    if parejas:
        print(f"\n{len(parejas)} pareja(s) sin respuesta a menos de 5 s, que "
              "NO casan con la huella.")
        print("Pueden ser de versiones viejas de las pruebas. Revisar a mano:")
        for a, b in parejas[:10]:
            print(f"   {a['ts_aviso']}  {a['change_1h']}/{a['change_3h']}  "
                  f"{a['fuente']}")
            print(f"   {b['ts_aviso']}  {b['change_1h']}/{b['change_3h']}  "
                  f"{b['fuente']}")

    if not falsas:
        print("\nNo hay filas falsas con la huella conocida.")
        return 0
    if not aplicar:
        print(f"\nNo se ha tocado nada. Para quitar las {len(falsas)}:")
        print("   python limpiar_salud_pruebas.py --aplicar")
        return 0

    # Fuera del repositorio: correr.sh sube lo que hay en data/, y un respaldo
    # ahi acabaria publicado en la siguiente corrida.
    respaldo = os.path.join(os.path.expanduser("~"),
                            "salud.csv.antes-de-limpiar")
    shutil.copy2(ruta, respaldo)
    tmp = ruta + ".limpiando"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=store.SALUD_FIELDS,
                           extrasaction="ignore")
        w.writeheader()
        for f in buenas:
            w.writerow(f)
    os.replace(tmp, ruta)
    print(f"\nQuitadas {len(falsas)} filas. Quedan {len(buenas)}.")
    print(f"Respaldo del archivo anterior: {respaldo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
