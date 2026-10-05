"""Nubes bajas: que se vean, que se midan y que NO se inventen.

Álvaro, 5/10/2026: «ya van varias veces que está lloviendo pero no se ve
ninguna nube en la app, pero eran nubes bajas».
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import numpy as np

from nowcast import config, nubes_bajas as nb, render, store
import evaluar

ok = True


def chk(nombre, cond, detalle=""):
    global ok
    print(f"  {'PASA' if cond else 'FALLA'}  {nombre}" + (f"  [{detalle}]" if detalle else ""))
    if not cond:
        ok = False


class F:
    def __init__(self, data, hora_utc):
        self.data = data.astype(np.float32)
        self.km_per_px = 2.44
        self.time = datetime(2026, 10, 5, hora_utc, tzinfo=timezone.utc)


def escena(suelo, nube, hora, radio_px=20, centro=(100, 100)):
    yy, xx = np.mgrid[0:200, 0:200]
    bt = np.full((200, 200), float(suelo))
    bt[(yy - centro[0]) ** 2 + (xx - centro[1]) ** 2 <= radio_px ** 2] = nube
    return F(bt, hora)


print("A. Nube baja de día sobre la ciudad")
s = nb.medir(escena(305, 285, 18))           # 12:00 local
chk("la ve encima", s["frac"] is not None and s["frac"] > 0.9, str(s))
chk("contraste ~20 K", s["contraste_k"] is not None and 18 <= s["contraste_k"] <= 22)
chk("de día", s["de_dia"] is True)

print("\nB. La misma nube lejos de la ciudad no cuenta")
s = nb.medir(escena(305, 285, 18, centro=(30, 30)))
chk("frac 0 sobre la ciudad", s["frac"] == 0.0, str(s))

print("\nC. De noche, con el suelo frío, el contraste muere")
s = nb.medir(escena(288, 284, 9))            # 03:00 local
chk("no la inventa", s["frac"] == 0.0, str(s))
chk("y marca noche", s["de_dia"] is False)

print("\nD. Todo cubierto: no hay suelo con qué comparar")
s = nb.medir(F(np.full((200, 200), 270.0), 18))
chk("frac null, no 0", s["frac"] is None, str(s))

print("\nE. Una tormenta fría no es nube baja")
s = nb.medir(escena(305, 215, 18))
chk("frac 0", s["frac"] == 0.0, str(s))

print("\nF. El mapa la pinta en gris, y no toca lo demás")
bt = np.array([[285.0, 304.0, 215.0, 245.0]])
sin = render.colorize(bt)
con = render.colorize(bt, ref=305.0)
chk("nube baja visible", con[0, 0, 3] > 0 and sin[0, 0, 3] == 0,
    f"alfa {con[0, 0, 3]}")
chk("gris, no color de tormenta", abs(int(con[0, 0, 0]) - int(con[0, 0, 2])) < 30)
chk("suelo sigue transparente", con[0, 1, 3] == 0)
chk("tormenta idéntica", (con[0, 2] == sin[0, 2]).all() and (con[0, 3] == sin[0, 3]).all())
chk("tope bajo de alfa: nunca compite", con[0, 0, 3] <= 110)

print("\nG. Las columnas")
chk("en la cabecera", all(c in store.PRED_FIELDS for c in ("nb_frac", "nb_contraste", "nb_dia")))
f = nb.filas({"frac": None, "contraste_k": None, "de_dia": None})
chk("vacías si no se sabe", f == {"nb_frac": "", "nb_contraste": "", "nb_dia": ""})

print("\nH. La sección 9 encuentra una señal real y no inventa una falsa")
rng = np.random.default_rng(3)


def datos(con_señal):
    filas = []
    for dia in range(12):
        for k in range(40):
            nube = rng.random() < 0.4
            p_llover = (0.5 if nube else 0.05) if con_señal else 0.15
            filas.append((0.05, int(rng.random() < p_llover),
                          {"nb_frac": 0.8 if nube else 0.0, "nb_dia": 1,
                           "valid_utc": f"2026-10-{dia + 1:02d}T18:{k:02d}"}))
    return {15: filas}


r = evaluar.nubes_bajas_vs_lluvia(datos(True))["día"]
chk("señal real: diferencia grande", r["dif"] is not None and r["dif"] > 0.3, f"{r['dif']:.2f}")
chk("y p pequeño", r["p"] < 0.01, f"{r['p']:.3f}")
r = evaluar.nubes_bajas_vs_lluvia(datos(False))["día"]
chk("sin señal: p grande", r["p"] is not None and r["p"] > 0.05, f"{r['p']:.3f}")
chk("ignora casos donde ya decía que sí",
    evaluar.nubes_bajas_vs_lluvia({15: [(0.9, 1, {"nb_frac": 1, "nb_dia": 1,
                                                  "valid_utc": "2026-10-01"})]})["día"]["n"] == 0)

print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
