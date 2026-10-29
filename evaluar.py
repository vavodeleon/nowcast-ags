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
from datetime import datetime, timedelta

import numpy as np

from nowcast import config, store


# Las dos fuentes de verdad que no salieron de un modelo numérico.
INDEPENDIENTES = {"manual", "malla", "muestra"}
# Las espontaneas son las que da una persona cuando ELLA decide. Sirven de
# verdad, pero no para comparar fuentes: se dan sobre todo cuando el sistema
# fallo. 'muestra' queda fuera de este grupo a proposito -el momento lo eligio
# el sistema al azar- y por eso tiene su propia seccion, la 4-quater.
ESPONTANEAS = {"manual", "malla"}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def observaciones() -> dict[str, tuple[int, str]]:
    """{franja: (llovió, fuente)}, con la humana ganando si hay varias."""
    obs: dict[str, tuple[int, str]] = {}
    for o in store.read_observations():
        valid = o.get("valid_utc")
        llovio = _num(o.get("rained"))
        if valid and llovio is not None:
            # Si hay varias observaciones del mismo instante, gana la humana:
            # es la única que vio el cielo de verdad. "manual" llega por la
            # página o por la notificación; "malla" por radio, que es la que
            # sigue funcionando en el apagón. Mismo ojo, distinto camino.
            if valid not in obs or o.get("source") in INDEPENDIENTES:
                obs[valid] = (int(llovio), str(o.get("source") or "?"))
    return obs


def cargar() -> dict[int, list[tuple[float, int, dict]]]:
    """Cruza predicciones con observaciones. Devuelve {lead: [(p, llovio, fila)]}."""
    obs = observaciones()
    por_lead: dict[int, list] = defaultdict(list)
    for p in store.read_predictions():
        lead = _num(p.get("lead_min"))
        pf = _num(p.get("p_final"))
        valid = p.get("valid_utc")
        if lead is None or pf is None or valid not in obs:
            continue
        llovio, fuente_verdad = obs[valid]
        p = dict(p, _verdad=fuente_verdad)
        por_lead[int(lead)].append((pf, llovio, p))
    return dict(sorted(por_lead.items()))


def brier(ps: np.ndarray, ys: np.ndarray) -> float:
    return float(np.mean((ps - ys) ** 2))


def es_independiente(fuente: str) -> bool:
    """¿La verdad de esta fila viene de una persona, no de un modelo?

    La distinción es el hallazgo central de este archivo: el análisis de
    Open-Meteo es un producto derivado de modelos numéricos, y los modelos que
    se evalúan son de esa familia. Solo estas dos fuentes son independientes.
    """
    return str(fuente or "").strip() in INDEPENDIENTES


def solo_manuales(datos: list) -> list:
    """Las correcciones espontaneas: sesgadas hacia el error por construccion."""
    return [d for d in datos
            if str(d[2].get("_verdad", "")).strip() in ESPONTANEAS]


def solo_muestra(datos: list) -> list:
    """Las respuestas a preguntas al azar: la unica verdad sin ese sesgo."""
    return [d for d in datos if str(d[2].get("_verdad", "")).strip() == "muestra"]


def pesos_muestra(registro: list[dict]) -> dict[str, float]:
    """{franja de la observación: 1/π} para cada pregunta contestada a tiempo.

    Desde el 12/10/2026 se pregunta más en los ratos con riesgo. Promediadas
    tal cual, las respuestas tendrían más lluvia de la que hay. Pesando cada
    una por 1/π (Horvitz-Thompson), cada respuesta vale por todos los ratos
    que representa, y el promedio vuelve a ser el de todos los ratos.

    La clave es la franja de la respuesta, que es donde `registrar` la pone
    en las observaciones.
    """
    pesos = {}
    for f in registro:
        pi = _num(f.get("pi"))
        if f.get("estado") != "respondida" or not pi or pi <= 0:
            continue
        t = f.get("respondido_utc") or ""
        try:
            slot = store.round_slot(datetime.fromisoformat(t))
        except ValueError:
            continue
        pesos[slot] = 1.0 / pi
    return pesos


def brier_w(ps: np.ndarray, ys: np.ndarray, ws: np.ndarray) -> float:
    return float(np.sum(ws * (ps - ys) ** 2) / np.sum(ws))


def comparar_ponderado(m: list, pesos: dict) -> dict:
    """Brier, separación y skill de cada fuente y de la mezcla, con 1/π."""
    filas = [(pf, y, f, pesos.get(f.get("valid_utc"), None)) for pf, y, f in m]
    filas = [x for x in filas if x[3] is not None]
    if not filas:
        return {}
    ws = np.array([x[3] for x in filas])
    ys = np.array([x[1] for x in filas], dtype=float)
    tasa = float(np.sum(ws * ys) / np.sum(ws))
    clima = brier_w(np.full_like(ys, tasa), ys, ws)
    out = {"n": len(filas), "lluvias": int(ys.sum()),
           "n_efectivo": float(ws.sum() ** 2 / np.sum(ws ** 2)),
           "tasa": tasa, "fuentes": {}}

    def resumen(ps):
        con, sin = ys == 1, ys == 0
        sep = None
        if con.any() and sin.any():
            sep = float(np.average(ps[con], weights=ws[con])
                        - np.average(ps[sin], weights=ws[sin]))
        b = brier_w(ps, ys, ws)
        return {"brier": b, "separacion": sep,
                "skill": (1 - b / clima) if clima > 0 else None}

    out["mezcla"] = resumen(np.array([x[0] for x in filas], dtype=float))
    for col, nombre in (("p_ir", "infrarrojo"), ("p_models", "modelos"),
                        ("p_radar", "radar")):
        vals = [_num(x[2].get(col)) for x in filas]
        if any(v is None for v in vals):
            continue
        out["fuentes"][nombre] = resumen(np.array(vals, dtype=float))
    return out


def muestreo_al_azar(datos: dict) -> None:
    """4-quater. La unica comparacion de fuentes que no esta sesgada.

    Todo lo anterior tiene un problema de fondo. La verdad de Open-Meteo sale
    de los mismos modelos que se evaluan, y las correcciones espontaneas son
    la muestra de los errores. Aqui la verdad la da una persona, y el MOMENTO
    lo eligio el sistema al azar, sin mirar el pronostico.

    Queda un sesgo posible, y se mide en vez de suponerlo: que se conteste mas
    cuando llueve. Para eso se registran tambien las preguntas que nadie
    contesto, y se compara lo que dijo Open-Meteo en unas y en otras.
    """
    from nowcast import muestreo as _m

    print("\n4-quater. PREGUNTAS AL AZAR — la comparación sin sesgo")
    # Las de prueba -abiertas a mano, `pi` vacio- no son al azar y no cuentan.
    registro = [f for f in _m.leer() if f.get("pi")]
    if not registro:
        print("\n   Todavía no se ha hecho ninguna pregunta.")
        return

    hechas = len(registro)
    resp = [f for f in registro if f.get("estado") == "respondida"]
    tarde = sum(1 for f in registro if f.get("estado") == "tarde")
    por_canal = {}
    for f in resp:
        por_canal[f.get("canal") or "?"] = por_canal.get(f.get("canal") or "?", 0) + 1
    canales = ", ".join(f"{n} por {c}" for c, n in sorted(por_canal.items()))
    print(f"\n   {hechas} preguntas, {len(resp)} contestadas a tiempo "
          f"({len(resp) / hechas:.0%})" + (f": {canales}" if canales else "")
          + (f"; {tarde} tarde" if tarde else "") + ".")

    # --- ¿se contesta mas cuando llueve? Open-Meteo existe para todos los
    # ratos, contestados o no, y eso permite medirlo.
    om = {o["valid_utc"]: o for o in store.read_observations()}
    def _lluvia_om(f):
        t = f.get("preguntado_utc") or ""
        try:
            slot = store.round_slot(datetime.fromisoformat(t))
        except ValueError:
            return None
        o = om.get(slot)
        return _num(o.get("rained")) if o else None
    si = [v for f in resp if (v := _lluvia_om(f)) is not None]
    no = [v for f in registro if f.get("estado") == "sin_respuesta"
          and (v := _lluvia_om(f)) is not None]
    if len(si) >= 10 and len(no) >= 10:
        a, b = sum(si) / len(si), sum(no) / len(no)
        print(f"\n   Lluvia según Open-Meteo en los ratos contestados: {a:.0%} "
              f"({len(si)}); en los no contestados: {b:.0%} ({len(no)}).")
        # Con el mismo cuidado que la 8-bis: un umbral fijo decia "parecido"
        # a 16% contra 6%, y "distinto" a diferencias que el azar da solo.
        # Se barajan las etiquetas contestada/no contestada y se mira cuanta
        # diferencia sale sin que haya nada detras.
        todo = np.array(si + no, dtype=float)
        rng = np.random.default_rng(0)
        nulas = []
        for _ in range(1000):
            rng.shuffle(todo)
            nulas.append(todo[:len(si)].mean() - todo[len(si):].mean())
        lo, hi = np.percentile(nulas, [2.5, 97.5])
        if lo <= a - b <= hi:
            print(f"   El azar da diferencias entre {lo:+.0%} y {hi:+.0%}: no hay")
            print("   señal de que contestar dependa del tiempo. Con más")
            print("   preguntas esta comprobación se vuelve más fina.")
        else:
            print(f"   Fuera de lo que da el azar ({lo:+.0%} a {hi:+.0%}): se")
            print("   contesta distinto según el tiempo. La muestra respondida")
            print("   tiene sesgo de respuesta; tomar lo de abajo con cuidado.")
    else:
        print("\n   Aún no hay suficientes contestadas y no contestadas para")
        print("   saber si contestar depende del tiempo (hacen falta 10 y 10).")

    # --- la comparacion de fuentes, en cada plazo que tenga datos. Cada
    # respuesta pesa 1/π: desde el 12/10/2026 se pregunta mas cuando hay
    # riesgo, y sin el peso los ratos lluviosos contarian de mas.
    pesos = pesos_muestra(registro)
    con_riesgo = sum(1 for f in registro if str(f.get("riesgo")) == "1")
    if con_riesgo:
        print(f"   {con_riesgo} de las {hechas} se sortearon con riesgo (π mayor); "
              "todo lo de abajo va ponderado por 1/π.")
    hay_algo = False
    for lead, d in datos.items():
        m = solo_muestra(d)
        if not m:
            continue
        hay_algo = True
        r = comparar_ponderado(m, pesos)
        if not r:
            continue
        print(f"\n   Plazo {lead} min: {r['n']} casos, {r['lluvias']} con lluvia "
              f"(tasa estimada de todos los ratos: {r['tasa']:.0%}).")
        if r["n"] < 30 or r["lluvias"] < 8:
            print("   Todavía no alcanza: hacen falta ~30 casos y al menos 8 con")
            print("   lluvia para que una diferencia entre fuentes signifique algo.")
            continue
        print(f"   (con los pesos, valen como {r['n_efectivo']:.0f} casos sin ponderar)")
        print(f"   {'fuente':>12} {'Brier':>8} {'separación':>11}")
        for nombre, x in sorted(r["fuentes"].items(), key=lambda kv: kv[1]["brier"]):
            print(f"   {nombre:>12} {x['brier']:>8.4f} {_pc(x['separacion']):>11}")
        mz = r["mezcla"]
        print(f"   mezcla: Brier {mz['brier']:.4f}, separación {_pc(mz['separacion'])}"
              + (f", skill {mz['skill']:+.3f}" if mz["skill"] is not None else ""))
    if not hay_algo:
        print("\n   Ninguna respuesta cruza todavía con una predicción guardada.")


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
    """Brier de cada fuente, y el de la mezcla SOBRE LOS MISMOS CASOS.

    La segunda parte es la que importa. Hasta el 28 de septiembre de 2026 la
    seccion 5 comparaba la mezcla calificada sobre todos sus casos contra cada
    fuente calificada sobre los suyos, y los "suyos" podian ser un subconjunto
    cualquiera. Asi salio el aviso "la mezcla (0.0788) es PEOR que infrarrojo
    sin ajuste (0.0364)": esa fila tenia 748 casos, casi todos de una semana con
    3.4% de lluvia, y en una semana seca cualquier cosa saca buen Brier. Su
    separacion era 0.1%, o sea ninguna informacion. No era una fuente mejor,
    era una semana mas facil.

    Dos numeros calificados sobre casos distintos no se pueden comparar. Por
    eso cada fuente trae ahora `brier_mezcla`: el de la mezcla en exactamente
    las mismas filas.
    """
    salida = {}
    for col, nombre in (("p_ir", "infrarrojo"), ("p_models", "modelos"),
                        ("p_radar", "radar")):
        trios = [(_num(f.get(col)), pf, y) for pf, y, f in datos]
        trios = [(p, pf, y) for p, pf, y in trios if p is not None]
        if len(trios) < 15:
            continue
        ps = np.array([t[0] for t in trios])
        mz = np.array([t[1] for t in trios], dtype=float)
        ys = np.array([t[2] for t in trios], dtype=float)
        con, sin = ps[ys == 1], ps[ys == 0]
        salida[nombre] = {
            "n": len(ps), "brier": brier(ps, ys),
            "brier_mezcla": brier(mz, ys),
            "lluvia": float(ys.mean()),
            "separacion": (float(con.mean() - sin.mean())
                           if len(con) and len(sin) else None),
        }
    return salida


def ganadoras(fuentes: dict) -> list:
    """Las fuentes que le ganan a la mezcla EN SUS PROPIOS CASOS.

    Una fuente sin separacion no compite aunque su Brier sea menor: sacar buen
    Brier sin distinguir nada es haber caido en una semana seca, no ser mejor.
    Es exactamente la trampa del aviso falso del 28/09/2026.
    """
    return [(n, d) for n, d in fuentes.items()
            if d["brier"] < d["brier_mezcla"] - 0.001
            and (d["separacion"] or 0) >= 0.01]


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

    print("\n4-bis. ¿DE DÓNDE SALE LA VERDAD? — el sesgo que puede engañarnos")
    verdades = {}
    for _p, _y, f in ref["datos"]:
        v = f.get("_verdad", "?")
        verdades[v] = verdades.get(v, 0) + 1
    for v, n in sorted(verdades.items(), key=lambda kv: -kv[1]):
        print(f"     {v:<24} {n:>6} casos")
    om = sum(n for v, n in verdades.items() if "openmeteo" in v)
    manual = sum(n for v, n in verdades.items() if es_independiente(v))
    if om and om / max(sum(verdades.values()), 1) > 0.5:
        print("\n   ATENCIÓN: la verdad viene del análisis de Open-Meteo, que es")
        print("   un producto derivado de modelos numéricos. Los 'modelos' que")
        print("   se evalúan abajo son de la MISMA familia. Parte de su ventaja")
        print("   puede ser circularidad y no habilidad: se les califica con un")
        print("   examen que ellos escribieron.")
        print(f"\n   Observaciones independientes (tuyas): {manual}")
        print("   NO bastan para arbitrar, y conviene saber por qué: ver 4-ter.")

    print("\n4-ter. SKILL SOLO CON TUS CONFIRMACIONES — y por qué es pesimista")
    man = solo_manuales(ref["datos"])
    if len(man) >= 20:
        rm = informe_lead(ref["lead"], man)
        print(f"\n   {len(man)} casos juzgados por ti, plazo {ref['lead']} min:")
        print(f"     lluvia observada:  {_pc(rm['base'])}  "
              f"(tasa base general: {_pc(ref['base'])})")
        print(f"     separación:        {_pc(rm['separacion'])}")
        print(f"     skill:             {rm['skill']:+.3f}   "
              f"(con Open-Meteo: {ref['skill']:+.3f})")
        print("\n   ESTE NÚMERO ES UNA COTA INFERIOR, y no por prudencia:")
        print("   por construcción. Confirmado el 20/09/2026 preguntándolo: el")
        print("   comando de radio se usa \"cuando la app dice que no llueve pero")
        print("   sí está lloviendo\". Nadie escribe nunca \"la app acertó\".")
        print("   Así que esta muestra no está sesgada hacia el error: ES la")
        print("   muestra de los errores. El sistema real acierta más que esto.")
        print("\n   Y la consecuencia incómoda: tampoco sirve para comparar")
        print("   fuentes entre sí. Si solo corriges cuando el pronóstico falló,")
        print("   penalizas a la fuente que lo dominaba -los modelos- por el")
        print("   mismo motivo por el que la escogiste. Cambiar el orden aquí no")
        print("   probaría que el infrarrojo es mejor; probaría que el sesgo")
        print("   existe. La tabla de abajo se imprime para verlo, no para")
        print("   creerla.")
        if abs(rm["base"] - ref["base"]) > 0.15:
            print(f"\n   Tasa de lluvia en tus casos: {_pc(rm['base'])} contra "
                  f"{_pc(ref['base'])} general.")
            print("   La diferencia mide el tamaño del sesgo, no un defecto.")
        else:
            print(f"\n   Curioso: la tasa de lluvia en tus casos ({_pc(rm['base'])})")
            print(f"   se parece a la general ({_pc(ref['base'])}). Corriges tanto")
            print("   los falsos positivos como los negativos, que es lo mejor")
            print("   que podía pasar con una muestra que tú eliges.")
        print("\n   Para arbitrar de verdad hace falta que la muestra la elija el")
        print("   SISTEMA: preguntar a ratos al azar, no solo cuando algo salió")
        print("   mal. Treinta respuestas a preguntas no provocadas valen más")
        print("   que trescientas correcciones espontáneas.")
    else:
        print(f"\n   Solo {len(man)} confirmaciones tuyas en este plazo; hacen")
        print("   falta ~20 para decir algo.")

    muestreo_al_azar(datos)

    print("\n5. QUÉ FUENTE CARGA LA INFORMACIÓN")
    fuentes = por_fuente(ref["datos"])
    if fuentes:
        print()
        print(f"   {'':>12} {'':>7} {'':>8} {'':>11} {'mezcla en':>10}")
        print(f"   {'fuente':>12} {'casos':>7} {'Brier':>8} {'separación':>11} "
              f"{'esos casos':>10}")
        for nombre, d in sorted(fuentes.items(), key=lambda kv: kv[1]["brier"]):
            print(f"   {nombre:>12} {d['n']:>7} {d['brier']:>8.4f} "
                  f"{_pc(d['separacion']):>11} {d['brier_mezcla']:>10.4f}")

        ganan = ganadoras(fuentes)
        if ganan:
            print()
            for nombre, d in ganan:
                print(f"   En sus {d['n']} casos, {nombre} sola "
                      f"({d['brier']:.4f}) le gana a la mezcla "
                      f"({d['brier_mezcla']:.4f}).")
            print("   Los pesos no se han movido lo suficiente hacia esa fuente.")
        else:
            print("\n   Ninguna fuente sola le gana a la mezcla en sus propios")
            print("   casos. La mezcla hace su trabajo.")
    else:
        print("   Sin datos suficientes por fuente todavía.")

    if len(man) >= 20:
        fm = por_fuente(man)
        if fm:
            print(f"\n   Las mismas fuentes, juzgadas SOLO por ti ({len(man)} casos):")
            print(f"   {'fuente':>12} {'casos':>7} {'Brier':>8} {'separación':>11}")
            for nombre, d in sorted(fm.items(), key=lambda kv: kv[1]["brier"]):
                print(f"   {nombre:>12} {d['n']:>7} {d['brier']:>8.4f} "
                      f"{_pc(d['separacion']):>11}")
            print("\n   Aquí Open-Meteo no se califica a sí mismo. Si el orden")
            print("   cambia respecto a la tabla de arriba, la ventaja de los")
            print("   modelos era en parte circularidad.")

    print("\n5-bis. LOS PESOS QUE HA APRENDIDO")
    cal = store.load_json(config.CALIBRATION_JSON, {}) or {}
    pesos = cal.get("weights") or {}
    if pesos:
        print(f"\n   {'plazo':>7} {'radar':>8} {'infrarr.':>9} {'modelos':>9}")
        for lead in sorted(pesos, key=lambda k: int(k)):
            w = pesos[lead]
            print(f"   {lead:>5} m {w.get('radar', 0):>8.2f} "
                  f"{w.get('ir', 0):>9.2f} {w.get('models', 0):>9.2f}")
        print("\n   Arrancaron en radar 0.15 / infrarrojo 0.60 / modelos 0.25.")
    else:
        print("   Todavía usa los pesos por omisión.")

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

    seccion_presion(datos)

    print("\n8. ¿MEJORÓ DESDE EL ÚLTIMO CAMBIO? — el número acumulado no lo dice")
    por_tramo(ref)
    por_salud(datos)
    nubes_bajas_seccion(datos)

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


# ====================================================================
# ¿UN DATO NUEVO MEJORA EL PRONÓSTICO? — la prueba común de las secciones 7 y 9
# ====================================================================
#
# Hasta el 12/10/2026 cada hipótesis tenía su propia prueba, y las dos
# preguntaban otra cosa:
#
# - La 7 comparaba la presión media cuando llovió y cuando no, mezclando los
#   ratos en que ya llovía al emitir. Así cualquier cosa que acompañe a la
#   lluvia parece predecirla. Y con 0.3 hPa de diferencia declaraba "merece
#   entrar", sin intervalo ni signo, con 4,000 filas que son ~60 días.
# - La 9 comparaba solo dentro de cada día, copiado de la 8-bis. Allí tenía
#   sentido (separar el enlace del tiempo); aquí descartaba justo lo útil: un
#   día entero bajo nube baja no tiene con qué compararse y no contaba.
#
# La pregunta que decide es una sola: si al pronóstico de siempre se le añade
# este dato, ¿acierta más? Se responde recalibrando p_final con y sin el dato
# (regresión logística de tres números), dejando fuera un día cada vez para
# calificar, y con un intervalo que remuestrea DÍAS completos: los ratos de
# un mismo día no son independientes, y contarlos como si lo fueran da una
# seguridad que no existe.

def _sig(z):
    return 1.0 / (1.0 + np.exp(-z))


def _logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-3, 1 - 1e-3)
    return np.log(p / (1 - p))


def _ajustar(X: np.ndarray, y: np.ndarray, l2: float = 1e-2) -> np.ndarray:
    """Regresión logística por Newton. Pocas variables: no hace falta más."""
    w = np.zeros(X.shape[1])
    for _ in range(30):
        p = _sig(X @ w)
        g = X.T @ (p - y) + l2 * w
        H = X.T @ (X * (p * (1 - p))[:, None]) + l2 * np.eye(X.shape[1])
        paso = np.linalg.solve(H, g)
        w -= paso
        if np.max(np.abs(paso)) < 1e-6:
            break
    return w


def ganancia_cv(filas: list[dict], reps: int = 400, semilla: int = 0) -> dict | None:
    """¿Añadir `x` a p_final mejora el Brier? Validación dejando fuera un día.

    `filas`: [{"p": p_final, "y": 0/1, "x": dato, "dia": "AAAA-MM-DD"}].
    Devuelve los dos Brier, la ganancia relativa y su intervalo del 95%.
    """
    if not filas:
        return None
    dias = sorted({f["dia"] for f in filas})
    ys = np.array([f["y"] for f in filas], dtype=float)
    out = {"n": len(filas), "lluvias": int(ys.sum()), "dias": len(dias)}
    if len(dias) < 3 or ys.sum() < 2 or ys.sum() == len(ys):
        return out
    lp = _logit([f["p"] for f in filas])
    xs = np.array([f["x"] for f in filas], dtype=float)
    xs = (xs - xs.mean()) / (xs.std() or 1.0)
    uno = np.ones_like(lp)
    X0 = np.column_stack([uno, lp])
    X1 = np.column_stack([uno, lp, xs])
    idx = np.array([dias.index(f["dia"]) for f in filas])

    e0 = np.zeros(len(dias))
    e1 = np.zeros(len(dias))
    cnt = np.zeros(len(dias))
    for k in range(len(dias)):
        fuera = idx == k
        dentro = ~fuera
        if ys[dentro].sum() == 0 or ys[dentro].sum() == dentro.sum():
            continue
        w0 = _ajustar(X0[dentro], ys[dentro])
        w1 = _ajustar(X1[dentro], ys[dentro])
        e0[k] = np.sum((_sig(X0[fuera] @ w0) - ys[fuera]) ** 2)
        e1[k] = np.sum((_sig(X1[fuera] @ w1) - ys[fuera]) ** 2)
        cnt[k] = fuera.sum()

    total = cnt.sum()
    b0, b1 = e0.sum() / total, e1.sum() / total
    rng = np.random.default_rng(semilla)
    boot = []
    for _ in range(reps):
        m = rng.integers(0, len(dias), len(dias))
        if cnt[m].sum() == 0:
            continue
        bb0 = e0[m].sum() / cnt[m].sum()
        bb1 = e1[m].sum() / cnt[m].sum()
        boot.append((bb0 - bb1) / bb0 if bb0 > 0 else 0.0)
    lo, hi = np.percentile(boot, [2.5, 97.5]) if boot else (None, None)
    out.update({"brier_sin": b0, "brier_con": b1,
                "ganancia": (b0 - b1) / b0 if b0 > 0 else 0.0,
                "lo": float(lo) if lo is not None else None,
                "hi": float(hi) if hi is not None else None})
    return out


# Por debajo de esto no importa. Añadir un dato inútil siempre cuesta un
# poco fuera de muestra (el ajuste aprende su ruido): con ruido puro y mil
# casos salía "empeora" por -0.2%, cierto e irrelevante.
GANANCIA_MINIMA = 0.01


def veredicto_ganancia(r: dict | None, min_lluvias: int = 20,
                       min_dias: int = 8) -> str:
    """'aun_no', 'gana', 'empeora' o 'nada', con la misma regla para todo."""
    if not r or "ganancia" not in r or r["lluvias"] < min_lluvias \
            or r["dias"] < min_dias or r["lo"] is None:
        return "aun_no"
    if r["lo"] > 0 and r["ganancia"] >= GANANCIA_MINIMA:
        return "gana"
    if r["hi"] < -GANANCIA_MINIMA:
        return "empeora"
    # El intervalo cabe tanto una ganancia como una pérdida que importan: no
    # es "no aporta", es "no se sabe". La primera versión los juntaba, y la
    # nube baja salió "fuera del pronóstico" con un intervalo de -20% a +25%
    # (25/10/2026): veinte días, casi toda la lluvia en unos pocos.
    if r["lo"] < -GANANCIA_MINIMA and r["hi"] > GANANCIA_MINIMA:
        return "incierto"
    return "nada"


def _imprimir_ganancia(r: dict | None, etiqueta: str) -> str:
    v = veredicto_ganancia(r)
    if r and "ganancia" in r:
        print(f"   {etiqueta:>10} {r['n']:>6} {r['lluvias']:>7} {r['dias']:>5} "
              f"{r['brier_sin']:>9.4f} {r['brier_con']:>9.4f} "
              f"{r['ganancia'] * 100:>+7.1f}%  [{r['lo'] * 100:+.1f}%, {r['hi'] * 100:+.1f}%]")
    else:
        n = r["n"] if r else 0
        ll = r["lluvias"] if r else 0
        print(f"   {etiqueta:>10} {n:>6} {ll:>7}   — pocos datos para probar")
    return v


def _dia_local(txt: str) -> str | None:
    try:
        t = datetime.fromisoformat(str(txt).replace("Z", "+00:00"))
    except ValueError:
        return None
    from nowcast import config as _c
    return t.astimezone(_c.TZ).strftime("%Y-%m-%d")


def _hora_local(txt: str) -> int | None:
    try:
        t = datetime.fromisoformat(str(txt).replace("Z", "+00:00"))
    except ValueError:
        return None
    from nowcast import config as _c
    return t.astimezone(_c.TZ).hour


_CABECERA_GANANCIA = (f"   {'':>10} {'casos':>6} {'lluvias':>7} {'días':>5} "
                      f"{'Brier sin':>9} {'Brier con':>9} {'ganancia':>8}  intervalo 95%")


def _explicar(veredictos: list[str], que: str) -> None:
    if "gana" in veredictos:
        print(f"\n   GANA en algún plazo: {que} mejora el pronóstico fuera de los")
        print("   días con que se ajustó, y el intervalo no toca el cero. Merece")
        print("   probarse como fuente (y volver a medirlo ya dentro).")
    elif all(v == "aun_no" for v in veredictos):
        print("\n   Aún no hay con qué decidir: hacen falta ~20 lluvias en al menos")
        print("   8 días distintos en el plazo que se mire.")
    elif "empeora" in veredictos:
        print(f"\n   Empeora en algún plazo: {que} confunde más de lo que ayuda.")
    elif "incierto" in veredictos:
        print(f"\n   No se sabe todavía: el intervalo cabe una mejora y una pérdida")
        print(f"   que importan. Con {que} así no se decide nada; hacen falta más")
        print("   días distintos con lluvia, no más ratos del mismo día.")
    else:
        print(f"\n   No aporta: {que} no mejora el pronóstico en más de 1% fuera de")
        print("   los días del ajuste. Fuera del pronóstico.")


# --------------------------------------------------------------- sección 7

def presion_mecanismo(datos_lead: list, obs: dict, col: str = "pres_3h",
                      reps: int = 400, semilla: int = 0) -> dict | None:
    """Descriptivo: en ratos SECOS al emitir, ¿qué hace la presión antes de que
    empiece a llover? Comparado a la misma hora del día, con intervalo por días.

    Negativo = baja antes de llover (frente, baja que se acerca). Positivo =
    sube (el aire frío que empuja una tormenta cercana). Las dos pueden servir
    para pronosticar; esto solo dice cuál es, no si sirve.
    """
    from nowcast import store as _s
    filas = []
    for _p, y, f in datos_lead:
        v = _num(f.get(col))
        if v is None:
            continue
        try:
            slot = _s.round_slot(datetime.fromisoformat(f.get("issued_utc", "")))
        except ValueError:
            continue
        o = obs.get(slot)
        if o is None or o[0] != 0:
            continue                       # ya llovía, o no se sabe
        h, d = _hora_local(f["issued_utc"]), _dia_local(f["issued_utc"])
        if h is None or d is None:
            continue
        filas.append((v, y, h, d))
    if not filas:
        return None

    v = np.array([f[0] for f in filas], dtype=float)
    y = np.array([f[1] for f in filas], dtype=int)
    h = np.array([f[2] for f in filas], dtype=int)
    dias = sorted({f[3] for f in filas})
    di = {d: k for k, d in enumerate(dias)}
    dia = np.array([di[f[3]] for f in filas])
    por_dia = [np.flatnonzero(dia == k) for k in range(len(dias))]

    def dif(ix):
        # Por hora del día: media con lluvia menos media sin, pesada por el
        # grupo más chico. En numpy: el remuestreo lo llama 400 veces y en el
        # Pi 3 un bucle de Python por fila tardaba minutos.
        hh, vv, yy = h[ix], v[ix], y[ix]
        n1 = np.bincount(hh[yy == 1], minlength=24)
        n0 = np.bincount(hh[yy == 0], minlength=24)
        s1 = np.bincount(hh[yy == 1], weights=vv[yy == 1], minlength=24)
        s0 = np.bincount(hh[yy == 0], weights=vv[yy == 0], minlength=24)
        ok = (n1 > 0) & (n0 > 0)
        if not ok.any():
            return None
        peso = np.minimum(n1, n0)[ok]
        return float(np.sum(peso * (s1[ok] / n1[ok] - s0[ok] / n0[ok])) / peso.sum())

    d0 = dif(np.arange(len(v)))
    rng = np.random.default_rng(semilla)
    boot = []
    for _ in range(reps):
        ix = np.concatenate([por_dia[k] for k in rng.integers(0, len(dias), len(dias))])
        b = dif(ix)
        if b is not None:
            boot.append(b)
    lo, hi = (np.percentile(boot, [2.5, 97.5]) if boot else (None, None))
    return {"n": len(filas), "lluvias": sum(y for _v, y, *_r in filas),
            "dias": len(dias), "dif": d0,
            "lo": float(lo) if lo is not None else None,
            "hi": float(hi) if hi is not None else None}


def seccion_presion(datos: dict, plazos=(15, 60, 180)) -> None:
    print("\n7. LA PRESIÓN — ¿mejora el pronóstico? y ¿anticipa o reacciona?")
    print("   (reescrita el 12/10/2026: ver el comentario de ganancia_cv)\n")
    print("   a) La decisión: p_final recalibrado con y sin el cambio de presión")
    print("      en 3 h, calificado en días que el ajuste no vio.\n")
    print(_CABECERA_GANANCIA)
    vs = []
    for lead in plazos:
        filas = []
        for pf, y, f in datos.get(lead, []):
            x, d = _num(f.get("pres_3h")), _dia_local(f.get("issued_utc", ""))
            if x is not None and d is not None:
                filas.append({"p": pf, "y": y, "x": x, "dia": d})
        vs.append(_imprimir_ganancia(ganancia_cv(filas), f"{lead} min"))
    _explicar(vs, "la presión")

    print("\n   b) El mecanismo (no decide nada): solo ratos SECOS al emitir,")
    print("      presión en 3 h cuando empezó a llover menos cuando no, a la")
    print("      misma hora del día. Negativo: baja antes de llover. Positivo:")
    print("      sube, como con el aire frío que empuja una tormenta cercana.\n")
    obs = observaciones()
    print(f"   {'plazo':>7} {'casos':>6} {'empezó':>7} {'días':>5} {'dif. hPa':>9}  intervalo 95%")
    for lead in plazos:
        r = presion_mecanismo(datos.get(lead, []), obs)
        if not r or r["dif"] is None or r["lo"] is None:
            print(f"   {lead:>5} m   — pocos datos")
            continue
        print(f"   {lead:>5} m {r['n']:>6} {r['lluvias']:>7} {r['dias']:>5} "
              f"{r['dif']:>+9.2f}  [{r['lo']:+.2f}, {r['hi']:+.2f}]")


# --------------------------------------------------------------- sección 9

def nubes_bajas_seccion(datos: dict, plazos=(15, 60)) -> None:
    print("\n9. NUBES BAJAS — ¿mejoran el pronóstico?")
    print("   (reescrita el 12/10/2026: ver el comentario de ganancia_cv)")
    print("   Solo de día: de noche el satélite confunde la nube con el suelo")
    print("   frío. Se registran desde el 5/10/2026.\n")
    print(_CABECERA_GANANCIA)
    vs = []
    for lead in plazos:
        filas = []
        for pf, y, f in datos.get(lead, []):
            x, dia = _num(f.get("nb_frac")), _num(f.get("nb_dia"))
            d = _dia_local(f.get("issued_utc", ""))
            if x is not None and dia == 1 and d is not None:
                filas.append({"p": pf, "y": y, "x": x, "dia": d})
        vs.append(_imprimir_ganancia(ganancia_cv(filas), f"{lead} min"))
    _explicar(vs, "la nube baja")

    # Lo que motivó la pregunta, para leerlo con los ojos: cuando el
    # pronóstico dijo menos de 20%, ¿llovió más bajo nube baja?
    con = [y for pf, y, f in datos.get(15, [])
           if pf < 0.2 and _num(f.get("nb_dia")) == 1
           and (_num(f.get("nb_frac")) or 0) >= 0.3]
    sin = [y for pf, y, f in datos.get(15, [])
           if pf < 0.2 and _num(f.get("nb_dia")) == 1
           and _num(f.get("nb_frac")) is not None
           and (_num(f.get("nb_frac")) or 0) < 0.3]
    if con or sin:
        print(f"\n   Para leer, no para decidir: a 15 min, de día, con pronóstico <20%,")
        print(f"   llovió {sum(con)} de {len(con)} veces bajo nube baja y "
              f"{sum(sin)} de {len(sin)} sin ella.")
        if con and len(con) + len(sin) and len(con) / (len(con) + len(sin)) > 0.7:
            print(f"   Ojo: el {len(con) / (len(con) + len(sin)):.0%} de los ratos marca nube baja;")
            print("   un umbral tan generoso diluye la señal.")


def _dif_estratos(estratos: dict, marcas: dict,
                  min_lluvias: int = 2) -> tuple[float | None, int]:
    """Skill de limpias menos degradadas, comparadas DENTRO de cada estrato.

    `estratos` es {estrato: array (p, y)} y `marcas` {estrato: array 0/1}. Se
    separan para poder barajar las marcas sin tocar los datos.

    Un estrato solo cuenta si los DOS grupos tienen lluvia dentro. Si uno de
    los dos cayo entero en un rato seco, su skill describe el rato, no el
    enlace, y compararlo seria volver a la trampa que esto existe para evitar.
    """
    pares = []
    for k, v in estratos.items():
        g = marcas[k]
        tasa = v[:, 1].mean()
        clima = tasa * (1 - tasa)
        if clima <= 0:
            continue
        lim, deg = v[g == 0], v[g == 1]
        if (len(lim) == 0 or len(deg) == 0
                or lim[:, 1].sum() < min_lluvias
                or deg[:, 1].sum() < min_lluvias):
            continue
        sk_l = 1 - brier(lim[:, 0], lim[:, 1]) / clima
        sk_d = 1 - brier(deg[:, 0], deg[:, 1]) / clima
        pares.append((min(len(lim), len(deg)), sk_l - sk_d))
    if not pares:
        return None, 0
    peso = sum(p for p, _ in pares)
    return sum(p * d for p, d in pares) / peso, len(pares)


def por_salud(datos: dict, lead: int = 15, dias: int = 7,
              barajadas: int = 400, semilla: int = 0,
              estrato_h: int = 24) -> None:
    """8-bis. ¿Una semana mala fue el enlace o el tiempo?

    Escrita por Alvaro el 28/09/2026 y corregida el mismo dia en dos puntos,
    los dos encontrados por test_evaluar.py:

    1. **Se compara dentro de la misma semana.** Medir cada fila contra la
       climatologia de su semana no bastaba: el propio skill depende de la tasa
       base, asi que con el MISMO pronostico, degradadas en una semana seca y
       limpias en una lluviosa daban "el enlace SI empeora" por 0.34.

       Y la semana resulto ser demasiado gruesa: el tiempo tambien cambia
       DENTRO de una semana, y una semana que empieza seca y termina lluviosa
       volvia a mezclar las dos cosas. El veredicto se calcula por DIA; la
       tabla semanal se queda porque se lee mejor.

    2. **Se mide el azar antes de dar veredicto.** Con marcas puestas al azar
       -sin relacion alguna con el pronostico- salia "pierden 0.083: el enlace
       SI empeora". Con 30-40 lluvias por grupo, un umbral fijo de 0.05 esta
       por debajo del ruido de muestreo. Ahora se barajan las marcas dentro de
       cada semana cientos de veces y solo cuenta la diferencia que el azar no
       produce.
    """
    filas, sin_marca = [], 0
    for p, y, d in datos.get(lead, []):
        g = _num(d.get("degradado"))
        if g is None or g != g:  # vacio o NaN: fila anterior al 20/09
            sin_marca += 1
            continue
        t = datetime.fromisoformat(str(d["valid_utc"]).replace("Z", "+00:00"))
        filas.append((t, float(p), int(y), 1 if g else 0))

    print("\n8-bis. ¿DEGRADADO O MAL TIEMPO?\n")
    print(f"   Plazo {lead} min. {sin_marca} filas sin marca (anteriores al")
    print("   commit del 20/09) no cuentan: esas semanas no se pueden juzgar aquí.")
    if not filas:
        print("   Todavía no hay filas marcadas.")
        return

    t0 = min(f[0] for f in filas)
    crudo: dict[int, list] = {}
    por_estrato: dict[int, list] = {}
    for t, p, y, g in filas:
        crudo.setdefault((t - t0).days // dias, []).append((p, y, g))
        h = int((t - t0).total_seconds() // 3600) // estrato_h
        por_estrato.setdefault(h, []).append((p, y, g))
    estratos = {k: np.array(v, dtype=float)[:, :2]
                for k, v in por_estrato.items()}
    marcas = {k: np.array(v, dtype=float)[:, 2].astype(int)
              for k, v in por_estrato.items()}

    print(f"\n   {'desde':>10} {'grupo':>10} {'casos':>6} {'lluvia':>7}"
          f" {'Brier':>7} {'skill':>7}")
    acum = {0: [0.0, 0.0, 0, 0], 1: [0.0, 0.0, 0, 0]}  # sse, clima, n, lluvias
    for k in sorted(crudo):
        v = np.array(crudo[k], dtype=float)
        tasa = v[:, 1].mean()
        clima = tasa * (1 - tasa)
        desde = (t0 + timedelta(days=k * dias)).date()
        for g, nombre in ((0, "limpia"), (1, "degradada")):
            s = v[v[:, 2] == g]
            if len(s) == 0:
                continue
            b = brier(s[:, 0], s[:, 1])
            llov = int(s[:, 1].sum())
            sk_txt = f"{1 - b / clima:>+7.3f}" if clima > 0 else f"{'—':>7}"
            nota = "  ← pocas lluvias" if llov < 5 else ""
            print(f"   {str(desde):>10} {nombre:>10} {len(s):>6} "
                  f"{s[:, 1].mean():>6.1%} {b:>7.4f} {sk_txt}{nota}")
            a = acum[g]
            a[0] += float(((s[:, 0] - s[:, 1]) ** 2).sum())
            a[1] += clima * len(s)
            a[2] += len(s)
            a[3] += llov

    print()
    for g, nombre in ((0, "limpias"), (1, "degradadas")):
        sse, cl, n, llov = acum[g]
        sk = 1 - sse / cl if cl > 0 else float("nan")
        tasa = llov / n if n else float("nan")
        print(f"   {nombre:>10}: {n:>5} casos, {llov:>3} con lluvia "
              f"({tasa:.1%}), skill contra su semana {sk:+.3f}")

    if acum[1][3] < 10 or acum[0][3] < 10:
        print("\n   Menos de 10 lluvias en algún grupo: todavía no se puede decir nada.")
        return

    dif, n_est = _dif_estratos(estratos, marcas)
    if dif is None:
        print("\n   Limpias y degradadas nunca coinciden en un mismo día con")
        print("   lluvia en los dos grupos. Así no se pueden comparar:")
        print("   cualquier diferencia sería el tiempo de cada día, no el enlace.")
        return

    # Cuanta diferencia produce el azar: barajar las marcas DENTRO de cada
    # semana conserva el tiempo de esa semana y el tamaño de cada grupo, y
    # rompe cualquier relacion real entre la marca y el pronostico.
    rng = np.random.default_rng(semilla)
    nulas = []
    for _ in range(barajadas):
        mezcladas = {k: rng.permutation(g) for k, g in marcas.items()}
        d, _n = _dif_estratos(estratos, mezcladas)
        if d is not None:
            nulas.append(d)
    lo, hi = (np.percentile(nulas, [2.5, 97.5]) if nulas
              else (float("-inf"), float("inf")))

    print(f"\n   Dentro del mismo día ({n_est} día"
          f"{'s' if n_est != 1 else ''} con lluvia en los dos grupos): "
          f"diferencia {dif:+.3f}.")
    print(f"   Por puro azar -barajando las marcas- sale entre {lo:+.3f} y "
          f"{hi:+.3f}")
    print("   el 95% de las veces.")
    if lo <= dif <= hi:
        print("\n   No se distingue del azar: el enlace no explica las semanas")
        print("   malas con estos datos. O no importa, o todavía hace falta")
        print("   más temporada para verlo.")
    elif dif > 0:
        print(f"\n   Fuera de lo que da el azar: las degradadas pierden {dif:.3f}")
        print("   de skill. El enlace SÍ empeora el pronóstico.")
    else:
        print(f"\n   Fuera del azar, pero al revés: las degradadas salen "
              f"{-dif:.3f} MEJOR.")
        print("   Raro: revisar qué marca 'degradado'.")

    t_l = acum[0][3] / acum[0][2]
    t_d = acum[1][3] / acum[1][2]
    if t_d > 1.5 * t_l:
        print("   Ojo: las corridas degradadas caen en ratos mucho más lluviosos")
        print("   que las limpias, incluso dentro de una semana. Enlace saturado")
        print("   y tormenta van juntos, y parte de la diferencia puede ser la")
        print("   tormenta, no el enlace.")


def por_tramo(ref: dict, dias: int = 7) -> None:
    """Skill por semanas, para que un cambio se pueda ver.

    Nace de un problema concreto del 21 de septiembre de 2026. Ese dia se
    cambio el reparto de pesos entre fuentes -el radar pasó de 0.26 a 0.0, los
    modelos de 0.44 a 0.65- y las secciones de arriba siguieron diciendo
    exactamente lo mismo: skill +0.302.

    No era un fallo. `predictions.csv` guarda la probabilidad que se publico en
    su momento, asi que las 4,500 filas historicas se calcularon con los pesos
    VIEJOS y ningun cambio en el motor puede alterarlas. El numero acumulado
    mide el pasado, y cuanto mas pasado hay, mas tarda en notarse el presente:
    con 4,500 casos dentro, una semana buena mueve el total tres milesimas.

    De ahi la unica forma de ver si un cambio sirvio: mirar por tramos. Y de
    ahi tambien la trampa que esto tiene, que conviene tener presente al leerlo:
    la lluvia no se reparte igual entre semanas. Una semana sin una sola
    tormenta tiene tasa base casi cero y el skill se vuelve inestable -de ahi
    que se imprima la tasa de cada tramo al lado-. Dos tramos solo son
    comparables si llovio parecido en los dos.
    """
    from datetime import datetime, timedelta

    filas = []
    for p, y, f in ref["datos"]:
        try:
            t = datetime.fromisoformat(f["issued_utc"])
        except (TypeError, ValueError, KeyError):
            continue
        filas.append((t, p, y))
    if len(filas) < 200:
        print(f"\n   Solo {len(filas)} casos con fecha; hacen falta más.")
        return

    filas.sort()
    fin = filas[-1][0]
    print(f"\n   Plazo {ref['lead']} min, en tramos de {dias} días "
          "(el más reciente abajo).\n")
    print(f"   {'desde':>12} {'casos':>7} {'lluvia':>8} {'Brier':>8} "
          f"{'clima':>8} {'skill':>8}")

    tramos = []
    inicio = fin - timedelta(days=dias)
    while True:
        bloque = [(p, y) for t, p, y in filas if inicio <= t < inicio + timedelta(days=dias)]
        if bloque:
            tramos.append((inicio, bloque))
        if inicio <= filas[0][0]:
            break
        inicio -= timedelta(days=dias)

    suma_sk, suma_n = 0.0, 0
    for inicio, bloque in sorted(tramos):
        ps = np.array([b[0] for b in bloque])
        ys = np.array([b[1] for b in bloque], dtype=float)
        base = float(ys.mean())
        b = brier(ps, ys)
        bc = brier(np.full_like(ps, base), ys)
        sk = 1.0 - b / bc if bc > 0 else 0.0
        # Con tasa base muy baja, la climatologia del tramo ya es casi
        # imbatible: acertar "no llueve" no cuesta nada. El skill de esas
        # semanas no es comparable con el de una semana de temporada.
        flojo = base < 0.07
        aviso = "  ← poca lluvia, no comparable" if flojo else ""
        if not flojo:
            suma_sk += sk * len(bloque)
            suma_n += len(bloque)
        print(f"   {inicio.strftime('%Y-%m-%d'):>12} {len(bloque):>7} "
              f"{_pc(base):>8} {b:>8.4f} {bc:>8.4f} {sk:>+8.3f}{aviso}")

    print("\n   Un cambio en el motor solo puede verse en los tramos")
    print("   POSTERIORES al día en que se hizo. Si el último tramo no")
    print("   mejora en dos o tres semanas con lluvia, el cambio no sirvió.")

    if suma_n:
        dentro = suma_sk / suma_n
        print("\n   POR QUÉ ESTOS NÚMEROS SON MENORES QUE EL DE LA SECCIÓN 3")
        print(f"     skill contra la climatología fija:     {ref['skill']:+.3f}")
        print(f"     skill dentro de cada semana:           {dentro:+.3f}")
        print(f"     lo que aporta saber la época del año:  "
              f"{ref['skill'] - dentro:+.3f}")
        print()
        print("   No es una contradicción, son dos preguntas. La sección 3")
        print("   compara contra 'llueve el 14.7% de los ratos, siempre', y")
        print("   las semanas de aquí van del 4.7% al 26.8%: parte del mérito")
        print("   es solo saber que agosto no es noviembre, que es cierto pero")
        print("   fácil. Cada tramo compara contra SU propia tasa, así que mide")
        print("   lo difícil: qué quince minutos, dentro de una semana lluviosa.")
        print()
        if ref["skill"] - dentro > 0.15:
            print("   Y aquí la mayor parte del mérito viene de la época del año.")
            print("   Eso es tasa base con pasos extra, no nowcasting.")
        else:
            print("   Aquí la mayor parte sobrevive al descuento, así que el")
            print("   sistema distingue de verdad ratos, no solo temporadas.")


def _pc(v) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


if __name__ == "__main__":
    sys.exit(main())
