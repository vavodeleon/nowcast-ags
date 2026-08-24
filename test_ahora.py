"""Distinguir el yunque de una tormenta lejana de una celda que si llueve.

Este archivo existe por un falso positivo real: el sistema decia "esta
lloviendo" cuando solo estaba nublado. El infrarrojo no puede distinguir por
temperatura una celda activa del yunque de una tormenta a 100 km -los dos
estan igual de frios- pero si por la FORMA del campo.
"""
import sys
from datetime import datetime, timezone
import numpy as np
sys.path.insert(0, ".")
ok = True
def chk(n, c, d=""):
    global ok
    print(f"  {'PASA' if c else 'FALLA'}  {n}" + (f"  [{d}]" if d else ""))
    if not c: ok = False

from nowcast import overhead, config
from nowcast.sources import Frame

KM = 2.45
N = 200

def campo(fn):
    yy, xx = np.mgrid[0:N, 0:N].astype(float)
    d_km = np.hypot(yy - N/2, xx - N/2) * KM
    return Frame(time=datetime.now(timezone.utc), data=fn(d_km).astype(np.float32),
                 km_per_px=KM, center_lat=config.LAT, center_lon=config.LON, kind="ir")

print("A. Yunque: frio, amplio y liso (NO llueve)")
# 222 K uniforme en 200 km: igual de frio que una tormenta, pero sin estructura
yunque = campo(lambda d: np.where(d < 200, 222.0, 265.0))
a = overhead.assess(yunque, [], None)
print(f"     BT {a.bt_encima:.0f} K · contraste {a.contraste:.1f} K · frio alrededor {a.fraccion_fria:.0%}")
chk("lo reconoce como yunque", a.forma == "yunque", a.forma)
chk("NO dice que llueve", a.lloviendo is False)
chk("lo explica en palabras", "no lloviendo" in a.estado.lower() or "lejana" in a.estado.lower(), a.estado)

print("\nB. Nucleo convectivo: frio, compacto y con contraste (SI llueve)")
nucleo = campo(lambda d: np.clip(205 + d * 0.75, 205, 272))
a = overhead.assess(nucleo, [], None)
print(f"     BT {a.bt_encima:.0f} K · contraste {a.contraste:.1f} K · frio alrededor {a.fraccion_fria:.0%}")
chk("lo reconoce como nucleo", a.forma == "nucleo", a.forma)
chk("SI dice que llueve", a.lloviendo is True)
chk("dice por que", "núcleo" in a.corroborado_por or "nucleo" in a.corroborado_por,
    a.corroborado_por)

print("\nC. Cielo despejado")
a = overhead.assess(campo(lambda d: np.full_like(d, 288.0)), [], None)
chk("no inventa nubes", a.forma == "despejado", a.forma)
chk("no dice que llueve", a.lloviendo is False)
chk("lo dice claro", a.estado == "Despejado", a.estado)

print("\nD. Nube media, ni frio ni despejado")
a = overhead.assess(campo(lambda d: np.full_like(d, 250.0)), [], None)
chk("nublado sin alarma", a.forma == "nubes" and not a.lloviendo, f"{a.forma}/{a.lloviendo}")

print("\nE. Corroboracion independiente")
# el mismo yunque, pero con rayos justo encima -> ahora si es tormenta
bloques = [{"edad_min": 3, "puntos": [[config.LAT + 0.05, config.LON, 12]]}]
a = overhead.assess(yunque, bloques, None)
chk("rayos cerca confirman lluvia", a.lloviendo is True)
chk("cuenta los rayos", a.rayos_cerca == 12, str(a.rayos_cerca))
chk("cita la corroboracion", "rayos" in a.corroborado_por, a.corroborado_por)

# rayos lejanos NO deben confirmar nada
lejos = [{"edad_min": 3, "puntos": [[config.LAT + 3.0, config.LON + 3.0, 99]]}]
a = overhead.assess(yunque, lejos, None)
chk("rayos lejanos no cuentan", a.rayos_cerca == 0 and not a.lloviendo,
    f"{a.rayos_cerca} rayos")

# rayos viejos tampoco
viejos = [{"edad_min": 55, "puntos": [[config.LAT, config.LON, 99]]}]
chk("rayos de hace una hora no cuentan",
    overhead.assess(yunque, viejos, None).rayos_cerca == 0)

# precipitacion observada si confirma
a = overhead.assess(yunque, [], 1.4)
chk("precipitacion observada confirma", a.lloviendo is True)
chk("la cita", "precipitación observada" in a.corroborado_por, a.corroborado_por)
chk("una traza no confirma", overhead.assess(yunque, [], 0.05).lloviendo is False)

print("\nF. Sin satelite")
a = overhead.assess(None, [], 2.0)
chk("solo con lluvia observada basta", a.lloviendo is True)
chk("sin nada, no afirma nada", overhead.assess(None, [], None).lloviendo is False)

print("\nG. Lectura de tus correcciones")
from nowcast import feedback
casos = [
    ("lluvia: si @ 2026-08-18T14:30:00Z", 1),
    ("lluvia: no @ 2026-08-18T14:30:00Z", 0),
    ("lluvia: sí @ 2026-08-18T14:30:00Z", 1),
    ("LLUVIA: NO @ 2026-08-18T14:30:00Z\n\nEl sistema decía: Está lloviendo", 0),
]
for texto, esperado in casos:
    m = feedback._CUERPO_RE.search(texto)
    got = None if not m else (0 if m.group(1).lower() == "no" else 1)
    chk(f"lee «{texto.splitlines()[0][:34]}»", got == esperado, str(got))
chk("ignora texto sin formato", feedback._CUERPO_RE.search("hola que tal") is None)

print("\nH. Lo manual manda sobre lo automatico")
from nowcast import verify, store
import inspect
src = inspect.getsource(verify.run)
chk("verify respeta las observaciones manuales", "manual" in src)

print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
