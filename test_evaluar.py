"""El código que MIDE, probado igual que el que predice.

Hasta el 28 de septiembre de 2026 `evaluar.py` no tenía ni una prueba, y le
costó al proyecto tres errores seguidos, los tres en código de medición:

- el diagnóstico de rumbo leía enteros de GOES sin desempaquetar,
- el mismo diagnóstico recortaba la lista de días antes de filtrar carpetas,
- y la sección 5 comparaba la mezcla sobre todos sus casos contra una fuente
  calificada solo en una semana seca, y anunció que una fuente sin ninguna
  información (separación 0.1%) era mejor que la mezcla.

Los tres tienen la misma forma: una medida que nadie había comprobado contra
un caso cuya respuesta se conoce. Un pronóstico malo se nota; una medida mala
convence, porque es la herramienta con la que se decide qué está mal.
"""
from __future__ import annotations

import contextlib
import io
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

import evaluar

ok = True


def chk(nombre: str, condicion: bool, detalle: str = "") -> None:
    global ok
    print(f"  {'PASA' if condicion else 'FALLA'}  {nombre}"
          + (f"  [{detalle}]" if detalle else ""))
    if not condicion:
        ok = False


def salida_de(f, *args, **kw) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        f(*args, **kw)
    return buf.getvalue()


rng = np.random.default_rng(11)
T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)

print("A. El aviso falso del 28/09: una fuente calificada en una semana seca")
# Se reproduce el caso real. La mezcla separa bien y se califica en todo el
# historial, con temporada de lluvias dentro. La fuente "suelta" no distingue
# nada, pero solo tiene datos de una semana con 3% de lluvia: su Brier sale
# bajísimo porque en una semana seca decir "poco" siempre acierta.
datos = []
for i in range(4000):
    lluvioso = i < 3200                       # agosto-septiembre: temporada
    y = 1 if rng.random() < (0.18 if lluvioso else 0.03) else 0
    pf = float(np.clip((0.55 if y else 0.10) + rng.normal(0, 0.12), 0, 1))
    fila = {"valid_utc": (T0 + timedelta(minutes=15 * i)).isoformat(),
            "p_models": pf, "p_radar": 0.0, "p_ir": pf}
    if not lluvioso:
        # la fuente sin información, solo en la semana seca
        fila["p_suelta"] = 0.03
    datos.append((pf, y, fila))

f = evaluar.por_fuente(datos)
chk("cada fuente trae el Brier de la mezcla en sus mismos casos",
    all("brier_mezcla" in d for d in f.values()))

# La prueba de verdad: la comparación vieja habría dicho que la fuente gana.
suelta_ys = np.array([y for _p, y, fl in datos if "p_suelta" in fl], float)
suelta_ps = np.full(len(suelta_ys), 0.03)
mezcla_todo = evaluar.brier(np.array([d[0] for d in datos]),
                            np.array([d[1] for d in datos], float))
b_suelta = evaluar.brier(suelta_ps, suelta_ys)
print(f"     suelta en su semana seca: {b_suelta:.4f}   "
      f"mezcla en todo: {mezcla_todo:.4f}")
chk("con la comparación vieja, la fuente sin información 'ganaba'",
    b_suelta < mezcla_todo, "así salió el aviso falso")
mezcla_seca = evaluar.brier(
    np.array([d[0] for d in datos if "p_suelta" in d[2]]), suelta_ys)
chk("pero sobre los MISMOS casos, la mezcla no pierde por mucho",
    mezcla_seca < b_suelta + 0.01, f"mezcla {mezcla_seca:.4f}")

print("\nB. La sección 5 ya no anuncia ganadores sin información")
# Una fuente sin separación no compite, aunque tenga mejor Brier en sus
# casos: sacar buen Brier sin distinguir nada es haber caído en una semana
# fácil.
sin_info = {"sin_info": {"n": 800, "brier": 0.030, "brier_mezcla": 0.040,
                         "lluvia": 0.03, "separacion": 0.001}}
chk("una fuente con 0.1% de separación no cuenta como ganadora",
    not evaluar.ganadoras(sin_info))
con_info = {"buena": {"n": 800, "brier": 0.030, "brier_mezcla": 0.040,
                      "lluvia": 0.15, "separacion": 0.30}}
chk("pero una que separa y gana en sus casos, sí",
    [n for n, _ in evaluar.ganadoras(con_info)] == ["buena"])
chk("y la tabla real de A no produce ganadores falsos",
    not evaluar.ganadoras(f), str([n for n, _ in evaluar.ganadoras(f)]))

print("\nC. 8-bis: separa el efecto del enlace del efecto del tiempo")


def semana(i0, n, tasa, degradada_mala):
    salida = {}
    filas = []
    for i in range(n):
        g = 1 if i % 3 == 0 else 0            # un tercio degradadas
        y = 1 if rng.random() < tasa else 0
        ruido = 0.30 if (g and degradada_mala) else 0.08
        p = float(np.clip((0.6 if y else 0.1) + rng.normal(0, ruido), 0, 1))
        fila = {"valid_utc": (T0 + timedelta(minutes=15 * (i0 + i))).isoformat(),
                "degradado": str(g)}
        filas.append((p, y, fila))
    return filas


malo = {15: semana(0, 700, 0.20, True) + semana(700, 700, 0.20, True)}
txt = salida_de(evaluar.por_salud, malo)
chk("detecta cuando el enlace SÍ empeora el pronóstico",
    "SÍ empeora" in txt, txt.strip().splitlines()[-1])

bueno = {15: semana(0, 700, 0.20, False) + semana(700, 700, 0.20, False)}
txt = salida_de(evaluar.por_salud, bueno)
chk("y cuando no lo explica", "No se distingue del azar" in txt,
    txt.strip().splitlines()[-1])

print("\nD. 8-bis no juzga con pocos datos ni con filas sin marca")
viejas = {15: [(0.2, 0, {"valid_utc": T0.isoformat(), "degradado": ""})
               for _ in range(50)]}
txt = salida_de(evaluar.por_salud, viejas)
chk("las filas sin marca no cuentan", "Todavía no hay filas marcadas" in txt)
chk("y lo dice", "50 filas sin marca" in txt)

pocas = {15: semana(0, 60, 0.05, True)}
txt = salida_de(evaluar.por_salud, pocas)
chk("con menos de 10 lluvias por grupo no concluye",
    "todavía no se puede decir nada" in txt)

print("\nD-bis. Marcas puestas al azar no producen un veredicto")
# El hallazgo del mismo 28/09: con marcas SIN relacion con el pronostico, el
# umbral fijo de 0.05 anunciaba "el enlace SI empeora" por 0.083. Se repite
# con varias semillas para que no pase por suerte.
falsos = 0
for semilla in range(8):
    r = np.random.default_rng(100 + semilla)
    filas = []
    for i in range(2000):
        y = 1 if r.random() < 0.15 else 0
        p = float(np.clip((0.6 if y else 0.1) + r.normal(0, 0.12), 0, 1))
        filas.append((p, y, {
            "valid_utc": (T0 + timedelta(minutes=15 * i)).isoformat(),
            "degradado": "1" if r.random() < 0.25 else "0"}))
    t = salida_de(evaluar.por_salud, {15: filas})
    if "SÍ empeora" in t or "MEJOR" in t:
        falsos += 1
chk("en 8 historiales con marcas al azar, a lo sumo un falso veredicto",
    falsos <= 1, f"{falsos} de 8")

print("\nE. Una semana seca no vuelve buena a la versión que cayó en ella")
# La razón de medir contra la climatología de CADA semana. Si las degradadas
# caen todas en una semana seca, su Brier crudo sale mejor sin serlo.
seca = semana(0, 700, 0.03, False)
lluviosa = semana(700, 700, 0.30, False)
for p, y, fl in seca:
    fl["degradado"] = "1"
for p, y, fl in lluviosa:
    fl["degradado"] = "0"
# mismo ruido en las dos: el enlace no hace nada; solo cambia el tiempo
mezclado = {15: seca + lluviosa}
txt = salida_de(evaluar.por_salud, mezclado)
chk("no atribuye al enlace lo que fue el tiempo",
    "SÍ empeora" not in txt and "MEJOR" not in txt,
    txt.strip().splitlines()[-1])

print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
