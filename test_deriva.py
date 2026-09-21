"""La trayectoria medida con rayos, que es la del núcleo y no la del yunque.

Nace de una observación de Álvaro mirando el cono contra las tormentas reales:
el rumbo apuntaba a otro lado, y su sospecha fue que el motor estaba siguiendo
la parte de arriba de la nube, la que se lleva la cizalladura.

Es un problema conocido del nowcasting por satélite. El infrarrojo mide el
TECHO, a 10-14 km. Ese techo lo arrastra el viento en altura. Los rayos salen
del núcleo convectivo, que es lo que de verdad llueve y lo que hay que seguir.

Aquí se comprueba que el estimador mide lo que dice, con tormentas sintéticas
cuyo rumbo se conoce de antemano.
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta, timezone

from nowcast import lightning

ok = True


def chk(nombre: str, condicion: bool, detalle: str = "") -> None:
    global ok
    print(f"  {'PASA' if condicion else 'FALLA'}  {nombre}"
          + (f"  [{detalle}]" if detalle else ""))
    if not condicion:
        ok = False


LAT0, LON0 = 21.84, -102.28
T0 = datetime(2026, 9, 21, 20, 0, tzinfo=timezone.utc)


def mover(lat, lon, norte_km, este_km):
    return (lat + norte_km / 111.0,
            lon + este_km / (111.0 * math.cos(math.radians(lat))))


def tormenta(rumbo_deg, kmh, bloques=4, destellos=30, lat=LAT0, lon=LON0):
    """Una celda que viaja hacia `rumbo_deg` a `kmh`, en bloques de 15 min."""
    rad = math.radians(rumbo_deg)
    paso = kmh / 4.0          # km por bloque de 15 min
    salida = []
    for i in range(bloques):
        clat, clon = mover(lat, lon, paso * i * math.cos(rad),
                           paso * i * math.sin(rad))
        # Unas pocas descargas repartidas alrededor del centro, como en la
        # realidad: el centroide es lo que importa, no un punto exacto.
        puntos = []
        for k in range(5):
            d = (k - 2) * 1.5
            p = mover(clat, clon, d, -d)
            puntos.append([round(p[0], 4), round(p[1], 4), destellos // 5])
        salida.append({"t": (T0 + timedelta(minutes=15 * i)).isoformat(),
                       "puntos": puntos, "total": destellos})
    return {"bloques": salida}


print("A. Una celda que va al este se mide yendo al este")
d = lightning.deriva(tormenta(90, 40))
chk("hay rumbo", d.bearing_deg is not None)
chk("apunta al este", d.bearing_deg is not None
    and abs(((d.bearing_deg - 90 + 180) % 360) - 180) < 12,
    f"{d.bearing_deg:.0f}°" if d.bearing_deg else "—")
chk("con la velocidad correcta", abs(d.speed_kmh - 40) < 6,
    f"{d.speed_kmh:.0f} km/h")
chk("y viene del oeste", d.from_direction == "oeste", d.from_direction)

print("\nB. Y una que va al noroeste, también")
d = lightning.deriva(tormenta(315, 25))
chk("apunta al noroeste", d.bearing_deg is not None
    and abs(((d.bearing_deg - 315 + 180) % 360) - 180) < 15,
    f"{d.bearing_deg:.0f}°" if d.bearing_deg else "—")
chk("viene del sureste", d.from_direction == "sureste", d.from_direction)

print("\nC. Dos tormentas a la vez no producen un rumbo promedio inventado")
# El fallo obvio de un centroide global: con una celda al norte y otra al sur,
# el centro de masa cae entre las dos y se mueve según cuál descargue más.
# Eso no es el movimiento de ninguna de las dos.
a = tormenta(90, 40)["bloques"]
lejos_lat, lejos_lon = mover(LAT0, LON0, 160, 0)
b = tormenta(270, 40, lat=lejos_lat, lon=lejos_lon)["bloques"]
mezcla = {"bloques": [
    {"t": x["t"], "puntos": x["puntos"] + y["puntos"],
     "total": x["total"] + y["total"]} for x, y in zip(a, b)]}
d = lightning.deriva(mezcla)
chk("sigue una sola y no inventa el promedio",
    d.bearing_deg is not None
    and (abs(((d.bearing_deg - 90 + 180) % 360) - 180) < 20
         or abs(((d.bearing_deg - 270 + 180) % 360) - 180) < 20),
    f"{d.bearing_deg:.0f}°" if d.bearing_deg else "—")
chk("con velocidad de celda, no de salto entre tormentas",
    d.speed_kmh < 70, f"{d.speed_kmh:.0f} km/h")

print("\nD. Con pocas descargas no se pronuncia")
d = lightning.deriva(tormenta(90, 40, destellos=5))
chk("la confianza es baja", d.confianza < 0.3, f"{d.confianza:.2f}")
d = lightning.deriva(tormenta(90, 40, bloques=2, destellos=100))
chk("un solo salto tampoco convence", d.confianza < 0.5, f"{d.confianza:.2f}")
d = lightning.deriva(tormenta(90, 40, bloques=4, destellos=80))
chk("cuatro bloques coherentes sí", d.confianza > 0.5, f"{d.confianza:.2f}")

print("\nE. Sin rayos no se inventa nada")
chk("sin bloques", lightning.deriva({"bloques": []}).bearing_deg is None)
chk("con bloques vacíos",
    lightning.deriva({"bloques": [{"t": T0.isoformat(), "puntos": [],
                                   "total": 0}]}).bearing_deg is None)
chk("con un solo bloque",
    lightning.deriva(tormenta(90, 40, bloques=1)).bearing_deg is None)

print("\nF. Una celda quieta no tiene rumbo, y eso no es un error")
d = lightning.deriva(tormenta(90, 0.0))
chk("sin rumbo", d.bearing_deg is None, str(d.bearing_deg))
chk("velocidad ~0", d.speed_kmh < 3, f"{d.speed_kmh:.1f}")

print("\nG. Un salto imposible se descarta en vez de publicarse")
# Si el seguimiento salta de una tormenta a otra lejana, la velocidad sale
# absurda. Publicar 300 km/h sería peor que no publicar nada: el cono
# apuntaría con enorme confianza a un sitio inventado.
lejos = mover(LAT0, LON0, 300, 0)
saltada = {"bloques": [
    {"t": T0.isoformat(), "puntos": [[LAT0, LON0, 50]], "total": 50},
    {"t": (T0 + timedelta(minutes=15)).isoformat(),
     "puntos": [[lejos[0], lejos[1], 50]], "total": 50},
]}
d = lightning.deriva(saltada)
chk("no se publica un rumbo", d.bearing_deg is None, str(d.bearing_deg))

print("\nH. La cizalladura, simulada: techo y núcleo discrepan")
# El caso que motivó todo esto. El núcleo va al noreste despacio; el yunque
# se estira hacia el este rápido. Si se midiera el techo saldría 'del oeste'
# y la celda en realidad viene del suroeste.
nucleo = lightning.deriva(tormenta(45, 20))
chk("los rayos dan el rumbo del núcleo",
    nucleo.from_direction == "suroeste", nucleo.from_direction)
chk("que no es el del yunque", nucleo.from_direction != "oeste")
print(f"\n     núcleo por rayos: viene del {nucleo.from_direction}, "
      f"{nucleo.speed_kmh:.0f} km/h")

print("\nI. El satélite calla cuando no alcanza a medir")
# El hallazgo del 21 de septiembre de 2026. GOES da un cuadro cada 15 min con
# 2.44 km/px: una celda a 7 km/h recorre 0.7 px entre cuadros y la correlacion
# de fase no puede dar direccion. El umbral viejo era 2 km/h -0.2 px- asi que
# el rumbo salia igual, inventado, con cara de dato.
import numpy as np
from nowcast import config, engine
from nowcast.sources import Frame

N = 160
KM_PX = 2.44


def campo(cy, cx):
    yy, xx = np.mgrid[0:N, 0:N]
    bt = np.full((N, N), 290.0)
    bt -= 70 * np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * 9.0 ** 2))
    return bt.astype(np.float32)


def cuadros(desplaz_px_por_cuadro):
    base = datetime(2026, 9, 21, 20, 0, tzinfo=timezone.utc)
    return [Frame(time=base + timedelta(minutes=15 * i),
                  data=campo(80, 60 + desplaz_px_por_cuadro * i),
                  km_per_px=KM_PX, center_lat=LAT0, center_lon=LON0, kind="ir")
            for i in range(4)]


lento = engine.estimate_motion(cuadros(0.4))
chk("con 0.4 px por cuadro no hay rumbo", lento.bearing_deg is None,
    str(lento.bearing_deg))
chk("y se dice con palabras",
    lento.from_direction == "sin movimiento definido", lento.from_direction)
rapido = engine.estimate_motion(cuadros(4.0))
chk("con 4 px sí lo hay", rapido.bearing_deg is not None)
chk("y apunta al este", rapido.bearing_deg is not None
    and abs(((rapido.bearing_deg - 90 + 180) % 360) - 180) < 20,
    f"{rapido.bearing_deg:.0f}°" if rapido.bearing_deg else "—")
# El umbral se movio de 2.0 a 1.0 el 22/09/2026 cuando la persistencia medida
# dijo que el rumbo del infrarrojo cambia 8 grados entre cuadros: eso no es
# ruido y 2 px estaba tirando informacion buena. Lo que la prueba fija no es el
# valor -es provisional y saldra de la seccion 2-ter de medir_deriva.py- sino
# que exista, que se exprese en pixeles y que cierre el medio pixel.
chk("el umbral existe y se expresa en píxeles",
    0.75 <= config.MOTION_MIN_PX <= 3.0, str(config.MOTION_MIN_PX))
chk("y cierra la zona de medio píxel", config.MOTION_MIN_PX > 0.5)

print("\nJ. Sin rumbo no se señala ninguna celda")
# Elegir una celda sin saber hacia dónde va es inventar justo la parte que
# importa: cuál de todas viene y cuándo. Antes se elegía igual.
nc = engine.run_nowcast(cuadros(0.4))
chk("no hay celda que venga", nc is not None and nc.nearest_cell_km is None,
    str(nc.nearest_cell_km) if nc else "sin nowcast")
chk("ni cono", nc is not None and nc.nearest_cell_radio_km is None)
nc2 = engine.run_nowcast(cuadros(4.0))
chk("con movimiento medible sí se evalúan celdas",
    nc2 is not None and nc2.motion.bearing_deg is not None)

print("\nK. Un rumbo que salta no se usa aunque venga con mil rayos")
# La otra mitad del hallazgo. En los datos reales habia casos con 1,400
# descargas -confianza maxima- cuyo rumbo cambiaba 90 grados entre cuadros
# consecutivos. Confianza alta y ruido puro no son incompatibles: la confianza
# mide si ESTA estimacion se sostiene sola, no si coincide con la anterior.
import os
import tempfile
from nowcast import store as _store
config.STATE_JSON = os.path.join(tempfile.mkdtemp(), "estado.json")

firme = lightning.deriva(tormenta(90, 40, destellos=200))
chk("la primera vez no hay con qué comparar",
    lightning.deriva_persistente(firme) is False)
chk("la segunda, con el mismo rumbo, sí",
    lightning.deriva_persistente(firme) is True)
girada = lightning.deriva(tormenta(200, 40, destellos=200))
chk("un giro de 110° se rechaza",
    lightning.deriva_persistente(girada) is False)
chk("y deja su propio rumbo como referencia para la siguiente",
    lightning.deriva_persistente(girada) is True)
chk("aunque la confianza sea alta", girada.confianza > 0.5,
    f"{girada.confianza:.2f}")

print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
