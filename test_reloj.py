"""reloj.json: pequeño, estable, y diciendo lo mismo que la página.

El veredicto vive en dos sitios -docs/index.html y nowcast/reloj.py- porque
la página no puede llamar a Python y el reloj no debe cargar la página. Esta
prueba ejecuta LA FUNCIÓN DE LA PÁGINA, sacada del HTML tal cual, con node, y
la compara con la de Python sobre una rejilla de casos. Si alguien cambia un
umbral en un lado y no en el otro, aquí se rompe.
"""
from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from nowcast import config, reloj, run, store

ok = True


def chk(nombre, cond, detalle=""):
    global ok
    print(f"  {'PASA' if cond else 'FALLA'}  {nombre}" + (f"  [{detalle}]" if detalle else ""))
    if not cond:
        ok = False


def casos():
    out = [{"probabilities": {}}, {"probabilities": {"60": None, "15": None}}]
    for p60, extra, eta, ahora in itertools.product(
            # A cada lado de cada umbral (.25, .45, .70; ETA 90): un umbral movido
            # en un solo lado cae siempre entre dos valores vecinos.
            (None, 0.05, 0.249, 0.25, 0.449, 0.45, 0.6, 0.699, 0.70, 0.95),
            (0.0, 0.449, 0.45),
            (None, 40.5, 90, 90.01, 120),
            ({}, {"lloviendo": True, "corroborado_por": "rayos"},
             {"forma": "yunque"})):
        probs = {"15": 0.1, "60": p60, "180": extra}
        out.append({"probabilities": probs, "cell_eta_min": eta, "ahora": ahora,
                    "motion_from": "oeste", "motion_speed_kmh": 22.5})
    return out


print("A. El veredicto de Python es el de la página")
html = open("docs/index.html", encoding="utf-8").read()
m = re.search(r"function veredicto\(d\)\{.*?\n\}\n", html, re.S)
chk("se encuentra la función en la página", m is not None)
node = shutil.which("node")
if m and node:
    cs = casos()
    js = (m.group(0) + "\nconst hay = v => v !== null && v !== undefined && "
          "!Number.isNaN(Number(v));\n"
          f"const cs = {json.dumps(cs)};\n"
          "console.log(JSON.stringify(cs.map(d => { const v = veredicto(d);"
          " return [v.ic, v.t, v.s, v.c.replace(/^var\\(--|\\)$/g, '')]; })));")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(js)
    salida = subprocess.run([node, fh.name], capture_output=True, text=True)
    os.unlink(fh.name)
    if salida.returncode != 0:
        chk("node ejecuta la función", False, salida.stderr[-300:])
    else:
        pagina = json.loads(salida.stdout)
        distintos = []
        for d, p in zip(cs, pagina):
            v = reloj.veredicto(d)
            if [v["icono"], v["titular"], v["apoyo"], v["color"]] != p:
                distintos.append((d, p, v))
        chk(f"coinciden los {len(cs)} casos", not distintos,
            str(distintos[0]) if distintos else "")
        titulares = {p[1] for p in pagina}
        chk("y la rejilla recorre todas las ramas", len(titulares) >= 9,
            f"{len(titulares)} titulares distintos")
elif not node:
    print("  (node no instalado: comparación omitida)")

print("\nB. El archivo compacto")
d = {"issued_utc": "2026-10-05T19:30:58+00:00", "issued_local": "2026-10-05 13:30",
     "probabilities": {"15": 0.224, "30": 0.21, "45": None, "60": 0.5,
                       "90": 0.255, "120": 0.318, "180": 0.321},
     "cell_eta_min": 40.4, "motion_from": "sur", "motion_speed_kmh": 30,
     "celda": {"km": 35.2, "eta_min": 40.4, "rumbo": 90.0, "intensidad": 0.7},
     "motion_giro": True, "nube_baja": {"frac": 0.8}, "degradado": False,
     "pressure": {"series": [{"x": 1}] * 200}}
c = reloj.compacto(d)
chk("versión", c["v"] == 1)
chk("hora corta", c["hora"] == "13:30")
chk("titular de la página", c["titular"] == "Lluvia en ~40 min", c["titular"])
chk("plazos en orden con null donde no se sabe",
    [x["min"] for x in c["p"]] == [15, 30, 45, 60, 90, 120, 180]
    and c["p"][2]["p"] is None)
chk("la celda dice de dónde viene ELLA: va al este, viene del oeste",
    c["celda"]["desde"] == "oeste", str(c["celda"]))
chk("giro y nube baja", c["giro"] is True and c["nube_baja"] is True)
tam = len(json.dumps(c, ensure_ascii=False).encode())
chk("menos de 1 KB", tam < 1024, f"{tam} bytes")

c = reloj.compacto({"probabilities": {}, "nube_baja": {"frac": None}})
chk("sin datos: null, no 0", c["p60"] is None and c["nube_baja"] is None
    and c["celda"] is None and c["hora"] is None)

print("\nC. publish() lo escribe junto a latest.json")
tmp = tempfile.mkdtemp()
viejo = config.LATEST_JSON
config.LATEST_JSON = os.path.join(tmp, "latest.json")
viejo_h = config.HISTORY_JSON
config.HISTORY_JSON = os.path.join(tmp, "history.json")
try:
    run.publish(dict(d))
    r = store.load_json(os.path.join(tmp, "reloj.json"), None)
    chk("existe y se lee", r is not None and r.get("v") == 1)
    # El mismo archivo sirve de muestra para las pruebas de Kotlin, que
    # comprueban que la app lo entiende: el contrato probado por los dos lados.
    muestra = "android/common/src/test/resources/reloj.json"
    if os.path.isdir(os.path.dirname(muestra)):
        actual = json.load(open(muestra, encoding="utf-8"))
        chk("la muestra de Kotlin tiene las mismas claves",
            set(actual) == set(r), str(set(actual) ^ set(r)))
finally:
    config.LATEST_JSON, config.HISTORY_JSON = viejo, viejo_h
    shutil.rmtree(tmp, ignore_errors=True)

print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
