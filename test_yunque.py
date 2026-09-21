"""El yunque, que es la mentira favorita del infrarrojo.

El IR mide la temperatura del TECHO de la nube. Una nube con el tope a -45 °C
puede ser una celda que descarga, o el yunque de una tormenta que esta a 100 km
extendido por el viento en altura. Igual de frios los dos. El yunque cubre
muchisimo mas territorio y **no moja**.

`overhead.py` ya resolvia esto para el presente con el contraste espacial. El
PRONOSTICO no: extrapolaba brillo, y el brillo de un yunque es de tormenta.

Lo que lo puso en numeros fue la retroalimentacion humana del 21 de septiembre
de 2026. En los 36 casos que Alvaro corrigio a mano, el infrarrojo tenia
separacion **-19.3%**: decia MAS probabilidad cuando NO llovia. No es que no
distinguiera, es que estaba al reves. Y encaja con cuando se usa el comando de
radio: "dice que llueve y esta seco".

Las dos señales que se añaden, y por que cada una:

- **Compacidad.** Nucleo frio con entorno templado a 60-90 km, contra manto
  liso. Es la misma idea de `overhead.py`, aplicada a la parcela que VA a
  llegar en vez de a la que ya esta encima.

- **Tendencia lagrangiana.** La parcela comparada con si misma un cuadro antes,
  siguiendo el movimiento. Una celda que crece enfria su tope varios grados
  cada quince minutos; un yunque ya frio no cambia. Seguir el movimiento y no
  el pixel es lo que hace que la cuenta signifique algo: mirar el mismo pixel
  mide el paso de la nube por encima, que es adveccion y el motor ya la modela
  aparte.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import numpy as np

from nowcast import config, engine
from nowcast.sources import Frame

ok = True


def chk(nombre: str, condicion: bool, detalle: str = "") -> None:
    global ok
    print(f"  {'PASA' if condicion else 'FALLA'}  {nombre}"
          + (f"  [{detalle}]" if detalle else ""))
    if not condicion:
        ok = False


N = 200
KM_PX = 2.45
CENTRO = N // 2
ahora = datetime.now(timezone.utc)


def cuadro(bt: np.ndarray, hace_min: float) -> Frame:
    return Frame(time=ahora - timedelta(minutes=hace_min),
                 data=bt.astype(np.float32), km_per_px=KM_PX,
                 center_lat=config.LAT, center_lon=config.LON, kind="ir")


def nucleo(cy: float, cx: float, sigma_px: float, profundidad: float,
           fondo: float = 290.0) -> np.ndarray:
    """Un campo de temperatura de brillo con un solo nucleo gaussiano."""
    yy, xx = np.mgrid[0:N, 0:N]
    bt = np.full((N, N), fondo)
    bt -= profundidad * np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2)
                               / (2 * sigma_px ** 2))
    return bt


print("A. Una celda compacta se reconoce como núcleo")
# 8 px de sigma son ~20 km: compacto para el pixel de GOES (2.44 km).
sig = engine.to_signal(cuadro(nucleo(CENTRO, CENTRO, 8.0, 80.0), 0))
c_nucleo = engine._compacidad(sig, CENTRO, CENTRO, KM_PX)
chk("compacidad alta", c_nucleo > 0.8, f"{c_nucleo:.2f}")

print("\nB. Un yunque extendido NO")
# Mismo frio en el centro, pero sigma de 60 px (~150 km): el anillo de
# comparacion a 60-95 km esta igual de frio que el centro.
sig_y = engine.to_signal(cuadro(nucleo(CENTRO, CENTRO, 60.0, 80.0), 0))
c_yunque = engine._compacidad(sig_y, CENTRO, CENTRO, KM_PX)
chk("compacidad baja", c_yunque < 0.35, f"{c_yunque:.2f}")
print(f"     núcleo {c_nucleo:.2f}  vs  yunque {c_yunque:.2f}")
chk("y el centro de los dos es igual de frío",
    abs(float(sig[CENTRO, CENTRO]) - float(sig_y[CENTRO, CENTRO])) < 0.01,
    "el brillo solo no los distingue")

print("\nC. La tendencia distingue crecer de estar ya hecho")
# Sin movimiento, para aislar la tendencia de la adveccion.
quieto = engine.Motion()
creciendo = [cuadro(nucleo(CENTRO, CENTRO, 8.0, 45.0), 15),
             cuadro(nucleo(CENTRO, CENTRO, 8.0, 80.0), 0)]
estable = [cuadro(nucleo(CENTRO, CENTRO, 8.0, 80.0), 15),
           cuadro(nucleo(CENTRO, CENTRO, 8.0, 80.0), 0)]
muriendo = [cuadro(nucleo(CENTRO, CENTRO, 8.0, 80.0), 15),
            cuadro(nucleo(CENTRO, CENTRO, 8.0, 45.0), 0)]
t_crece = engine._tendencia_lagrangiana(creciendo, CENTRO, CENTRO, 6.0,
                                        quieto, 0)
t_estable = engine._tendencia_lagrangiana(estable, CENTRO, CENTRO, 6.0,
                                          quieto, 0)
t_muere = engine._tendencia_lagrangiana(muriendo, CENTRO, CENTRO, 6.0,
                                        quieto, 0)
print(f"     crece {t_crece:+.3f}   estable {t_estable:+.3f}   "
      f"muere {t_muere:+.3f}")
chk("creciendo da positivo", t_crece > 0.05, f"{t_crece:+.3f}")
chk("estable da ~0", abs(t_estable) < 0.02, f"{t_estable:+.3f}")
chk("disipandose da negativo", t_muere < -0.05, f"{t_muere:+.3f}")

print("\nD. Sigue la PARCELA, no el píxel")
# Un nucleo identico que solo se desplaza no esta creciendo. Si la cuenta
# mirara el mismo pixel, el nucleo entrando en el disco daria una tendencia
# positiva enorme y el sistema confundiria adveccion con crecimiento -que es
# justo lo que el motor ya modela por separado, asi que se contaria dos veces.
desplazamiento_px = 10.0
mov = engine.Motion()
mov.vy_px_min = 0.0
mov.vx_px_min = desplazamiento_px / 15.0     # 10 px en 15 min
trasladado = [cuadro(nucleo(CENTRO, CENTRO - desplazamiento_px, 8.0, 80.0), 15),
              cuadro(nucleo(CENTRO, CENTRO, 8.0, 80.0), 0)]
t_mov = engine._tendencia_lagrangiana(trasladado, CENTRO, CENTRO, 6.0, mov, 0)
chk("un núcleo que solo se mueve no 'crece'", abs(t_mov) < 0.05,
    f"{t_mov:+.3f}")
# Y la comprobacion de que la prueba prueba algo: mirando el pixel quieto, el
# mismo caso daria una tendencia grande.
t_ingenuo = engine._tendencia_lagrangiana(trasladado, CENTRO, CENTRO, 6.0,
                                          quieto, 0)
chk("mirando el píxel quieto sí se confundiría", t_ingenuo > 0.2,
    f"{t_ingenuo:+.3f}")

print("\nE. El pronóstico completo: yunque castigado, celda respetada")
nc_nucleo = engine.run_nowcast(
    [cuadro(nucleo(CENTRO, CENTRO, 8.0, 45.0), 15),
     cuadro(nucleo(CENTRO, CENTRO, 8.0, 80.0), 0)])
nc_yunque = engine.run_nowcast(
    [cuadro(nucleo(CENTRO, CENTRO, 60.0, 80.0), 15),
     cuadro(nucleo(CENTRO, CENTRO, 60.0, 80.0), 0)])
crudo_n = nc_nucleo.score_crudo_at(15)
ajus_n = nc_nucleo.score_at(15)
crudo_y = nc_yunque.score_crudo_at(15)
ajus_y = nc_yunque.score_at(15)
print(f"     celda:  crudo {crudo_n:.3f} -> ajustado {ajus_n:.3f}")
print(f"     yunque: crudo {crudo_y:.3f} -> ajustado {ajus_y:.3f}")
chk("el brillo a secas los ve casi igual", abs(crudo_n - crudo_y) < 0.15,
    f"{abs(crudo_n - crudo_y):.3f} de diferencia")
chk("el ajustado los separa", ajus_n - ajus_y > 0.2,
    f"{ajus_n - ajus_y:.3f} de diferencia")
chk("y el yunque baja respecto a su crudo", ajus_y < crudo_y,
    f"{ajus_y:.3f} < {crudo_y:.3f}")

print("\nF. El radar no se toca")
# El radar mide lluvia directamente y no tiene el problema del yunque.
# Aplicarle esto seria castigarlo por un defecto ajeno.
lluvia = [Frame(time=ahora - timedelta(minutes=15),
                data=(nucleo(CENTRO, CENTRO, 8.0, -40.0, fondo=0.0)
                      ).astype(np.float32),
                km_per_px=KM_PX, center_lat=config.LAT, center_lon=config.LON,
                kind="radar"),
          Frame(time=ahora,
                data=(nucleo(CENTRO, CENTRO, 8.0, -40.0, fondo=0.0)
                      ).astype(np.float32),
                km_per_px=KM_PX, center_lat=config.LAT, center_lon=config.LON,
                kind="radar")]
nc_r = engine.run_nowcast(lluvia)
chk("score crudo y ajustado coinciden",
    abs(nc_r.score_at(15) - nc_r.score_crudo_at(15)) < 1e-9,
    f"{nc_r.score_at(15):.4f} vs {nc_r.score_crudo_at(15):.4f}")
chk("compacidad neutra", nc_r.detalle_at(15)[1] == 1.0)

print("\nG. Sin segundo cuadro no se inventa una tendencia")
chk("un solo cuadro da 0.0",
    engine._tendencia_lagrangiana([cuadro(nucleo(CENTRO, CENTRO, 8.0, 80.0), 0)],
                                  CENTRO, CENTRO, 6.0, quieto, 0) == 0.0)
print("\nH. Fuera de la imagen tampoco")
# Si la parcela de hace un cuadro cae fuera del campo, un 0 de senal no es
# "no habia nube", es "no alcanzo a ver". Devolver 0.0 de tendencia deja el
# score como estaba, que es lo honesto.
chk("una parcela fuera del campo da 0.0",
    engine._tendencia_lagrangiana(creciendo, -500.0, -500.0, 6.0, quieto, 0)
    == 0.0)

print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
