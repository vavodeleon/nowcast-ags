"""Trayectorias curvas: tormentas que giran.

Álvaro, 5/10/2026: «cuando las nubes tienen una trayectoria no lineal, por
ejemplo estas estaban girando en forma circular, no se podía calcular ninguna
trayectoria».

Se monta un anillo de seis celdas que gira alrededor de un centro desplazado de
la ciudad, de modo que las celdas le pasan por encima una tras otra. La verdad
-cuándo pasa cada una- se calcula con la misma geometría que las genera, así
que no hay que creerle a nadie.

Una advertencia que costó un error: el ETA es el momento de MÁXIMO
acercamiento, no el de entrar a 20 km. La primera versión de esta comparación
medía lo segundo y declaró «equivocado» un ETA de 40 minutos que era exacto.
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

from nowcast import campo, config, engine
from nowcast.sources import Frame

ok = True


def chk(nombre: str, condicion: bool, detalle: str = "") -> None:
    global ok
    print(f"  {'PASA' if condicion else 'FALLA'}  {nombre}"
          + (f"  [{detalle}]" if detalle else ""))
    if not condicion:
        ok = False


N, KM = 200, 2.44
CIUDAD = (100.0, 100.0)
BASE = datetime(2026, 10, 3, 20, tzinfo=timezone.utc)


class Giro:
    def __init__(self, centro, radio, grados_min, n=6):
        self.centro, self.radio, self.w, self.n = centro, radio, grados_min, n

    def pos(self, k, t):
        a = math.radians(k * 360 / self.n + self.w * t)
        return (self.centro[0] - self.radio * math.sin(a),
                self.centro[1] + self.radio * math.cos(a))

    def campo_bt(self, t):
        yy, xx = np.mgrid[0:N, 0:N]
        bt = np.full((N, N), 290.0)
        for k in range(self.n):
            cy, cx = self.pos(k, t)
            bt -= 75 * np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * 7.0 ** 2))
        return bt.astype(np.float32)

    def cuadros(self):
        return [Frame(time=BASE + timedelta(minutes=15 * i),
                      data=self.campo_bt(15 * i), km_per_px=KM,
                      center_lat=21.84, center_lon=-102.28, kind="ir")
                for i in range(5)]

    def verdad(self, k, t0=60):
        """(minutos, km) del máximo acercamiento de la celda k a la ciudad."""
        return min(((t, math.hypot(self.pos(k, t0 + t)[0] - CIUDAD[0],
                                   self.pos(k, t0 + t)[1] - CIUDAD[1]) * KM)
                    for t in range(0, 270)), key=lambda z: z[1])


def recta(fr, y, x):
    """ETA con el método anterior: movimiento local de la celda, en recta."""
    m = engine.motion_en(fr, y, x)
    if m.bearing_deg is None:
        return None
    sp = math.hypot(m.vy_px_min, m.vx_px_min)
    dy, dx = CIUDAD[0] - y, CIUDAD[1] - x
    along = (dy * m.vy_px_min + dx * m.vx_px_min) / sp
    cross = abs(dy * m.vx_px_min - dx * m.vy_px_min) / sp
    if along > 0 and cross * KM <= 15 + 0.3 * along * KM:
        return along / sp
    return None


print("A. Un giro cerrado: la recta no sirve y el campo sí")
cerrado = Giro((100, 125), 25, 1.2)
fr = cerrado.cuadros()
fld = campo.medir(fr)
aciertos_campo, aciertos_recta, que_pasan = 0, 0, 0
for k in range(6):
    y, x = cerrado.pos(k, 60)
    tv, dv = cerrado.verdad(k)
    if dv >= 15 or tv == 0:
        continue                   # no pasa, o ya está encima ahora
    que_pasan += 1
    r = engine._por_campo(fld, y, x, *CIUDAD, KM, 270, 0.5)
    if r and abs(r[0] - tv) <= max(20, 0.25 * tv):
        aciertos_campo += 1
    re = recta(fr, y, x)
    if re is not None and abs(re - tv) <= max(20, 0.25 * tv):
        aciertos_recta += 1
chk("el campo acierta la mayoría de las celdas que pasan",
    aciertos_campo >= que_pasan - 1, f"{aciertos_campo} de {que_pasan}")
chk("la recta no (por eso hacía falta esto)",
    aciertos_recta <= 1, f"{aciertos_recta} de {que_pasan}")

print("\nB. Reconoce que es un giro")
chk("es_giro lo detecta", campo.es_giro(fld))
m = engine.estimate_motion(fr)
print(f"     (la mediana global diría: {m.from_direction}, "
      f"{m.speed_kmh:.0f} km/h — un promedio que no va a ningún sitio)")

print("\nC. Una traslación recta sigue igual de bien")


def recta_bt(t):
    yy, xx = np.mgrid[0:N, 0:N]
    bt = np.full((N, N), 290.0)
    for cy, cx0 in ((60, 40), (110, 30), (150, 55)):
        cx = cx0 + 0.27 * t
        bt -= 75 * np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * 7.0 ** 2))
    return bt.astype(np.float32)


fr_r = [Frame(time=BASE + timedelta(minutes=15 * i), data=recta_bt(15 * i),
              km_per_px=KM, center_lat=21.84, center_lon=-102.28, kind="ir")
        for i in range(5)]
fld_r = campo.medir(fr_r)
errs = []
for cy, cx0 in ((60, 40), (110, 30), (150, 55)):
    _, yf, xf = campo.trayectoria(fld_r, cy, cx0 + 0.27 * 60, 60)[-1]
    errs.append(math.hypot(yf - cy, xf - (cx0 + 0.27 * 120)) * KM)
chk("error a una hora menor de 3 km", max(errs) < 3, f"{max(errs):.1f} km")
chk("y no lo confunde con un giro", not campo.es_giro(fld_r))

print("\nD. Una tesela loca no tuerce el camino")
orig = campo._rellenar


def con_locas(vy, vx, ys, xs, radio):
    fy, fx = orig(vy, vx, ys, xs, radio)
    r = np.random.default_rng(1)
    for _ in range(6):
        i, j = r.integers(0, fy.shape[0]), r.integers(0, fy.shape[1])
        fy[i, j], fx[i, j] = r.normal(0, 1.5, 2)
    return fy, fx


amplio = Giro((100, 150), 50, 0.6)


def error_medio(g):
    f = campo.medir(g.cuadros())
    e = []
    for k in range(6):
        y, x = g.pos(k, 60)
        _, yf, xf = campo.trayectoria(f, y, x, 60)[-1]
        yr, xr = g.pos(k, 120)
        e.append(math.hypot(yf - yr, xf - xr) * KM)
    return float(np.mean(e))


limpio = error_medio(amplio)
campo._rellenar = con_locas
sucio = error_medio(amplio)
campo._rellenar = orig
chk("con seis teselas basura el error casi no cambia", sucio < limpio + 6,
    f"{limpio:.1f} → {sucio:.1f} km")

print("\nE. El motor publica el camino y el giro")
nc = engine.run_nowcast(cerrado.cuadros())
chk("hay celda", nc.nearest_cell_km is not None)
chk("con camino de varios puntos", len(nc.nearest_cell_trayectoria) >= 3,
    f"{len(nc.nearest_cell_trayectoria)} puntos")
chk("en latitud y longitud", all(len(p) == 2 and 20 < p[0] < 24
                                 and -105 < p[1] < -100
                                 for p in nc.nearest_cell_trayectoria))
chk("y marca el giro", nc.giro is True)

print("\nF. Sin nubes suficientes para un campo, vuelve a la recta")
pocas = [Frame(time=BASE + timedelta(minutes=15 * i), data=recta_bt(15 * i),
               km_per_px=KM, center_lat=21.84, center_lon=-102.28, kind="ir")
         for i in range(5)]
viejo = config.CAMPO_MIN_TESELAS
config.CAMPO_MIN_TESELAS = 999          # fuerza "no hay campo"
nc2 = engine.run_nowcast(pocas)
config.CAMPO_MIN_TESELAS = viejo
chk("sin camino integrado", nc2.nearest_cell_trayectoria == [])
chk("y sin declarar giro", nc2.giro is False)

print("\nG. Rápido incluso en el Pi")
import time  # noqa: E402
t = time.time()
campo.medir(cerrado.cuadros())
dt = time.time() - t
chk("medir el campo cuesta menos de 2 s aquí", dt < 2.0, f"{dt:.2f} s")

print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
