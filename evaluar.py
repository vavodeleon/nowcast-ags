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


# Las dos fuentes de verdad que no salieron de un modelo numérico.
INDEPENDIENTES = {"manual", "malla"}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def cargar() -> dict[int, list[tuple[float, int, dict]]]:
    """Cruza predicciones con observaciones. Devuelve {lead: [(p, llovio, fila)]}."""
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
    return [d for d in datos if es_independiente(d[2].get("_verdad", ""))]


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
                        ("p_radar", "radar"),
                        # El infrarrojo antes del ajuste por yunque. Aparece al
                        # lado del ajustado a proposito: la pregunta no es si el
                        # ajuste es razonable -lo es, y tiene fisica detras- sino
                        # si en ESTOS casos separa mejor. Si no, sobra.
                        ("p_ir_crudo", "infrarrojo (sin ajuste)")):
        pares = [(_num(f.get(col)), y) for _p, y, f in datos]
        pares = [(p, y) for p, y in pares if p is not None]
        if len(pares) < 15:
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


def presion_vs_lluvia(datos: list, col: str = "pres_3h") -> dict | None:
    """La hipótesis abierta: ¿la caída de presión anticipa la lluvia AQUÍ?

    Nació de un caso: el 30 de agosto el aviso de presión salió 30 minutos
    antes de la lluvia y 90 antes del aviso de lluvia. Un caso no es
    evidencia; esto la busca.
    """
    pares = [(_num(f.get(col)), y) for _p, y, f in datos]
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
        "presion_si_llovio": float(con.mean()),
        "presion_si_no": float(sin.mean()),
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

    print("\n5. QUÉ FUENTE CARGA LA INFORMACIÓN")
    fuentes = por_fuente(ref["datos"])
    if fuentes:
        print(f"\n   {'fuente':>12} {'casos':>7} {'Brier':>8} {'separación':>11}")
        for nombre, d in sorted(fuentes.items(), key=lambda kv: kv[1]["brier"]):
            print(f"   {nombre:>12} {d['n']:>7} {d['brier']:>8.4f} "
                  f"{_pc(d['separacion']):>11}")
        mejor_sola = min(fuentes.values(), key=lambda d: d["brier"])
        nombre_mejor = min(fuentes, key=lambda k: fuentes[k]["brier"])
        if mejor_sola["brier"] < ref["brier"] - 0.001:
            print(f"\n   La mezcla (Brier {ref['brier']:.4f}) es PEOR que")
            print(f"   {nombre_mejor} sola ({mejor_sola['brier']:.4f}).")
            print("   Los pesos no se han movido lo suficiente hacia la fuente buena.")
        else:
            print(f"\n   La mezcla ({ref['brier']:.4f}) mejora a la mejor fuente sola.")
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

    print("\n7. LA HIPÓTESIS ABIERTA: ¿la presión anticipa la lluvia aquí?")
    print("   Se mira en cada plazo y en dos ventanas: un frente se anuncia")
    print("   antes, así que la señal debería verse mejor a plazos largos.\n")
    print(f"   {'plazo':>7} {'ventana':>9} {'casos':>7} {'llovió':>9} "
          f"{'no llovió':>10} {'dif.':>8}")
    for col, etiqueta in (("pres_1h", "1 h"), ("pres_3h", "3 h")):
        for r in informes:
            d = presion_vs_lluvia(r["datos"], col)
            if not d or not d.get("suficientes"):
                continue
            print(f"   {r['lead']:>5} m {etiqueta:>9} {d['n']:>7} "
                  f"{d['presion_si_llovio']:>+9.2f} {d['presion_si_no']:>+10.2f} "
                  f"{d['diferencia']:>+8.2f}")
    print()
    pr = presion_vs_lluvia(ref["datos"])
    if pr and pr.get("suficientes"):
        print(f"\n   Cambio de presión en 3 h, según lo que pasó ({pr['n']} casos):")
        print(f"     cuando llovió:    {pr['presion_si_llovio']:+.2f} hPa")
        print(f"     cuando no llovió: {pr['presion_si_no']:+.2f} hPa")
        print(f"     diferencia:       {pr['diferencia']:+.2f} hPa")
        if abs(pr["diferencia"]) < 0.3:
            print("\n   Diferencia pequeña: por ahora no parece anticipar nada.")
        else:
            print("\n   Hay señal. Merece entrar como fuente del pronóstico.")
    else:
        n = pr.get("n", 0) if pr else 0
        print(f"\n   Solo {n} casos con presión registrada; hacen falta ~30.")
        print("   Las columnas se añadieron el 31 de agosto; hay que esperar.")

    print("\n7-bis. EL AJUSTE POR YUNQUE, ¿SIRVIÓ?")
    ajuste(ref)

    print("\n8. ¿MEJORÓ DESDE EL ÚLTIMO CAMBIO? — el número acumulado no lo dice")
    por_tramo(ref)

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


def ajuste(ref: dict) -> None:
    """Las dos versiones del infrarrojo, cara a cara sobre los mismos casos.

    El 21 de septiembre de 2026 se le añadio al infrarrojo un ajuste por
    compacidad del campo y por enfriamiento local, para no confundir una celda
    con el yunque de una tormenta lejana. Tenia motivo -separacion -19.3% en las
    correcciones humanas- y fisica que lo respalda.

    Nada de eso es una comprobacion. Por eso cada fila guarda las dos
    probabilidades, la ajustada y la cruda, y aqui se comparan. Si el ajuste no
    separa mejor, hay que quitarlo en vez de defenderlo.
    """
    pares = [(_num(f.get("p_ir")), _num(f.get("p_ir_crudo")), y)
             for _p, y, f in ref["datos"]]
    pares = [(a, b, y) for a, b, y in pares if a is not None and b is not None]
    if len(pares) < 40:
        print(f"\n   Solo {len(pares)} filas con las dos versiones; el ajuste")
        print("   empezó a registrarse el 21/09/2026 y hacen falta unos días.")
        return

    ys = np.array([p[2] for p in pares], dtype=float)
    aj = np.array([p[0] for p in pares])
    cr = np.array([p[1] for p in pares])
    if not (ys == 1).any() or not (ys == 0).any():
        print("\n   Todavía no hay casos de los dos tipos.")
        return

    def sep(v):
        return float(v[ys == 1].mean() - v[ys == 0].mean())

    print(f"\n   {len(pares)} filas, plazo {ref['lead']} min\n")
    print(f"   {'versión':>26} {'Brier':>8} {'separación':>12}")
    print(f"   {'con ajuste':>26} {brier(aj, ys):>8.4f} {_pc(sep(aj)):>12}")
    print(f"   {'sin ajuste (brillo a secas)':>26} {brier(cr, ys):>8.4f} "
          f"{_pc(sep(cr)):>12}")
    mejor = sep(aj) - sep(cr)
    print(f"\n   El ajuste cambia la separación en {mejor * 100:+.1f} puntos.")
    if mejor > 0.03:
        print("   Sirve. Distinguir la forma de la nube aporta de verdad.")
    elif mejor < -0.03:
        print("   NO sirve: empeora. Hay que quitarlo, no afinarlo.")
    else:
        print("   Indistinguible por ahora. Con más casos se verá; si se queda")
        print("   aquí, es complejidad que no se paga y conviene retirarla.")


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
