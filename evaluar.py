"""¿Este sistema predice algo, o solo repite la climatología con pasos extra?

    python evaluar.py

Lee `data/predictions.csv` y `data/observations.csv`, los cruza, y responde
la única pregunta que importa: **¿le gana a decir siempre el mismo número?**

## Por qué existe este archivo

Durante la tormenta de granizo del 30 de agosto la probabilidad a 60 minutos
se quedó clavada en 0.51 mientras el tope nuboso se enfriaba de 250 K a 226 K
y la celda pasaba de 26 km a estar encima. Un pronóstico que no se mueve
cuando el mundo cambia tanto no está observando el mundo.

La sospecha concreta: con 14,500 pares acumulados, `apply_curve` confía por
completo en la curva isotónica. Si esa curva se aplanó -porque la señal cruda
no discrimina- la salida es constante y el sistema reporta la tasa base
disfrazada de pronóstico.

## Las tres preguntas, en orden de importancia

**1. ¿Separa?** Si la probabilidad media los días que llovió es igual a la de
los días que no, el sistema no distingue nada y ninguna calibración lo
arregla. Es lo primero que hay que mirar y casi nunca se mira.

**2. ¿Es nítido?** Un pronóstico perfectamente calibrado que siempre dice el
22% es inútil aunque llueva el 22% de las veces. La nitidez es la dispersión
de lo que dice.

**3. ¿Le gana a la climatología?** El skill score. Positivo significa que
aporta; cero o negativo significa que decir "llueve el X% de los días" habría
servido igual.

Un sistema puede estar perfectamente calibrado y ser completamente inútil.
Por eso la calibración va la última.
"""
from __future__ import annotations

import csv
import os
import sys
from collections import defaultdict

import numpy as np

from nowcast import config, store


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def cargar() -> dict[int, list[tuple[float, int, dict]]]:
    """Cruza predicciones con observaciones. Devuelve {lead: [(p, llovio, fila)]}."""
    obs: dict[str, int] = {}
    for o in store.read_observations():
        valid = o.get("valid_utc")
        llovio = _num(o.get("rained"))
        if valid and llovio is not None:
            # Si hay varias observaciones del mismo instante, la manual gana:
            # es la única que vio el cielo de verdad.
            if valid not in obs or o.get("source") == "manual":
                obs[valid] = int(llovio)

    por_lead: dict[int, list] = defaultdict(list)
    for p in store.read_predictions():
        lead = _num(p.get("lead_min"))
        pf = _num(p.get("p_final"))
        valid = p.get("valid_utc")
        if lead is None or pf is None or valid not in obs:
            continue
        por_lead[int(lead)].append((pf, obs[valid], p))
    return dict(sorted(por_lead.items()))


def brier(ps: np.ndarray, ys: np.ndarray) -> float:
    return float(np.mean((ps - ys) ** 2))


def informe_lead(lead: int, datos: list) -> dict:
    ps = np.array([d[0] for d in datos], dtype=float)
    ys = np.array([d[1] for d in datos], dtype=float)
    n = len(ps)
    base = float(ys.mean())

    b = brier(ps, ys)
    b_clima = brier(np.full_like(ps, base), ys)
    skill = 1.0 - b / b_clima if b_clima > 0 else 0.0

    con = ps[ys == 1]
    sin = ps[ys == 0]
    separacion = (float(con.mean() - sin.mean())
                  if len(con) and len(sin) else None)

    return {
        "lead": lead, "n": n, "base": base,
        "brier": b, "brier_clima": b_clima, "skill": skill,
        "p_media": float(ps.mean()), "p_sd": float(ps.std()),
        "p_min": float(ps.min()), "p_max": float(ps.max()),
        "p_si_llovio": float(con.mean()) if len(con) else None,
        "p_si_no": float(sin.mean()) if len(sin) else None,
        "separacion": separacion,
        "ps": ps, "ys": ys, "datos": datos,
    }


def fiabilidad(ps: np.ndarray, ys: np.ndarray, bins: int = 5) -> list:
    """¿De las veces que dijo 70%, llovió el 70%?"""
    bordes = np.linspace(0, 1, bins + 1)
    salida = []
    for i in range(bins):
        sel = (ps >= bordes[i]) & (ps < bordes[i + 1] if i < bins - 1
                                   else ps <= bordes[i + 1])
        if sel.sum() == 0:
            continue
        salida.append((bordes[i], bordes[i + 1], int(sel.sum()),
                       float(ps[sel].mean()), float(ys[sel].mean())))
    return salida


def por_fuente(datos: list) -> dict:
    """Brier de cada fuente por separado, sobre los mismos casos."""
    salida = {}
    for col, nombre in (("p_ir", "infrarrojo"), ("p_models", "modelos"),
                        ("p_radar", "radar")):
        pares = [(_num(f.get(col)), y) for _p, y, f in datos]
        pares = [(p, y) for p, y in pares if p is not None]
        if len(pares) < 30:
            continue
        ps = np.array([p for p, _ in pares])
        ys = np.array([y for _, y in pares], dtype=float)
        con, sin = ps[ys == 1], ps[ys == 0]
        salida[nombre] = {
            "n": len(ps), "brier": brier(ps, ys),
            "separacion": (float(con.mean() - sin.mean())
                           if len(con) and len(sin) else None),
        }
    return salida


def presion_vs_lluvia(datos: list) -> dict | None:
    """La hipótesis abierta: ¿la caída de presión anticipa la lluvia AQUÍ?

    Nació de un caso: el 30 de agosto el aviso de presión salió 30 minutos
    antes de la lluvia y 90 antes del aviso de lluvia. Un caso no es
    evidencia; esto la busca.
    """
    pares = [(_num(f.get("pres_3h")), y) for _p, y, f in datos]
    pares = [(v, y) for v, y in pares if v is not None]
    if len(pares) < 30:
        return {"n": len(pares), "suficientes": False}
    vs = np.array([v for v, _ in pares])
    ys = np.array([y for _, y in pares], dtype=float)
    con, sin = vs[ys == 1], vs[ys == 0]
    if not len(con) or not len(sin):
        return {"n": len(vs), "suficientes": False}
    return {
        "n": len(vs), "suficientes": True,
        "presion_3h_si_llovio": float(con.mean()),
        "presion_3h_si_no": float(sin.mean()),
        "diferencia": float(con.mean() - sin.mean()),
    }


def curva_calibracion() -> dict:
    """¿La curva aprendida se aplanó? Si es plana, la salida es constante."""
    cal = store.load_json(config.CALIBRATION_JSON, {}) or {}
    salida = {}
    for lead, curva in (cal.get("curves") or {}).items():
        y = curva.get("y") or []
        if len(y) >= 2:
            salida[lead] = {"puntos": len(y), "recorrido": max(y) - min(y),
                            "min": min(y), "max": max(y)}
    return salida


def main() -> int:
    datos = cargar()
    if not datos:
        print("Todavía no hay pares de predicción y observación que cruzar.")
        print("Se necesitan unos días de temporada para que esto diga algo.")
        return 0

    print("=" * 66)
    print("¿PREDICE ALGO, O REPITE LA CLIMATOLOGÍA?")
    print("=" * 66)

    informes = [informe_lead(l, d) for l, d in datos.items()]

    print("\n1. ¿SEPARA? — la pregunta que casi nunca se hace")
    print("   Probabilidad media que dijo, según lo que pasó después.\n")
    print(f"   {'plazo':>7} {'casos':>7} {'llovió':>9} {'no llovió':>10} "
          f"{'separación':>11}")
    for r in informes:
        sep = r["separacion"]
        marca = "" if sep is None else ("  <-- nula" if abs(sep) < 0.02 else
                                        ("  <-- débil" if abs(sep) < 0.08 else ""))
        print(f"   {r['lead']:>5} m {r['n']:>7} "
              f"{_pc(r['p_si_llovio']):>9} {_pc(r['p_si_no']):>10} "
              f"{_pc(sep):>11}{marca}")
    print("\n   Si la separación es ~0, el sistema dice lo mismo llueva o no,")
    print("   y ninguna calibración puede arreglar eso.")

    print("\n2. ¿ES NÍTIDO? — un 51% eterno está calibrado y es inútil")
    print(f"\n   {'plazo':>7} {'media':>8} {'desv.':>8} {'mínimo':>8} "
          f"{'máximo':>8} {'recorrido':>10}")
    for r in informes:
        rec = r["p_max"] - r["p_min"]
        marca = "  <-- plano" if r["p_sd"] < 0.05 else ""
        print(f"   {r['lead']:>5} m {_pc(r['p_media']):>8} {_pc(r['p_sd']):>8} "
              f"{_pc(r['p_min']):>8} {_pc(r['p_max']):>8} {_pc(rec):>10}{marca}")

    print("\n3. ¿LE GANA A LA CLIMATOLOGÍA? — el skill score")
    print(f"\n   {'plazo':>7} {'Brier':>8} {'clima':>8} {'skill':>9}  veredicto")
    for r in informes:
        s = r["skill"]
        v = ("aporta" if s > 0.05 else
             "marginal" if s > 0.01 else
             "no aporta nada" if s > -0.01 else "PEOR que climatología")
        print(f"   {r['lead']:>5} m {r['brier']:>8.4f} {r['brier_clima']:>8.4f} "
              f"{s:>+9.3f}  {v}")
    print(f"\n   Tasa base observada: {_pc(informes[0]['base'])} de los casos con lluvia.")

    print("\n4. FIABILIDAD — de las veces que dijo X%, ¿llovió el X%?")
    ref = max(informes, key=lambda r: r["n"])
    print(f"   (plazo de {ref['lead']} min, {ref['n']} casos)\n")
    print(f"   {'rango':>12} {'casos':>7} {'dijo':>8} {'pasó':>8}")
    for a, b, n, dicho, real in fiabilidad(ref["ps"], ref["ys"]):
        print(f"   {_pc(a):>5}-{_pc(b):<6} {n:>7} {_pc(dicho):>8} {_pc(real):>8}")

    print("\n5. QUÉ FUENTE CARGA LA INFORMACIÓN")
    fuentes = por_fuente(ref["datos"])
    if fuentes:
        print(f"\n   {'fuente':>12} {'casos':>7} {'Brier':>8} {'separación':>11}")
        for nombre, d in sorted(fuentes.items(), key=lambda kv: kv[1]["brier"]):
            print(f"   {nombre:>12} {d['n']:>7} {d['brier']:>8.4f} "
                  f"{_pc(d['separacion']):>11}")
    else:
        print("   Sin datos suficientes por fuente todavía.")

    print("\n6. LA CURVA DE CALIBRACIÓN, ¿SE APLANÓ?")
    curvas = curva_calibracion()
    if curvas:
        print(f"\n   {'plazo':>7} {'puntos':>8} {'recorrido':>10}  "
              f"{'rango que produce':>20}")
        for lead, c in sorted(curvas.items(), key=lambda kv: int(kv[0])):
            marca = "  <-- APLANADA" if c["recorrido"] < 0.1 else ""
            print(f"   {lead:>5} m {c['puntos']:>8} {c['recorrido']:>10.3f}  "
                  f"{_pc(c['min'])} a {_pc(c['max'])}{marca}")
        print("\n   Una curva con poco recorrido convierte cualquier entrada")
        print("   en casi el mismo número de salida.")
    else:
        print("   Todavía no hay curva aprendida.")

    print("\n7. LA HIPÓTESIS ABIERTA: ¿la presión anticipa la lluvia aquí?")
    pr = presion_vs_lluvia(ref["datos"])
    if pr and pr.get("suficientes"):
        print(f"\n   Cambio de presión en 3 h, según lo que pasó ({pr['n']} casos):")
        print(f"     cuando llovió:    {pr['presion_3h_si_llovio']:+.2f} hPa")
        print(f"     cuando no llovió: {pr['presion_3h_si_no']:+.2f} hPa")
        print(f"     diferencia:       {pr['diferencia']:+.2f} hPa")
        if abs(pr["diferencia"]) < 0.3:
            print("\n   Diferencia pequeña: por ahora no parece anticipar nada.")
        else:
            print("\n   Hay señal. Merece entrar como fuente del pronóstico.")
    else:
        n = pr.get("n", 0) if pr else 0
        print(f"\n   Solo {n} casos con presión registrada; hacen falta ~30.")
        print("   Las columnas se añadieron el 31 de agosto; hay que esperar.")

    print("\n" + "=" * 66)
    peor = min(informes, key=lambda r: r["skill"])
    mejor = max(informes, key=lambda r: r["skill"])
    if mejor["skill"] < 0.01:
        print("VEREDICTO: por ahora no le gana a la climatología en ningún plazo.")
        print("Antes de añadir sensores, hay que entender por qué.")
    else:
        print(f"VEREDICTO: mejor plazo {mejor['lead']} min "
              f"(skill {mejor['skill']:+.3f}); "
              f"peor {peor['lead']} min ({peor['skill']:+.3f}).")
    print("=" * 66)
    return 0


def _pc(v) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


if __name__ == "__main__":
    sys.exit(main())
