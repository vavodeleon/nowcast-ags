"""El barometro de la malla como fuente de presion.

El riesgo central de esta integracion es de unidades: el sensor mide presion
de estacion (~815 hPa a 1880 m) y Open-Meteo entrega presion reducida a nivel
del mar (~1015 hPa). Mezclarlas sin convertir daria un salto de 200 hPa, que
el detector de frentes leeria como una caida catastrofica y mandaria una
alerta de salud aterradora por un error de aritmetica.

Casi todas las comprobaciones de aqui vigilan eso.
"""
from __future__ import annotations

import math
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

from nowcast import barometro, config, http, notify, pressure

ok = True


def chk(nombre: str, condicion: bool, detalle: str = "") -> None:
    global ok
    print(f"  {'PASA' if condicion else 'FALLA'}  {nombre}"
          + (f"  [{detalle}]" if detalle else ""))
    if not condicion:
        ok = False


AHORA = datetime(2026, 8, 26, 18, 0, tzinfo=timezone.utc)
BASE_ESTACION = 814.9      # lo que marca de verdad el sensor de Álvaro
BASE_MSL = 1013.0
AMPLITUD = 3.5


class Reloj(datetime):
    @classmethod
    def now(cls, tz=None):
        return AHORA.astimezone(tz) if tz else AHORA.replace(tzinfo=None)


pressure.datetime = Reloj
barometro.datetime = Reloj

tmp = tempfile.mkdtemp()
RUTA = os.path.join(tmp, "clima.db")


def marea(t: datetime) -> float:
    h = t.hour + t.minute / 60
    return (AMPLITUD * math.cos(2 * math.pi * (h - 4) / 24)
            + 0.8 * math.cos(2 * math.pi * h / 12))


def sembrar(horas: float = 30, cadencia_s: int = 60, caida_1h: float = 0.0,
            edad_min: float = 0.5, ruido: float = 0.0) -> None:
    """Crea una clima.db como la del servicio de Meshtastic."""
    if os.path.exists(RUTA):
        os.remove(RUTA)
    con = sqlite3.connect(RUTA)
    con.execute("create table observaciones (ts integer primary key, "
                "nodo text, temp_c real, humedad real, presion real)")
    fin = AHORA - timedelta(minutes=edad_min)
    n = int(horas * 3600 / cadencia_s)
    filas = []
    for i in range(n):
        t = fin - timedelta(seconds=cadencia_s * (n - 1 - i))
        # la marea va en unidades de nivel del mar; al sensor le llega
        # dividida por el mismo factor con el que luego se recupera
        v = BASE_ESTACION + marea(t) / (BASE_MSL / BASE_ESTACION)
        atras_h = (fin - t).total_seconds() / 3600
        if caida_1h and atras_h <= 1:
            v -= caida_1h * (1 - atras_h) / (BASE_MSL / BASE_ESTACION)
        if ruido:
            v += ruido * math.sin(i * 2.399)     # deterministico, no aleatorio
        filas.append((int(t.timestamp()), "!0acb66ac", 25.0, None, round(v, 4)))
    con.executemany("insert into observaciones values (?,?,?,?,?)", filas)
    con.commit()
    con.close()


def serie_modelo() -> dict:
    tiempos, valores = [], []
    for h in range(-30, 49):
        t = AHORA + timedelta(hours=h)
        valores.append(round(BASE_MSL + marea(t), 2))
        tiempos.append(t.strftime("%Y-%m-%dT%H:%M"))
    return {"hourly": {"time": tiempos, "pressure_msl": valores,
                       "surface_pressure": [v - 200 for v in valores]}}


def estado() -> pressure.PressureState:
    http.get_json = lambda url, **k: serie_modelo()
    return pressure.fetch()


config.CLIMA_DB = RUTA

print("A. Lectura de la base")
sembrar()
serie = barometro.leer()
r = barometro.resumen(serie)
chk("lee las observaciones", len(serie) > 1000, f"{len(serie)} muestras")
chk("detecta la cadencia de 60 s", r["cadencia_s"] == 60, f"{r['cadencia_s']} s")
chk("la considera fresca", r["edad_min"] < 5, f"{r['edad_min']} min")
chk("la da por utilizable", barometro.utilizable(serie))
chk("los valores son de estacion, no de nivel del mar",
    800 < serie[-1][1] < 830, f"{serie[-1][1]:.1f} hPa")

print("\nB. El factor de conversión")
factor = barometro.factor_a_nivel_del_mar(serie, BASE_MSL)
chk("se estima solo, sin altitud supuesta",
    factor is not None and 1.20 < factor < 1.28, f"{factor:.4f}" if factor else "None")
chk("un factor absurdo se rechaza",
    barometro.factor_a_nivel_del_mar(serie, 400.0) is None)

print("\nC. Sin frente, el sensor no inventa uno")
s = estado()
chk("la fuente es el sensor", s.fuente == "sensor local", s.fuente)
chk("la presión se publica a nivel del mar",
    s.now_msl is not None and 995 < s.now_msl < 1035, f"{s.now_msl} hPa")
chk("y la de estación también se conserva",
    s.now_surface is not None and 800 < s.now_surface < 830, f"{s.now_surface} hPa")
chk("change_1h cerca de cero pese a la marea",
    s.change_1h is not None and abs(s.change_1h) < 0.6, f"{s.change_1h} hPa")
chk("no se declara caída rápida", not s.is_falling_fast)

print("\nD. Un frente real medido por el sensor")
sembrar(caida_1h=2.5)
s = estado()
chk("change_1h lo refleja",
    s.change_1h is not None and s.change_1h <= -1.5, f"{s.change_1h} hPa")
chk("se declara caída rápida", s.is_falling_fast)
chk("la magnitud está en unidades de nivel del mar",
    s.change_1h is not None and -4.0 < s.change_1h < -1.5,
    f"{s.change_1h} hPa (el sensor midió ~-2.0 de estación)")

print("\nE. Ruido del sensor: la mediana lo absorbe")
sembrar(ruido=0.35)          # +-0.35 hPa entre muestras, generoso para un BMP280
s = estado()
chk("el ruido no dispara la alerta", not s.is_falling_fast, f"{s.change_1h} hPa")

print("\nF. Cuando el sensor no sirve, se vuelve al modelo sin ruido")
sembrar(edad_min=120)        # el nodo lleva dos horas sin reportar
s = estado()
chk("un sensor viejo no se usa", s.fuente == "modelo", s.fuente)
chk("pero el estado sigue completo", s.change_1h is not None and s.now_msl is not None,
    f"{s.change_1h} hPa, {s.now_msl} hPa")
chk("y se deja constancia de por qué", bool(s.sensor.get("edad_min")),
    str(s.sensor))

sembrar(horas=0.5)           # apenas media hora de historia
s = estado()
chk("con poca historia tampoco", s.fuente == "modelo", s.fuente)

os.remove(RUTA)
s = estado()
chk("sin base de datos no revienta", s.fuente == "modelo" and s.now_msl is not None)
chk("y lo dice sin muestras", s.sensor.get("muestras") == 0, str(s.sensor))

print("\nG. Una base corrupta tampoco tumba el pronóstico")
with open(RUTA, "wb") as fh:
    fh.write(b"esto no es una base de datos")
s = estado()
chk("se ignora y se sigue", s.fuente == "modelo" and s.now_msl is not None)

print("\nH. El caso que este archivo existe para impedir")
# Si alguien quitara la conversion, change_1h saldria de ~200 hPa y la
# alerta diria algo aterrador. Se comprueba que eso no puede pasar.
sembrar()
s = estado()
chk("ningún cambio absurdo llega al estado",
    all(v is None or abs(v) < 30
        for v in (s.change_1h, s.change_3h, s.change_6h, s.change_24h)),
    f"1h {s.change_1h}, 3h {s.change_3h}, 24h {s.change_24h}")

enviados: list[str] = []
notify.send = lambda titulo, cuerpo, **kw: (enviados.append(cuerpo), True)[1]
notify._cooldown_ok_hours = lambda c, h: True
notify._mark = lambda c: None
config.NTFY_TOPIC_SALUD = "canal-de-prueba"
sembrar(caida_1h=2.5)
notify.maybe_pressure_alert(estado())
texto = " ".join(enviados)
chk("el aviso menciona una caída creíble",
    bool(enviados) and not any(x in texto for x in ("200", "199", "198")),
    texto.splitlines()[0] if enviados else "no se envió nada")
print(f"\n   Lo que le llegaría:\n     {texto.splitlines()[0] if enviados else '—'}")

import shutil
shutil.rmtree(tmp, ignore_errors=True)
print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
