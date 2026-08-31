"""La ventana de 1 hora: que detecte frentes sin confundirlos con la marea.

Bajar el umbral a 1.5 hPa/h tiene un riesgo concreto. La presion oscila sola
unos 7 hPa al dia por mareas atmosfericas termicas, y una sinusoide de esa
amplitud cambia hasta ~0.9 hPa por hora en su tramo mas inclinado. Eso queda
incomodamente cerca del umbral nuevo: sobre la serie cruda, el sistema
alertaria todas las tardes de un frente que no existe.

La correccion armonica deberia dejar eso en practicamente cero. Esta prueba
lo comprueba con series sinteticas, sin tocar la red.
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta, timezone

from nowcast import config, http, notify, pressure
# Aislamiento: sin esto, la prueba lee el barometro REAL de la malla si la
# maquina lo tiene, y la serie sintetica que fabricamos aqui queda pisada por
# la presion de verdad. Paso en el Raspberry: la suite pasaba en una maquina
# sin sensor y fallaba en la que si lo tiene.
config.CLIMA_DB = ""

ok = True


def chk(nombre: str, condicion: bool, detalle: str = "") -> None:
    global ok
    print(f"  {'PASA' if condicion else 'FALLA'}  {nombre}"
          + (f"  [{detalle}]" if detalle else ""))
    if not condicion:
        ok = False


AMPLITUD = 3.5          # +-3.5 hPa = 7 hPa de recorrido, el caso realista
BASE = 1013.0

# El reloj se congela. La primera version usaba la hora real y fallaba segun
# el minuto en que se ejecutara: la serie se construye en horas exactas, pero
# fetch() pregunta por 'ahora' con minutos, y la busqueda del valor mas
# cercano caia en una muestra o en la siguiente segun el momento. Paso de
# verdad: la prueba pasaba en una maquina y fallaba en el Raspberry.
AHORA = datetime(2026, 8, 26, 18, 0, tzinfo=timezone.utc)


class Reloj(datetime):
    """datetime con now() fijo. Hereda para que fromisoformat siga sirviendo."""

    @classmethod
    def now(cls, tz=None):
        return AHORA.astimezone(tz) if tz else AHORA.replace(tzinfo=None)


pressure.datetime = Reloj


def serie(frente_hpa_h: float = 0.0, horas_de_frente: int = 0) -> dict:
    """Respuesta sintetica de Open-Meteo: marea diaria + frente opcional.

    El frente entra como una rampa que termina en AHORA y se mantiene
    despues: asi la caida es la misma se mire la muestra anterior o la
    posterior, y la prueba no depende de donde caiga el redondeo.
    """
    tiempos, valores = [], []
    for h in range(-30, 49):
        t = AHORA + timedelta(hours=h)
        # marea: componentes de 24 h y 12 h, como la real
        hora_dia = t.hour + t.minute / 60
        v = (BASE
             + AMPLITUD * math.cos(2 * math.pi * (hora_dia - 4) / 24)
             + 0.8 * math.cos(2 * math.pi * hora_dia / 12))
        if frente_hpa_h and h >= -horas_de_frente:
            # La rampa NO se aplana en h=0: sigue bajando un par de horas
            # mas. Un frente real no se detiene en seco al llegar la hora en
            # que uno mira, y aplanarla ahi hacia que 'ahora' y 'hace una
            # hora' cayeran en el mismo valor cuando el redondeo subia.
            avance = min(h + horas_de_frente, horas_de_frente + 3)
            v -= frente_hpa_h * avance
        tiempos.append(t.strftime("%Y-%m-%dT%H:%M"))
        valores.append(round(v, 2))
    return {"hourly": {"time": tiempos, "pressure_msl": valores,
                       "surface_pressure": [v - 200 for v in valores]}}


def estado(**kw) -> pressure.PressureState:
    datos = serie(**kw)
    http.get_json = lambda url, **k: datos
    return pressure.fetch()


print("A. La marea atmosferica no debe disparar nada")
s = estado()
chk("el ciclo diario se detecta", s.daily_cycle_amplitude >= 5.0,
    f"{s.daily_cycle_amplitude} hPa de recorrido")
chk("change_1h queda casi en cero",
    s.change_1h is not None and abs(s.change_1h) < 0.5,
    f"{s.change_1h} hPa/h")
chk("no se declara caida rapida", not s.is_falling_fast)
chk("no se declara caida en curso", not s.is_falling_now)

print("\n   Sin la correccion, esto es lo que habria pasado:")
crudo = serie()["hourly"]["pressure_msl"]
# la pendiente maxima de la marea, en la serie cruda
peor = min(crudo[i + 1] - crudo[i] for i in range(len(crudo) - 1))
print(f"     la marea sola cambia hasta {peor:.2f} hPa en una hora")
chk("y ese valor si habria cruzado el umbral",
    abs(peor) > config.PRESSURE_DROP_1H * 0.5,
    f"umbral {config.PRESSURE_DROP_1H}")

print("\nB. Un frente de verdad si se detecta")
s = estado(frente_hpa_h=2.0, horas_de_frente=2)
chk("change_1h refleja la caida",
    s.change_1h is not None and s.change_1h <= -1.5, f"{s.change_1h} hPa/h")
chk("se declara caida rapida", s.is_falling_fast)
chk("la velocidad se reporta",
    s.velocidad_hpa_h is not None and s.velocidad_hpa_h > 0,
    f"{s.velocidad_hpa_h} hPa/h" if s.velocidad_hpa_h else "None")

print("\nC. Una caida lenta no dispara la alerta rapida")
# 3 hPa repartidos en 12 horas: real, pero no es un frente.
s = estado(frente_hpa_h=0.25, horas_de_frente=12)
chk("no se declara caida rapida", not s.is_falling_fast, f"{s.change_1h} hPa/h")

print("\nD. El aviso se manda una vez, no en cada corrida")
enviados: list[str] = []
marcas: dict[str, bool] = {}
notify.send = lambda titulo, cuerpo, **kw: (enviados.append(titulo), True)[1]
notify._cooldown_ok_hours = lambda clave, horas: not marcas.get(clave)
notify._mark = lambda clave: marcas.__setitem__(clave, True)
config.NTFY_TOPIC_SALUD = "canal-de-prueba"

s = estado(frente_hpa_h=2.0, horas_de_frente=2)
notify.maybe_pressure_alert(s)
primera = len(enviados)
notify.maybe_pressure_alert(s)
notify.maybe_pressure_alert(s)
chk("la primera vez avisa", primera >= 1, f"{primera} aviso(s)")
chk("las siguientes callan por el cooldown", len(enviados) == primera,
    f"{len(enviados)} en total")
chk("el aviso es el de caida rapida",
    any("rapida" in t.lower() for t in enviados), str(enviados))

print("\nE. Sin canal no revienta")
config.NTFY_TOPIC_SALUD = ""
chk("devuelve False sin lanzar excepcion",
    notify.maybe_pressure_alert(s) is False)

print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
