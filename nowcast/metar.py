"""El aeropuerto de Aguascalientes (MMAS) como termómetro de referencia.

## Por qué el aeropuerto

Para saber si un pronóstico de helada o de frente acertó hace falta un
termómetro de verdad. El que hay en la malla -un BMP280 en una caja IP65 al
sol en el mástil- marcó 37.9 °C con 25-28 de ambiente: sirve para presión, no
para temperatura. El aeropuerto emite cada hora un METAR con termómetro
reglamentario, viento, rocío y presión, y se puede leer sin cuenta.

Dos salvedades que hay que tener presentes al leer los números:

- Está ~20 km al sur del centro, en terreno abierto. De madrugada suele estar
  MÁS FRÍO que la ciudad, que guarda calor. Una helada en el aeropuerto no
  siempre es helada en casa.
- Reporta en grados enteros y a veces se salta una hora. Para la mínima de una
  noche o el paso de un frente da igual; para décimas no sirve.

## Qué se deduce de él

`frentes_observados` y `minimas_observadas` son la VERDAD contra la que se
califica frio.py. Por eso usan la misma definición de frente que el
pronóstico (ver `firma_frente`): si se definieran distinto, la calificación
mediría la diferencia de definiciones y no el acierto.
"""
from __future__ import annotations

import csv
import logging
import os
from datetime import datetime, timedelta, timezone

from . import config, http, store

log = logging.getLogger(__name__)

ESTACION = "MMAS"
URL = "https://aviationweather.gov/api/data/metar?ids={id}&hours={h}&format=json"
CAMPOS = ["obs_utc", "temp", "dewp", "wdir", "wspd", "wgst", "altim", "cover", "raw"]


def _csv() -> str:
    # Se resuelve al usarlo, no al importar: las pruebas redirigen DATA_DIR.
    return os.path.join(config.DATA_DIR, "metar.csv")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- bajar

def parsear(respuesta) -> list[dict]:
    """JSON de aviationweather.gov -> filas. Lo que no se entiende se omite."""
    filas = []
    for m in respuesta or []:
        t = m.get("obsTime")
        if t is None:
            continue
        try:
            obs = datetime.fromtimestamp(int(t), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            continue
        # "VRB" (variable) no es una dirección: se guarda vacía, no como 0,
        # que se leería "viento del norte".
        wdir = m.get("wdir")
        filas.append({
            "obs_utc": obs.isoformat(),
            "temp": _num(m.get("temp")),
            "dewp": _num(m.get("dewp")),
            "wdir": _num(wdir) if wdir != "VRB" else None,
            "wspd": _num(m.get("wspd")),
            "wgst": _num(m.get("wgst")),
            "altim": _num(m.get("altim")),
            "cover": m.get("cover") or "",
            "raw": m.get("rawOb") or "",
        })
    return filas


def bajar(horas: int = 6) -> list[dict]:
    # Tiempo corto: es un dato de apoyo, no puede comerse el presupuesto de
    # la corrida si el servidor del aeropuerto anda lento.
    datos = http.get_json(URL.format(id=ESTACION, h=horas), timeout=10)
    if datos is None:
        log.warning("el aeropuerto no respondió")
        return []
    return parsear(datos)


def leer() -> list[dict]:
    if not os.path.exists(_csv()):
        return []
    with open(_csv(), newline="", encoding="utf-8") as fh:
        filas = list(csv.DictReader(fh))
    for f in filas:
        for k in ("temp", "dewp", "wdir", "wspd", "wgst", "altim"):
            f[k] = _num(f.get(k))
    return filas


def guardar(nuevas: list[dict]) -> int:
    """Añade solo lo que no estaba (por hora de observación). Devuelve cuántas."""
    ya = {f["obs_utc"] for f in leer()}
    nuevas = sorted((f for f in nuevas if f["obs_utc"] not in ya),
                    key=lambda f: f["obs_utc"])
    if not nuevas:
        return 0
    store._ensure(_csv(), CAMPOS)
    with open(_csv(), "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CAMPOS, extrasaction="ignore")
        for f in nuevas:
            w.writerow({k: ("" if f.get(k) is None else f.get(k)) for k in CAMPOS})
    return len(nuevas)


def actualizar() -> int:
    """Se llama en cada corrida. Seis horas de margen cubren cortes de red."""
    try:
        return guardar(bajar(6))
    except Exception as exc:
        log.warning("no se pudo actualizar el METAR: %s", exc)
        return 0


def ultima() -> dict | None:
    filas = leer()
    return filas[-1] if filas else None


# ---------------------------------------------------------------- frentes

def es_del_norte(wdir) -> bool:
    """Viento del noroeste al noreste: el que trae el aire frío aquí."""
    return wdir is not None and (wdir >= 300 or wdir <= 60)


def firma_frente(dp24: float | None, dt24: float | None, dtd24: float | None) -> bool:
    """La definición de "pasó un frente", la MISMA para pronóstico y observación.

    Cambios contra la misma hora del día anterior, para quitar el ciclo
    diario de temperatura (15-20 °C aquí) y la marea de presión (~4 hPa):

    - la presión sube al menos FRENTE_DP24_HPA (llega aire frío y denso), y
    - se enfría al menos FRENTE_DT24_C, o el aire se seca al menos
      FRENTE_DTD24_C de rocío (frentes secos, comunes en la meseta: bajan
      poco la máxima pero desploman el rocío y la mínima de la noche).
    """
    if dp24 is None or dp24 < config.FRENTE_DP24_HPA:
        return False
    frio = dt24 is not None and dt24 <= -config.FRENTE_DT24_C
    seco = dtd24 is not None and dtd24 <= -config.FRENTE_DTD24_C
    return frio or seco


def _cerca(serie: list[tuple[datetime, dict]], t: datetime, tol_min: int = 45):
    mejor, gap = None, None
    for ti, f in serie:
        g = abs((ti - t).total_seconds()) / 60
        if g <= tol_min and (gap is None or g < gap):
            mejor, gap = f, g
    return mejor


def frentes_observados(filas: list[dict] | None = None) -> list[datetime]:
    """Momentos en que el aeropuerto registró la llegada de un frente.

    Se toma el PRIMER momento que cumple la firma, y los siguientes 48 h se
    consideran el mismo frente: un frente cumple la firma durante horas.
    """
    filas = filas if filas is not None else leer()
    serie = []
    for f in filas:
        try:
            serie.append((datetime.fromisoformat(f["obs_utc"]), f))
        except (ValueError, KeyError):
            continue
    serie.sort(key=lambda x: x[0])
    eventos: list[datetime] = []
    for t, f in serie:
        if eventos and t - eventos[-1] < timedelta(hours=48):
            continue
        antes = _cerca(serie, t - timedelta(hours=24))
        if antes is None:
            continue

        def d(k):
            a, b = f.get(k), antes.get(k)
            return None if a is None or b is None else a - b

        if firma_frente(d("altim"), d("temp"), d("dewp")):
            eventos.append(t)
    return eventos


# ---------------------------------------------------------------- mínimas

def ventana_noche(fecha_local) -> tuple[datetime, datetime]:
    """La noche que termina la mañana de `fecha_local`: 18:00 a 10:00 local."""
    ini = datetime(fecha_local.year, fecha_local.month, fecha_local.day,
                   18, tzinfo=config.TZ) - timedelta(days=1)
    return ini, ini + timedelta(hours=16)


def minimas_observadas(filas: list[dict] | None = None) -> dict[str, float]:
    """{'AAAA-MM-DD': mínima de la noche que termina esa mañana}.

    Solo noches bien cubiertas: al menos 8 reportes, y alguno entre las 5 y las
    9 de la mañana, que es cuando cae la mínima. Una noche con huecos justo a
    esa hora daría una mínima falsamente templada.
    """
    filas = filas if filas is not None else leer()
    por_noche: dict[str, list[tuple[datetime, float]]] = {}
    for f in filas:
        if f.get("temp") is None:
            continue
        try:
            t = datetime.fromisoformat(f["obs_utc"]).astimezone(config.TZ)
        except (ValueError, KeyError):
            continue
        manana = (t + timedelta(days=1)).date() if t.hour >= 18 else t.date()
        if t.hour >= 18 or t.hour < 10:
            por_noche.setdefault(manana.isoformat(), []).append((t, f["temp"]))
    salida = {}
    for dia, obs in por_noche.items():
        if len(obs) < 8 or not any(5 <= t.hour < 9 for t, _ in obs):
            continue
        salida[dia] = min(v for _, v in obs)
    return salida
