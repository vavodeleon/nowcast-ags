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
# Desde el 12/10/2026 la 9 pregunta si la nube baja mejora el pronóstico,
# calificando en días que el ajuste no vio. Caso clave, el que la versión
# anterior no veía: días ENTEROS bajo nube baja que llueven más. Comparar solo
# dentro del día los descartaba.
import contextlib, io  # noqa: E401,E402
from datetime import datetime as _dt, timedelta as _td, timezone as _tz  # noqa: E402


def datos(con_señal, semilla):
    rng = np.random.default_rng(semilla)
    filas = []
    for dia in range(30):
        nublado = rng.random() < 0.5            # el día entero, o casi
        for k in range(40):
            t = _dt(2026, 9, 1, 15, tzinfo=_tz.utc) + _td(days=dia, minutes=10 * k)
            frac = (0.8 if nublado else 0.05) + rng.normal(0, 0.05)
            pf = 0.08
            p = (0.30 if nublado else 0.02) if con_señal else pf
            filas.append((pf, int(rng.random() < p),
                          {"nb_frac": frac, "nb_dia": 1,
                           "issued_utc": t.isoformat()}))
    return {15: filas, 60: filas}


def salida(d):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        evaluar.nubes_bajas_seccion(d)
    return buf.getvalue()


txt = salida(datos(True, 1))
chk("con señal de días enteros: GANA", "GANA" in txt, txt.split("\n")[6])
falsos = sum("GANA" in salida(datos(False, 50 + k)) for k in range(6))
chk("sin señal no inventa una (≤1 de 6 semillas)", falsos <= 1, f"{falsos} de 6")
chk("con pocos días no decide", "Aún no hay" in salida({15: datos(True, 2)[15][:80]}))

print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
