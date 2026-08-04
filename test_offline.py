"""Prueba de degradacion y del ciclo de aprendizaje, sin tocar la red.

Simula que todas las fuentes estan caidas (el sistema no debe inventar
probabilidades) y luego alimenta un historial sintetico donde el radar
es bueno y los modelos son malos, para comprobar que la calibracion lo
aprende sola.

    python test_offline.py

OJO: escribe en data/. Correr sobre una copia si ya tienes historial real.
"""
import logging, sys
logging.basicConfig(level=logging.ERROR)

from nowcast import http
http.get_bytes = lambda *a, **k: None
http.get_json  = lambda *a, **k: None
http.post      = lambda *a, **k: False

from nowcast import run, verify, calibrate, notify, daily, store

ok = True
def chk(name, fn):
    global ok
    try:
        r = fn()
        print(f"  PASA  {name}")
        return r
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"  FALLA {name}: {e}")
        ok = False
        return None

print("Todas las fuentes caidas:")
chk("verify.run()", verify.run)
chk("calibrate.refresh()", calibrate.refresh)
res = chk("build_forecast()", run.build_forecast)
if res:
    print("     prob 60min:", res["probabilities"].get("60"),
          "| confianza:", res["confidence"],
          "| fuentes:", res["sources"])
    assert all(v is None for v in res["probabilities"].values()), "sin datos debe ser None, no 0"
    print("  PASA  reporta desconocido (None) en vez de 0% sin datos")
    chk("publish()", lambda: run.publish(dict(res, _rows=[])))
    chk("maybe_alert() no dispara", lambda: notify.maybe_alert(res) is False)
chk("daily.build_report()", daily.build_report)

# ahora con un historial sintetico para probar el ciclo de aprendizaje completo
print("\nCiclo de aprendizaje con historial sintetico:")
import random, numpy as np
from datetime import datetime, timedelta, timezone
random.seed(3); np.random.seed(3)
t0 = datetime.now(timezone.utc) - timedelta(days=20)
preds, obs = [], []
for i in range(1500):
    t = t0 + timedelta(minutes=15*i)
    valid = store.round_slot(t + timedelta(minutes=60))
    # radar bueno, modelos malos: el sistema deberia aprenderlo
    truth = 1 if np.random.rand() < 0.25 else 0
    p_radar = np.clip(truth*0.7 + np.random.rand()*0.3, 0, 1)
    p_models = np.clip(0.4 + np.random.rand()*0.4, 0, 1)
    p_ir = np.clip(truth*0.45 + np.random.rand()*0.5, 0, 1)
    p_final = 0.45*p_radar + 0.35*p_ir + 0.20*p_models
    preds.append(dict(issued_utc=t.isoformat(), valid_utc=valid, lead_min=60,
        p_final=round(p_final,4), p_radar=round(p_radar,4), p_ir=round(p_ir,4),
        p_models=round(p_models,4), w_radar=.45, w_ir=.35, w_models=.20,
        score_radar="", score_ir="", motion_speed_kmh=30, motion_from="oeste",
        motion_conf=.8, growth=1.0, cell_eta_min="", cell_km="",
        cell_intensity="", cape=1200, radar_coverage=.6))
    obs.append(dict(valid_utc=valid, rained=truth, mm="", peak_score="", source="test"))
store.append_predictions(preds)
store.append_observations(obs)

cal = calibrate.refresh()
w = cal["weights"]["60"]; sk = cal["skill"]["60"]
print(f"     pesos aprendidos: radar {w['radar']:.2f}  IR {w['ir']:.2f}  modelos {w['models']:.2f}")
print(f"     Brier por fuente: {sk['brier']}")
print(f"     Brier combinado {sk['brier_final']} vs climatologia {sk['brier_climatology']} -> skill {sk.get('skill_score')}")
if w["radar"] > w["models"]: print("  PASA  aprendio a confiar mas en el radar que en los modelos")
else: print("  FALLA no aprendio los pesos"); ok = False
if sk["brier"]["radar"] < sk["brier"]["models"]: print("  PASA  midio bien la destreza relativa")
else: print("  FALLA destreza mal medida"); ok = False

raw, calib = 0.6, calibrate.apply_curve(0.6, 60, cal)
print(f"     calibracion: crudo 0.60 -> calibrado {calib:.3f}")
if abs(calib-raw) > 0.005: print("  PASA  la curva corrige el sesgo")
else: print("  AVISO la curva no cambio nada")

perf = verify.recent_performance(days=30)
print(f"     desempeno 60min: {perf.get('60')}")
if perf.get("60"): print("  PASA  reporta desempeno")
else: print("  FALLA sin desempeno"); ok = False

print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
