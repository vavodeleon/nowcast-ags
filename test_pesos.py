"""El reparto de pesos entre fuentes, que estaba midiendo lo que no importaba.

Medido el 21 de septiembre de 2026 con 4,525 pares reales:

    fuente        Brier   separacion   peso que recibia
    modelos      0.0843        33.5%              0.44
    infrarrojo   0.1224        18.6%              0.30
    radar        0.1467         0.0%              0.26

El radar tenia separacion **cero** -su probabilidad era constante, o sea
literalmente ninguna informacion- y se llevaba un cuarto del voto. Y la mezcla
completa (Brier 0.0874) salia PEOR que los modelos solos (0.0843): juntar una
fuente buena con una inutil da algo intermedio, no algo mejor.

La causa no era un error de programacion sino de metrica. El Brier de un
pronostico inutil es la tasa base (0.125 aqui) y el del mejor disponible 0.084:
todo el rango entre "no sirve" y "es lo mejor que hay" es un factor de 1.5, asi
que `1/Brier` normalizado reparte casi a partes iguales pase lo que pase.

Esta prueba fija el comportamiento nuevo con casos donde la respuesta correcta
se conoce de antemano.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import numpy as np

from nowcast import calibrate, config, store

ok = True


def chk(nombre: str, condicion: bool, detalle: str = "") -> None:
    global ok
    print(f"  {'PASA' if condicion else 'FALLA'}  {nombre}"
          + (f"  [{detalle}]" if detalle else ""))
    if not condicion:
        ok = False


tmp = tempfile.mkdtemp()
config.PREDICTIONS_CSV = os.path.join(tmp, "pred.csv")
config.OBSERVATIONS_CSV = os.path.join(tmp, "obs.csv")
config.CALIBRATION_JSON = os.path.join(tmp, "cal.json")

print("A. La separación mide lo que dice medir")
y = np.array([1.0, 1.0, 0.0, 0.0])
w = np.ones(4)
chk("una fuente que distingue perfecto separa 1.0",
    abs(calibrate._separacion(np.array([1., 1., 0., 0.]), y, w) - 1.0) < 1e-9)
chk("una constante separa 0.0",
    abs(calibrate._separacion(np.array([.4, .4, .4, .4]), y, w)) < 1e-9)
chk("una fuente al revés separa negativo",
    calibrate._separacion(np.array([0., 0., 1., 1.]), y, w) < 0)
chk("sin casos de lluvia no se inventa nada",
    calibrate._separacion(np.array([.5, .5]), np.array([0., 0.]),
                          np.ones(2)) == 0.0)

print("\nB. Una fuente constante recibe peso CERO, no un cuarto")
# Es el caso del radar: sin cobertura util en Aguascalientes, `p_radar` sale
# 0.0 siempre. Antes se llevaba 0.26 del voto por tener un Brier parecido al
# de la climatologia, que es lo que tiene cualquier cosa que no dice nada.
ahora = datetime.now(timezone.utc)
filas, obs = [], []
rng = np.random.default_rng(7)
for i in range(600):
    llovio = 1 if rng.random() < 0.3 else 0
    emitido = ahora - timedelta(minutes=15 * (i + 1))
    slot = store.round_slot(emitido + timedelta(minutes=60))
    filas.append({
        "issued_utc": emitido.isoformat(), "valid_utc": slot, "lead_min": 60,
        # modelos: separa de verdad
        "p_models": round(0.75 if llovio else 0.12, 4),
        # infrarrojo: separa a medias
        "p_ir": round(0.45 if llovio else 0.30, 4),
        # radar: constante, no dice nada
        "p_radar": 0.0,
        "p_final": round(0.5 if llovio else 0.2, 4),
        "w_radar": 0.26, "w_ir": 0.30, "w_models": 0.44,
    })
    obs.append({"valid_utc": slot, "rained": llovio, "mm": "",
                "peak_score": "", "source": "openmeteo"})
store.append_predictions(filas)
store.append_observations(obs)

cal = calibrate.build_calibration()
pesos = calibrate.weights_for(60, cal)
sep = (cal.get("skill") or {}).get("60", {}).get("separacion", {})
print(f"     separaciones medidas: {sep}")
print(f"     pesos resultantes:    {pesos}")
chk("el radar queda en cero", pesos["radar"] == 0.0, str(pesos["radar"]))
chk("los modelos se llevan la mayoría", pesos["models"] > 0.5,
    str(pesos["models"]))
chk("el infrarrojo conserva una parte", 0.05 < pesos["ir"] < 0.5,
    str(pesos["ir"]))
chk("los pesos suman 1", abs(sum(pesos.values()) - 1.0) < 0.01,
    str(round(sum(pesos.values()), 4)))

print("\nC. La mezcla nueva le gana a la que había")
# La comprobacion que de verdad importa: que el cambio mejore el Brier. Se
# calculan las dos mezclas sobre los mismos casos.
ys = np.array([float(o["rained"]) for o in obs])
unos = np.ones(len(ys))
viejos = {"radar": 0.26, "ir": 0.30, "models": 0.44}
usables = {"radar": True, "ir": True, "models": True}
def mezcla(pesos_):
    return np.array([calibrate._recomponer(
        {"p_radar": 0.0, "p_ir": 0.0, "p_models": 0.0} and
        {"radar": float(f["p_radar"]), "ir": float(f["p_ir"]),
         "models": float(f["p_models"])}, usables, pesos_) for f in filas])
b_viejo = calibrate._brier(mezcla(viejos), ys, unos)
b_nuevo = calibrate._brier(mezcla(pesos), ys, unos)
print(f"     Brier con los pesos viejos: {b_viejo:.4f}")
print(f"     Brier con los pesos nuevos: {b_nuevo:.4f}")
chk("la mezcla nueva es mejor", b_nuevo < b_viejo,
    f"{b_nuevo:.4f} vs {b_viejo:.4f}")

print("\nD. La curva se ajusta a la mezcla de HOY, no a la de ayer")
# Las filas de arriba llevan `p_final` incoherente con los pesos a proposito
# (0.5 / 0.2, que no es lo que sale de mezclar). Si la curva se ajustara al
# `p_final` guardado, sus `x` se agruparian en esos dos valores.
curva = (cal.get("curves") or {}).get("60") or {}
xs = curva.get("x") or []
chk("hay curva", len(xs) >= 3, f"{len(xs)} puntos")
pegados = sum(1 for v in xs if abs(v - 0.5) < 0.01 or abs(v - 0.2) < 0.01)
chk("las x no son los p_final guardados", pegados <= 1, f"{pegados} coinciden")
esperado = calibrate._recomponer({"radar": 0.0, "ir": 0.45, "models": 0.75},
                                 usables, pesos)
chk("sino la mezcla recompuesta",
    any(abs(v - esperado) < 0.02 for v in xs), f"se buscaba ~{esperado:.3f}")

print("\nE. Si ninguna fuente separa nada, se vuelve al prior")
# Dividir entre cero, o repartir por separaciones negativas, daria pesos sin
# sentido. Mejor no aprender nada que aprender un disparate.
#
# Este caso encontro un fallo de verdad: tres fuentes constantes no separan
# exactamente cero, separan -2.8e-17, y el signo de ese polvo decidia entre
# volver al prior y repartir un tercio a cada una. La prueba fallaba a veces.
chk("una separación de polvo no cuenta como señal",
    calibrate.SEPARACION_MINIMA >= 0.005, str(calibrate.SEPARACION_MINIMA))
chk("y da igual el signo del polvo",
    calibrate._separacion(np.array([0.2, 0.2, 0.2, 0.2]), y, w)
    < calibrate.SEPARACION_MINIMA)
config.PREDICTIONS_CSV = os.path.join(tmp, "pred2.csv")
config.OBSERVATIONS_CSV = os.path.join(tmp, "obs2.csv")
filas2, obs2 = [], []
for i in range(500):
    llovio = 1 if rng.random() < 0.3 else 0
    emitido = ahora - timedelta(minutes=15 * (i + 1))
    slot = store.round_slot(emitido + timedelta(minutes=60))
    filas2.append({
        "issued_utc": emitido.isoformat(), "valid_utc": slot, "lead_min": 60,
        "p_models": 0.2, "p_ir": 0.2, "p_radar": 0.2, "p_final": 0.2,
        "w_radar": 0.26, "w_ir": 0.30, "w_models": 0.44,
    })
    obs2.append({"valid_utc": slot, "rained": llovio, "mm": "",
                 "peak_score": "", "source": "openmeteo"})
store.append_predictions(filas2)
store.append_observations(obs2)
cal2 = calibrate.build_calibration()
p2 = calibrate.weights_for(60, cal2)
chk("se usan los pesos por defecto",
    all(abs(p2[s] - config.DEFAULT_SOURCE_WEIGHTS[s]) < 0.01 for s in p2),
    str(p2))

print("\nF. Con pocas muestras no se mueve casi nada")
# El suavizado hacia el prior sigue vigente: 40 casos no son una medicion.
config.PREDICTIONS_CSV = os.path.join(tmp, "pred3.csv")
config.OBSERVATIONS_CSV = os.path.join(tmp, "obs3.csv")
store.append_predictions(filas[:50])
store.append_observations(obs[:50])
cal3 = calibrate.build_calibration()
p3 = calibrate.weights_for(60, cal3)
chk("el radar todavía no cae del todo", p3["radar"] > 0.05, str(p3["radar"]))
chk("pero ya va bajando", p3["radar"] < config.DEFAULT_SOURCE_WEIGHTS["radar"]
    or p3["models"] > config.DEFAULT_SOURCE_WEIGHTS["models"], str(p3))

print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
