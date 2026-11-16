"""Frentes fríos y heladas: mundos sintéticos donde se sabe la respuesta.

La trampa principal: en Aguascalientes la temperatura oscila 15-20 °C entre
el día y la noche y la presión ~4 hPa con la marea. Una firma de frente mal
hecha vería un "frente" cada tarde. Por eso todo se compara contra la misma
hora del día anterior, y aquí se comprueba que el ciclo diario solo, sin
frente, no dispara nada.
"""
from __future__ import annotations

import math
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

os.environ["NOWCAST_DATA_DIR"] = os.environ.get("NOWCAST_DATA_DIR") or tempfile.mkdtemp()

from nowcast import config, frio, metar  # noqa: E402

ok = True


def chk(nombre, cond, detalle=""):
    global ok
    print(f"  {'PASA' if cond else 'FALLA'}  {nombre}" + (f"  [{detalle}]" if detalle else ""))
    if not cond:
        ok = False


AHORA = datetime(2026, 11, 16, 18, tzinfo=timezone.utc)      # 12:00 local
INI = AHORA - timedelta(hours=24)
HORAS = [INI + timedelta(hours=i) for i in range(96)]


def serie_modelo(frente_en_h=None, minima=6.0, diurno=True):
    """Una serie horaria con ciclo diario y, si se pide, un frente."""
    d = {v: [] for v in frio.VARIABLES}
    for t in HORAS:
        loc = t.astimezone(config.TZ)
        h = loc.hour + loc.minute / 60
        # máximo hacia las 15:00, mínimo hacia las 07:00
        ciclo = math.cos((h - 15) / 24 * 2 * math.pi) if diurno else 0
        temp = (minima + 9) + 9 * ciclo
        rocio = 4.0
        pres = 1018 + 2 * math.cos((h - 10) / 12 * 2 * math.pi)   # marea semidiurna
        wdir = 200.0
        t700 = 5.0
        if frente_en_h is not None:
            pasado = (t - AHORA).total_seconds() / 3600 - frente_en_h
            if pasado >= 0:
                f = min(1.0, pasado / 6)                 # entra en 6 h
                temp -= 7 * f; rocio -= 8 * f; pres += 5 * f
                wdir = 20.0; t700 -= 4 * f
        d["temperature_2m"].append(round(temp, 1))
        d["dew_point_2m"].append(round(rocio, 1))
        d["pressure_msl"].append(round(pres, 1))
        d["wind_direction_10m"].append(wdir)
        d["wind_gusts_10m"].append(45.0 if frente_en_h is not None and wdir == 20.0 else 15.0)
        d["temperature_700hPa"].append(t700)
    return d


def respuesta(por_modelo):
    h = {"time": [t.strftime("%Y-%m-%dT%H:%M") for t in HORAS]}
    for m, d in por_modelo.items():
        for v, s in d.items():
            h[f"{v}_{m}"] = s
    return {"hourly": h}


M = config.OPENMETEO_MODELS

print("A. El ciclo diario solo no es un frente")
r = frio.calcular(AHORA, respuesta({m: serie_modelo() for m in M}))
chk("sin frente con 20 °C de oscilación y marea de 4 hPa", r["frente"] is None, str(r["frente"]))

print("\nB. Un frente que ven 3 de 5 modelos")
mod = {m: serie_modelo(frente_en_h=30 + k) if k < 3 else serie_modelo()
       for k, m in enumerate(M)}
r = frio.calcular(AHORA, respuesta(mod))
fr = r["frente"]
chk("lo detecta", fr is not None)
if fr:
    chk("llega cuando llega (±4 h de la mediana, 31 h)", 27 <= fr["en_h"] <= 35, f"{fr['en_h']} h")
    chk("dice cuántos lo ven", fr["modelos"] == "3 de 5", fr["modelos"])
    chk("la máxima baja", fr["max_antes"] - fr["max_despues"] >= 4,
        f"{fr['max_antes']} -> {fr['max_despues']}")
    chk("viento del norte", fr["viento_de"] in ("norte", "noreste"), str(fr["viento_de"]))
    chk("se enfría en altura (700 hPa)", (fr["enfria_700"] or 0) <= -2, str(fr["enfria_700"]))

print("\nC. Uno solo no basta")
mod = {m: serie_modelo(frente_en_h=30) if k == 0 else serie_modelo() for k, m in enumerate(M)}
chk("1 de 5 no es frente", frio.calcular(AHORA, respuesta(mod))["frente"] is None)

print("\nD. Helada: probabilidad que sigue a la mínima y a la duda")
frio_ = frio.calcular(AHORA, respuesta({m: serie_modelo(minima=0.5) for m in M}))
templ = frio.calcular(AHORA, respuesta({m: serie_modelo(minima=9.0) for m in M}))
chk("mínima de 0.5 °C: probable", frio_["helada"]["p"] > 0.7, str(frio_["helada"]))
chk("mínima de 9 °C: improbable", templ["helada"]["p"] < 0.05, str(templ["helada"]["p"]))
chk("sin calibrar todavía, y lo dice", frio_["helada"]["calibrada"] is False)
chk("la noche es la de mañana (a mediodía)", frio_["helada"]["fecha"] == "2026-11-17")
de_madrugada = frio.noche_objetivo(datetime(2026, 11, 17, 11, tzinfo=timezone.utc))  # 05:00 local
chk("y de madrugada es la de hoy", de_madrugada.isoformat() == "2026-11-17")

print("\nE. La calibración aprende el sesgo del aeropuerto")
filas, minimas = [], {}
for k in range(15):
    dia = datetime(2026, 10, 1, tzinfo=config.TZ) + timedelta(days=k)
    emit = (dia - timedelta(hours=6)).astimezone(timezone.utc)      # 18:00 de la víspera
    filas.append({"emitido_utc": emit.isoformat(), "helada_fecha": dia.date().isoformat(),
                  "tmin_mediana": 6.0})
    minimas[dia.date().isoformat()] = 4.0 + (k % 3 - 1) * 0.5           # 2 °C más frío
cal = frio.calibracion(filas, minimas)
chk("calibrada con 15 noches", cal["calibrada"] and cal["noches"] == 15, str(cal))
chk("aprende que el aeropuerto está ~2 °C más frío", abs(cal["sesgo"] + 2.0) < 0.2, str(cal["sesgo"]))
chk("con poca dispersión, pero nunca menos de 1 °C", cal["sigma"] == 1.0, str(cal["sigma"]))
pocas = frio.calibracion(filas[:5], minimas)
chk("con 5 noches no se fía", not pocas["calibrada"] and pocas["sigma"] == config.HELADA_SIGMA_INICIAL)

print("\nF. El aeropuerto: frente observado y mínimas")
obs = []
for k in range(72):
    t = datetime(2026, 11, 10, 6, tzinfo=timezone.utc) + timedelta(hours=k)
    loc = t.astimezone(config.TZ)
    ciclo = math.cos((loc.hour - 15) / 24 * 2 * math.pi)
    temp, rocio, pres, wdir = 14 + 9 * ciclo, 5.0, 1027 + 2 * math.cos((loc.hour - 10) / 12 * 2 * math.pi), 200
    if k >= 40:
        f = min(1, (k - 40) / 6)
        temp -= 7 * f; rocio -= 8 * f; pres += 5 * f; wdir = 20
    obs.append({"obs_utc": t.isoformat(), "temp": round(temp), "dewp": round(rocio),
                "wdir": wdir, "wspd": 8, "altim": round(pres, 1)})
ev = metar.frentes_observados(obs)
chk("un frente, uno solo", len(ev) == 1, str(ev))
if ev:
    llega = datetime(2026, 11, 10, 6, tzinfo=timezone.utc) + timedelta(hours=40)
    chk("cerca de cuando entró", timedelta(0) <= ev[0] - llega <= timedelta(hours=8),
        str(ev[0] - llega))
mins = metar.minimas_observadas(obs)
chk("mínimas por noche, solo las bien cubiertas", len(mins) >= 2, str(mins))
chk("la noche después del frente es más fría", list(mins.values())[-1] < list(mins.values())[0],
    str(mins))
chk("un viento VRB no se lee como del norte",
    metar.parsear([{"obsTime": 1794850980, "wdir": "VRB", "temp": 5}])[0]["wdir"] is None)

print("\nG. Avisos: una vez por frente, una vez por noche, a su hora")
mandados = []
enviar = lambda t, m, **k: mandados.append((t, m)) or True  # noqa: E731
mod = {m: serie_modelo(frente_en_h=30, minima=0.5) for m in M}
tarde = datetime(2026, 11, 17, 0, tzinfo=timezone.utc)      # 18:00 local
r = frio.calcular(tarde, respuesta(mod))
frio.avisar(r, tarde, enviar)
chk("frente y helada avisados", [t for t, _ in mandados] == ["Frente frío en camino", "Helada esta madrugada"],
    str([t for t, _ in mandados]))
frio.avisar(r, tarde + timedelta(minutes=15), enviar)
chk("la corrida siguiente no repite", len(mandados) == 2)
mediodia = frio.calcular(AHORA, respuesta(mod))
mandados.clear()
import json  # noqa: E402
st = json.load(open(config.STATE_JSON)); st.pop("frio_avisos", None)
json.dump(st, open(config.STATE_JSON, "w"))
frio.avisar(mediodia, AHORA, enviar)
chk("a mediodía no se avisa de la helada", all("Helada" not in t for t, _ in mandados), str(mandados))
chk("el texto de helada dice que aún no está calibrado",
    "sin calibrar" in frio.texto_helada(r["helada"]))

print("\nH. Sin datos no inventa")
r = frio.calcular(AHORA, {})
chk("todo null, nada de ceros", r["frente"] is None and r["helada"] is None, str(r))

print("\nI. La calificación (sección 10 de evaluar.py)")
import evaluar  # noqa: E402
filas, minimas = [], {}
for k in range(12):
    dia = datetime(2026, 12, 1, tzinfo=config.TZ) + timedelta(days=k)
    emit = (dia - timedelta(hours=6)).astimezone(timezone.utc)
    pred = 2.0 if k % 2 else 8.0                         # noches frías y templadas
    filas.append({"emitido_utc": emit.isoformat(), "helada_fecha": dia.date().isoformat(),
                  "tmin_mediana": pred, "tmin_corregida": pred - 1, "p_helada": 0.8 if k % 2 else 0.05})
    # una mañana temprana del mismo día no debe contar: no es "de la tarde"
    filas.append({"emitido_utc": (dia + timedelta(hours=5)).astimezone(timezone.utc).isoformat(),
                  "helada_fecha": dia.date().isoformat(), "tmin_mediana": 99, "p_helada": 0.0})
    minimas[dia.date().isoformat()] = pred - 1.5
h = evaluar.evaluar_heladas(filas, minimas)
chk("califica el pronóstico de la tarde, no el de esa misma mañana",
    h["noches"] == 12 and abs(h["sesgo"] + 1.5) < 1e-9, str(h))
chk("cuenta avisos acertados y heladas sin aviso",
    h["aviso_bien"] == 6 and h["sin_aviso"] == 0 and h["aviso_falso"] == 0, str(h))
chk("la corrección ayuda cuando el sesgo es real", h["mae_corregida"] < h["mae_cruda"])

base = datetime(2026, 12, 1, tzinfo=timezone.utc)
eventos = [base + timedelta(days=3), base + timedelta(days=10)]
anuncios = []
for d0, eta in ((2, base + timedelta(days=3, hours=2)),          # acierta el primero
                (6, base + timedelta(days=7))):                  # falsa alarma
    for k in range(4):
        emit = base + timedelta(days=d0, hours=k)
        anuncios.append({"emitido_utc": emit.isoformat(), "frente_eta_utc": eta.isoformat()})
fr = evaluar.evaluar_frentes(anuncios, eventos, hasta=base + timedelta(days=20))
chk("un frente acertado, uno perdido, una falsa alarma",
    (fr["acertados"], fr["perdidos"], fr["falsas"]) == (1, 1, 1), str(fr))
chk("muchas corridas de un mismo frente cuentan como un anuncio", fr["anunciados"] == 2)
chk("y el error de hora se mide", fr["error_h"] == 2.0, str(fr["error_h"]))
fr = evaluar.evaluar_frentes(anuncios, eventos, hasta=base + timedelta(days=7))
chk("un frente cuya hora no ha pasado no se juzga", fr["falsas"] == 0)

print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
