"""Avisos de tormenta electrica: distancias, tendencia y transiciones.

Lo que hay que probar aqui no es que detecte rayos -eso ya lo hace el GLM-
sino que no maree a nadie. Un aviso cada quince minutos mientras dura una
tormenta seria peor que no avisar: se silencia el canal y se pierde tambien
lo util. Por eso casi todas las comprobaciones son sobre cuando NO avisa.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from nowcast import config, lightning

ok = True


def chk(nombre: str, condicion: bool, detalle: str = "") -> None:
    global ok
    print(f"  {'PASA' if condicion else 'FALLA'}  {nombre}"
          + (f"  [{detalle}]" if detalle else ""))
    if not condicion:
        ok = False


# --- utilidades para fabricar historiales -------------------------------

def punto_a(km_al_norte: float) -> list:
    """Un destello a N km al norte de la ubicacion configurada."""
    return [round(config.LAT + km_al_norte / 111.0, 4), config.LON, 1]


def historial(*distancias_km) -> dict:
    """Bloques de 15 min, del mas viejo al mas nuevo. None = sin rayos."""
    base = datetime(2026, 8, 26, 20, 0, tzinfo=timezone.utc)
    bloques = []
    for i, d in enumerate(distancias_km):
        t = (base + timedelta(minutes=15 * i)).isoformat()
        puntos = [] if d is None else [punto_a(d)]
        bloques.append({"t": t, "puntos": puntos, "total": len(puntos)})
    return {"bloques": bloques}


print("A. Distancias")
d = lightning._dist_km(config.LAT, config.LON)
chk("la ubicacion esta a 0 km de si misma", d < 0.001, f"{d:.4f} km")
d50 = lightning._dist_km(config.LAT + 50 / 111.0, config.LON)
chk("50 km al norte da ~50 km", 49 < d50 < 51, f"{d50:.1f} km")

print("\nB. Lectura de la tormenta")
t = lightning.evaluar(historial(None, None, None, None))
chk("sin rayos: despejado", t.fase == "despejado" and t.dist_cercano_km is None)

t = lightning.evaluar(historial(200, 200, 200, 200))
chk("rayos muy lejos: despejado", t.fase == "despejado", t.fase)

t = lightning.evaluar(historial(90, 75, 60, 45))
chk("celda que se acerca: fase acercandose", t.fase == "acercandose", t.fase)
chk("la tendencia es negativa", t.tendencia_km is not None and t.tendencia_km < 0,
    f"{t.tendencia_km:.1f} km" if t.tendencia_km else "None")

t = lightning.evaluar(historial(45, 60, 75, 90))
chk("celda que se aleja: no dice acercandose", not t.acercandose, t.fase)

t = lightning.evaluar(historial(60, 40, 25, 12))
chk("celda encima: fase encima", t.fase == "encima", t.fase)
chk("cuenta los destellos cercanos", t.destellos_cerca == 1, str(t.destellos_cerca))

print("\nC. Histeresis: lo que evita el bombardeo")
# Una celda oscilando entre 22 y 30 km cruzaria el umbral de 25 km una y otra
# vez. Con histeresis, una vez dentro se sigue considerando "encima" hasta
# los 35 km, asi que no hay ida y vuelta.
t = lightning.evaluar(historial(22, 30, 22, 30), fase_previa="encima")
chk("una celda que oscila no sale de 'encima'", t.fase == "encima", t.fase)

t = lightning.evaluar(historial(30, 30, 30, 30), fase_previa="despejado")
chk("a 30 km sin venir de cerca no se declara 'encima'",
    t.fase != "encima", t.fase)

# Salir de verdad si que debe poder.
t = lightning.evaluar(historial(40, 60, 90, 120), fase_previa="encima")
chk("una celda que se va de verdad sale de 'encima'", t.fase != "encima", t.fase)

print("\nD. El 'ya paso' no se declara antes de tiempo")
# Un solo bloque sin rayos son 15 minutos: no basta para el "ya paso".
t = lightning.evaluar(historial(20, 20, 20, None), fase_previa="encima")
chk("15 min de silencio no bastan", t.fase != "despejado", t.fase)
t = lightning.evaluar(historial(20, None, None, None), fase_previa="encima")
chk("45 min de silencio si bastan", t.fase == "despejado",
    f"{t.fase}, {t.minutos_sin_actividad} min sin actividad")

print("\nE. Transiciones: solo se avisa de lo que cambia")
# Se sustituye el envio real por un registro, y el estado por un dict en
# memoria: la prueba no toca la red ni el disco.
from nowcast import notify   # noqa: E402

enviados: list[tuple[str, str]] = []
memoria = {"fase_tormenta": "despejado"}

notify.send = lambda titulo, cuerpo, **kw: (enviados.append((titulo, cuerpo)), True)[1]
notify._fase_previa = lambda: memoria["fase_tormenta"]
notify._guardar_fase = lambda f: memoria.__setitem__("fase_tormenta", f)
notify._cooldown_ok_hours = lambda clave, horas: True
notify._mark = lambda clave: None
config.NTFY_TOPIC_SALUD = "canal-de-prueba"

secuencia = [
    ("nada", historial(None, None, None, None), 0),
    ("se acerca", historial(90, 75, 60, 45), 1),
    ("sigue acercandose", historial(75, 60, 45, 40), 0),   # misma fase: callado
    ("llega", historial(60, 40, 25, 12), 1),
    ("sigue encima", historial(20, 15, 12, 10), 0),        # misma fase: callado
    ("oscila en el borde", historial(22, 30, 26, 31), 0),  # histeresis: callado
    ("se va", historial(40, 80, 130, 200), 1),             # esto si es noticia
]

for etiqueta, datos, esperados in secuencia:
    antes = len(enviados)
    t = lightning.evaluar(datos, memoria["fase_tormenta"])
    notify.maybe_storm_alert(t)
    nuevos = len(enviados) - antes
    chk(f"{etiqueta}: {esperados} aviso(s)", nuevos == esperados,
        f"mando {nuevos}, fase {t.fase}")

print("\n   Lo que le habria llegado al telefono:")
for titulo, cuerpo in enviados:
    print(f"     - {titulo}")
    for linea in cuerpo.splitlines():
        if linea.strip():
            print(f"         {linea}")

chk("tres avisos en toda la tormenta, no uno por corrida",
    len(enviados) == 3, f"{len(enviados)} avisos")

print("\nF. Sin canal configurado no revienta")
config.NTFY_TOPIC_SALUD = ""
memoria["fase_tormenta"] = "despejado"
t = lightning.evaluar(historial(60, 40, 25, 12), "despejado")
chk("devuelve False sin lanzar excepcion", notify.maybe_storm_alert(t) is False)

print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
