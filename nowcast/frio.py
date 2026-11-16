"""Frentes fríos y heladas: la temporada seca de Aguascalientes.

## Qué hace

Cada corrida pide a Open-Meteo 24 h de pasado y 72 de futuro de los cinco
modelos, y saca dos cosas:

- **Frente**: el primer momento en que cada modelo cumple la firma de frente
  (`metar.firma_frente`: presión que sube y aire que se enfría o se seca,
  contra la misma hora del día anterior). Si lo ven al menos
  FRENTE_MIN_MODELOS, hay frente: llegada = mediana de los que lo ven.
- **Helada**: la mínima de la próxima madrugada según cada modelo, corregida
  con el sesgo que el aeropuerto haya enseñado, y la probabilidad de que la
  mínima sea de HELADA_UMBRAL_C (3 °C) o menos.

## Por qué la misma firma que el aeropuerto

La verdad sale de metar.py con `firma_frente`. Si el pronóstico usara otra
definición -por ejemplo, el giro del viento- la sección 10 de evaluar.py
mediría cuánto se parecen dos definiciones y no si el pronóstico acertó.

## Por qué 700 hPa y no 850

El manual dice "mira 850 hPa" para el aire frío. Aquí, a 1,880 m, la presión
en superficie ronda 815 hPa: el nivel de 850 queda BAJO TIERRA y los modelos
lo inventan extrapolando. Se usa 700 hPa (~3,000 m), que está en el aire
libre sobre la meseta. Se publica como dato ("se enfría en altura"), no entra
en la firma: el aeropuerto no lo puede medir.

## La incertidumbre de la helada es honesta, no optimista

Mientras no haya HELADA_MIN_NOCHES noches verificadas, la mínima se toma con
±HELADA_SIGMA_INICIAL °C. Los modelos suelen quedarse cortos en las noches
despejadas y en calma de la meseta, y el aeropuerto está en terreno abierto:
hasta medir cuánto, la probabilidad tiene que reflejar que no se sabe.
"""
from __future__ import annotations

import csv
import logging
import math
import os
import statistics
from datetime import date, datetime, timedelta, timezone

from . import config, http, metar, store

log = logging.getLogger(__name__)

VARIABLES = ["temperature_2m", "dew_point_2m", "pressure_msl",
             "wind_direction_10m", "wind_gusts_10m", "temperature_700hPa"]

CAMPOS = ["emitido_utc", "frente_eta_utc", "frente_n", "frente_validos",
          "frente_max_antes", "frente_max_despues", "frente_racha",
          "helada_fecha", "tmin_mediana", "tmin_spread", "tmin_corregida",
          "p_helada", "sesgo", "sigma", "noches_cal"]

_PUNTOS = ["norte", "noreste", "este", "sureste",
           "sur", "suroeste", "oeste", "noroeste"]
_DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def _csv() -> str:
    return os.path.join(config.DATA_DIR, "frio.csv")


# ---------------------------------------------------------------- datos

def bajar() -> dict | None:
    url = ("https://api.open-meteo.com/v1/forecast"
           f"?latitude={config.LAT:.4f}&longitude={config.LON:.4f}"
           f"&hourly={','.join(VARIABLES)}"
           "&past_hours=24&forecast_hours=72&timezone=UTC"
           f"&models={','.join(config.OPENMETEO_MODELS)}")
    return http.get_json(url, timeout=20)


def series(datos: dict) -> tuple[list[datetime], dict[str, dict[str, list]]]:
    """(horas, {modelo: {variable: valores}}). Un modelo sin presión ni
    temperatura no sirve para nada aquí y se omite."""
    h = (datos or {}).get("hourly") or {}
    horas = []
    for ts in h.get("time") or []:
        try:
            t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            horas.append(t if t.tzinfo else t.replace(tzinfo=timezone.utc))
        except ValueError:
            horas.append(None)
    out = {}
    for m in config.OPENMETEO_MODELS:
        d = {}
        for v in VARIABLES:
            s = h.get(f"{v}_{m}")
            if s is None and len(config.OPENMETEO_MODELS) == 1:
                s = h.get(v)
            d[v] = s or []
        if d["temperature_2m"] and d["pressure_msl"]:
            out[m] = d
    return horas, out


def _v(s: list, i: int):
    return s[i] if 0 <= i < len(s) and s[i] is not None else None


# ---------------------------------------------------------------- frente

def frente_modelo(horas, d: dict, ahora: datetime) -> datetime | None:
    """Primer momento futuro en que este modelo cumple la firma de frente."""
    for i, t in enumerate(horas):
        if t is None or t <= ahora:
            continue
        j = i - 24                          # horario: 24 posiciones = 24 h
        if j < 0 or horas[j] is None or abs((t - horas[j]) - timedelta(hours=24)) > timedelta(minutes=30):
            continue

        def dif(v):
            a, b = _v(d[v], i), _v(d[v], j)
            return None if a is None or b is None else a - b

        if metar.firma_frente(dif("pressure_msl"), dif("temperature_2m"),
                              dif("dew_point_2m")):
            return t
    return None


def _max_entre(horas, s, a, b):
    vals = [_v(s, i) for i, t in enumerate(horas) if t is not None and a <= t < b]
    vals = [x for x in vals if x is not None]
    return max(vals) if vals else None


def _rumbo(grados: list[float]) -> str | None:
    if not grados:
        return None
    x = sum(math.cos(math.radians(g)) for g in grados)
    y = sum(math.sin(math.radians(g)) for g in grados)
    ang = math.degrees(math.atan2(y, x)) % 360
    return _PUNTOS[int((ang + 22.5) % 360 // 45)]


def frente(horas, modelos: dict, ahora: datetime) -> dict:
    """Combina los modelos. Siempre devuelve cuántos lo ven y cuántos podían."""
    vistos = {m: frente_modelo(horas, d, ahora) for m, d in modelos.items()}
    detect = {m: t for m, t in vistos.items() if t is not None}
    res = {"n": len(detect), "validos": len(modelos), "eta": None}
    if len(detect) < config.FRENTE_MIN_MODELOS:
        return res
    ts = sorted(detect.values())
    eta = ts[len(ts) // 2]
    antes, despues, rachas, dirs, enfr = [], [], [], [], []
    for m, t in detect.items():
        d = modelos[m]
        a = _max_entre(horas, d["temperature_2m"], t - timedelta(hours=24), t)
        b = _max_entre(horas, d["temperature_2m"], t + timedelta(hours=6), t + timedelta(hours=30))
        if a is not None and b is not None:
            antes.append(a); despues.append(b)
        r = _max_entre(horas, d["wind_gusts_10m"], t - timedelta(hours=3), t + timedelta(hours=12))
        if r is not None:
            rachas.append(r)
        dirs += [x for i, tt in enumerate(horas)
                 if tt is not None and t <= tt < t + timedelta(hours=12)
                 and (x := _v(d["wind_direction_10m"], i)) is not None]
        t7a = _max_entre(horas, d["temperature_700hPa"], t - timedelta(hours=1), t + timedelta(hours=1))
        t7b = _max_entre(horas, d["temperature_700hPa"], t + timedelta(hours=11), t + timedelta(hours=13))
        if t7a is not None and t7b is not None:
            enfr.append(t7b - t7a)
    res.update({
        "eta": eta,
        "margen_h": round((ts[-1] - ts[0]).total_seconds() / 7200, 1),
        "max_antes": round(statistics.median(antes)) if antes else None,
        "max_despues": round(statistics.median(despues)) if despues else None,
        "racha_kmh": round(statistics.median(rachas)) if rachas else None,
        "viento_de": _rumbo(dirs),
        "enfria_700": round(statistics.median(enfr), 1) if enfr else None,
    })
    return res


# ---------------------------------------------------------------- helada

def noche_objetivo(ahora: datetime) -> date:
    """La madrugada que importa: la de hoy si aún no son las 10, si no la de mañana."""
    loc = ahora.astimezone(config.TZ)
    return loc.date() if loc.hour < 10 else loc.date() + timedelta(days=1)


def tmin_modelos(horas, modelos: dict, fecha: date) -> list[float]:
    ini, fin = metar.ventana_noche(fecha)
    out = []
    for d in modelos.values():
        vals = [_v(d["temperature_2m"], i) for i, t in enumerate(horas)
                if t is not None and ini <= t < fin]
        vals = [x for x in vals if x is not None]
        # Una noche a medias (la ventana empieza fuera de lo bajado) daría una
        # "mínima" que no es la de la madrugada: mejor no tenerla.
        if len(vals) >= 12:
            out.append(min(vals))
    return out


def de_la_tarde(filas: list[dict], fechas) -> dict[str, dict]:
    """{fecha: la fila emitida más cerca de las 18:00 de la víspera (±4 h)}.

    Es el pronóstico que importa calificar: el aviso de helada sale a esa
    hora. Calificar el de las 5 de la mañana, con la helada ya encima, haría
    parecer el sistema mejor de lo que es cuando sirve.
    """
    fechas = set(fechas)
    mejor: dict[str, tuple[float, dict]] = {}
    for f in filas:
        fecha = f.get("helada_fecha")
        if not fecha or fecha not in fechas or _num(f.get("tmin_mediana")) is None:
            continue
        try:
            emit = datetime.fromisoformat(f["emitido_utc"]).astimezone(config.TZ)
            objetivo = datetime.fromisoformat(fecha).replace(tzinfo=config.TZ) - timedelta(hours=6)
        except (ValueError, KeyError):
            continue
        dist = abs((emit - objetivo).total_seconds())
        if dist <= 4 * 3600 and (fecha not in mejor or dist < mejor[fecha][0]):
            mejor[fecha] = (dist, f)
    return {k: v[1] for k, v in mejor.items()}


def calibracion(filas=None, minimas=None) -> dict:
    """Sesgo y dispersión de la mínima, aprendidos del aeropuerto.

    sesgo = observada − prevista: negativo si el aeropuerto amanece más frío
    de lo que dicen los modelos, que es lo esperable en noches despejadas.
    Solo las últimas 40 noches: el sesgo de noviembre no es el de febrero.
    """
    filas = filas if filas is not None else leer()
    minimas = minimas if minimas is not None else metar.minimas_observadas()
    tarde = de_la_tarde(filas, minimas)
    res = [minimas[k] - float(tarde[k]["tmin_mediana"]) for k in sorted(tarde)][-40:]
    if len(res) < config.HELADA_MIN_NOCHES:
        return {"sesgo": 0.0, "sigma": config.HELADA_SIGMA_INICIAL,
                "noches": len(res), "calibrada": False}
    sesgo = statistics.fmean(res)
    sigma = math.sqrt(statistics.fmean([(r - sesgo) ** 2 for r in res]))
    # Nunca más seguro que 1 °C: el aeropuerto da grados enteros.
    return {"sesgo": round(sesgo, 2), "sigma": round(max(1.0, sigma), 2),
            "noches": len(res), "calibrada": True}


def prob_bajo(umbral: float, media: float, sigma: float) -> float:
    return 0.5 * (1 + math.erf((umbral - media) / (sigma * math.sqrt(2))))


def helada(horas, modelos: dict, ahora: datetime, cal: dict) -> dict | None:
    fecha = noche_objetivo(ahora)
    tm = tmin_modelos(horas, modelos, fecha)
    if not tm:
        return None
    med = statistics.median(tm)
    spread = statistics.pstdev(tm) if len(tm) > 1 else 0.0
    corr = med + cal["sesgo"]
    # La duda total suma la del pasado (sigma) y el desacuerdo de hoy.
    sig = math.sqrt(cal["sigma"] ** 2 + spread ** 2)
    return {"fecha": fecha.isoformat(), "tmin_mediana": round(med, 1),
            "spread": round(spread, 1), "tmin": round(corr, 1),
            "p": round(prob_bajo(config.HELADA_UMBRAL_C, corr, sig), 3),
            "sigma": round(sig, 2),
            "umbral": config.HELADA_UMBRAL_C, "calibrada": cal["calibrada"],
            "noches": cal["noches"], "modelos": len(tm)}


# ---------------------------------------------------------------- todo junto

def _cuando(t: datetime) -> str:
    loc = t.astimezone(config.TZ)
    return f"{_DIAS[loc.weekday()]} {loc.day} ~{loc.strftime('%H:%M')}"


def calcular(ahora: datetime | None = None, datos: dict | None = None) -> dict:
    """Lo que va a latest.json bajo "frio". Claves siempre presentes; null si no se sabe."""
    ahora = ahora or datetime.now(timezone.utc)
    if datos is None:
        datos = bajar()
    horas, modelos = series(datos)
    fr, he = None, None
    if modelos:
        f = frente(horas, modelos, ahora)
        if f["eta"] is not None:
            fr = {"eta_utc": f["eta"].isoformat(), "eta_local": _cuando(f["eta"]),
                  "en_h": round((f["eta"] - ahora).total_seconds() / 3600, 1),
                  "modelos": f"{f['n']} de {f['validos']}",
                  "margen_h": f["margen_h"], "max_antes": f["max_antes"],
                  "max_despues": f["max_despues"], "racha_kmh": f["racha_kmh"],
                  "viento_de": f["viento_de"], "enfria_700": f["enfria_700"]}
        he = helada(horas, modelos, ahora, calibracion())
    else:
        f = {"n": 0, "validos": 0}
    u = metar.ultima()
    aero = None
    if u:
        aero = {"obs_utc": u["obs_utc"], "temp": u.get("temp"), "rocio": u.get("dewp"),
                "viento_de": (_PUNTOS[int((u["wdir"] + 22.5) % 360 // 45)]
                              if u.get("wdir") is not None else None),
                "viento_kmh": (round(u["wspd"] * 1.852) if u.get("wspd") is not None else None)}
    salida = {"frente": fr, "helada": he, "aeropuerto": aero}
    salida["_fila"] = {
        "emitido_utc": ahora.isoformat(),
        "frente_eta_utc": fr["eta_utc"] if fr else "",
        "frente_n": f.get("n", 0), "frente_validos": f.get("validos", 0),
        "frente_max_antes": (fr or {}).get("max_antes") or "",
        "frente_max_despues": (fr or {}).get("max_despues") or "",
        "frente_racha": (fr or {}).get("racha_kmh") or "",
        "helada_fecha": he["fecha"] if he else "",
        "tmin_mediana": he["tmin_mediana"] if he else "",
        "tmin_spread": he["spread"] if he else "",
        "tmin_corregida": he["tmin"] if he else "",
        "p_helada": he["p"] if he else "",
        "sesgo": "" if not he else round(he["tmin"] - he["tmin_mediana"], 2),
        "sigma": he["sigma"] if he else "", "noches_cal": he["noches"] if he else "",
    }
    return salida


# ---------------------------------------------------------------- registro

def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def leer() -> list[dict]:
    if not os.path.exists(_csv()):
        return []
    with open(_csv(), newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def registrar(fila: dict | None) -> None:
    if not fila:
        return
    store._ensure(_csv(), CAMPOS)
    with open(_csv(), "a", newline="", encoding="utf-8") as fh:
        csv.DictWriter(fh, fieldnames=CAMPOS, extrasaction="ignore").writerow(fila)


# ---------------------------------------------------------------- avisos

def _estado() -> dict:
    return store.load_json(config.STATE_JSON, {}).get("frio_avisos") or {}


def _guardar_estado(e: dict) -> None:
    todo = store.load_json(config.STATE_JSON, {})
    todo["frio_avisos"] = e
    store.save_json(config.STATE_JSON, todo)


def texto_frente(fr: dict) -> str:
    partes = [f"Llega {fr['eta_local']}"
              + (f" (±{fr['margen_h']:.0f} h)" if fr.get("margen_h") else "") + "."]
    if fr.get("max_antes") is not None and fr.get("max_despues") is not None:
        partes.append(f"La máxima pasa de {fr['max_antes']} a {fr['max_despues']} °C.")
    if fr.get("racha_kmh"):
        partes.append(f"Viento del {fr.get('viento_de') or 'norte'} con rachas de "
                      f"~{fr['racha_kmh']} km/h.")
    partes.append(f"Lo ven {fr['modelos']} modelos.")
    return " ".join(partes)


def texto_helada(he: dict) -> str:
    t = (f"Mínima prevista ~{he['tmin']:.0f} °C; probabilidad de {he['umbral']:.0f} °C "
         f"o menos: {he['p']:.0%}. Lo más frío, poco antes del amanecer.")
    if not he.get("calibrada"):
        t += (f" (Aún sin calibrar contra el aeropuerto: {he.get('noches', 0)} de "
              f"{config.HELADA_MIN_NOCHES} noches.)")
    return t + " Medido en el aeropuerto; la ciudad suele estar algo menos fría."


def avisar(frio_res: dict, ahora: datetime | None = None, enviar=None) -> list[str]:
    """Manda lo que toque y devuelve qué mandó. Una vez por frente y por noche."""
    from . import notify
    enviar = enviar or notify.send
    ahora = ahora or datetime.now(timezone.utc)
    e = _estado()
    enviados = []

    fr = frio_res.get("frente")
    if fr:
        lo, hi = config.FRENTE_AVISO_H
        eta = datetime.fromisoformat(fr["eta_utc"])
        previo = e.get("frente_eta")
        mismo = False
        if previo:
            try:
                mismo = abs((eta - datetime.fromisoformat(previo)).total_seconds()) < 18 * 3600
            except ValueError:
                mismo = False
        if lo <= fr["en_h"] <= hi and not mismo:
            if enviar("Frente frío en camino", texto_frente(fr), tags="snowflake"):
                e["frente_eta"] = fr["eta_utc"]
                enviados.append("frente")

    he = frio_res.get("helada")
    hora = ahora.astimezone(config.TZ).hour
    a, b = config.HELADA_AVISO_HORAS
    if (he and he["p"] >= config.HELADA_AVISO_P and a <= hora < b
            and e.get("helada_fecha") != he["fecha"]):
        if enviar("Helada esta madrugada", texto_helada(he), tags="cold_face"):
            e["helada_fecha"] = he["fecha"]
            enviados.append("helada")

    if enviados:
        _guardar_estado(e)
    return enviados
