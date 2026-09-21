"""Rayos detectados por el GLM de GOES-19.

El GLM (Geostationary Lightning Mapper) va a bordo del mismo satelite del que
ya bajamos el infrarrojo, y publica en el mismo bucket publico de NOAA. Es
deteccion optica real de relampagos desde el espacio, no una estimacion a
partir de la nube.

Numeros que hacen esto viable: cada archivo cubre 20 segundos y pesa ~360 KB,
con unos 30 segundos de retraso. Quince minutos son 45 archivos, ~16 MB:
cuatro veces mas ligero que las imagenes de infrarrojo que ya descargamos.

Se mantiene una hora de historia en cuatro bloques de 15 minutos. El bloque
mas reciente se dibuja brillante y los viejos se van apagando, de modo que
se ve hacia donde avanza la actividad electrica sin necesidad de controles.
"""
from __future__ import annotations

import io
import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import h5py
import numpy as np

from . import config, http, store
from .goes import BUCKET, S3_NS

log = logging.getLogger(__name__)

_KEY_RE = re.compile(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})")

# Los rayos se agrupan en una rejilla para que el JSON no crezca sin control:
# en una tormenta activa el GLM ve miles de destellos en 15 minutos.
GRID_DEG = 0.02          # ~2 km
MAX_PUNTOS_POR_BLOQUE = 1200


def _list_glm(hora: datetime) -> list[tuple[datetime, str]]:
    """Archivos GLM de una hora concreta."""
    import xml.etree.ElementTree as ET

    prefix = (f"GLM-L2-LCFA/{hora.year}/{hora.timetuple().tm_yday:03d}"
              f"/{hora.hour:02d}/")
    salida: list[tuple[datetime, str]] = []
    token = ""
    # una hora son 180 archivos: hay que paginar
    for _ in range(3):
        url = f"{BUCKET}/?list-type=2&max-keys=1000&prefix={prefix}{token}"
        raw = http.get_bytes(url)
        if not raw:
            break
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            break
        for node in root.findall(f"{S3_NS}Contents"):
            k = node.find(f"{S3_NS}Key")
            if k is None or not k.text:
                continue
            m = _KEY_RE.search(k.text)
            if not m:
                continue
            year, doy, hh, mm, ss = (int(g) for g in m.groups())
            t = (datetime(year, 1, 1, tzinfo=timezone.utc)
                 + timedelta(days=doy - 1, hours=hh, minutes=mm, seconds=ss))
            salida.append((t, k.text))
        nxt = root.find(f"{S3_NS}NextContinuationToken")
        if nxt is None or not nxt.text:
            break
        token = f"&continuation-token={nxt.text}"
    return salida


def _flashes(raw: bytes) -> list[tuple[float, float]]:
    """Extrae (lat, lon) de los destellos dentro del dominio."""
    fuera = config.LIGHTNING_RADIUS_DEG
    try:
        with h5py.File(io.BytesIO(raw), "r") as fh:
            if "flash_lat" not in fh or "flash_lon" not in fh:
                return []
            lat = np.asarray(fh["flash_lat"][()], dtype=np.float64)
            lon = np.asarray(fh["flash_lon"][()], dtype=np.float64)
    except Exception as exc:
        log.debug("archivo GLM ilegible: %s", exc)
        return []

    if lat.size == 0:
        return []
    cerca = ((np.abs(lat - config.LAT) <= fuera)
             & (np.abs(lon - config.LON) <= fuera))
    return list(zip(lat[cerca].tolist(), lon[cerca].tolist()))


def fetch_recent(minutos: int = 15,
                 limite: float | None = None) -> list[tuple[float, float]]:
    """Destellos de los ultimos N minutos alrededor de la ciudad.

    'limite' es un instante de time.monotonic() pasado el cual se deja de
    descargar y se devuelve lo que haya. Son ~45 archivos por corrida, la
    gran mayoria de las peticiones que hace el sistema; con el enlace lento
    son tambien la mayor parte del tiempo. Media capa de rayos es mucho
    mejor que una corrida que muere por tiempo agotado.
    """
    ahora = datetime.now(timezone.utc)
    desde = ahora - timedelta(minutes=minutos)

    claves: list[tuple[datetime, str]] = []
    for h in {desde.replace(minute=0, second=0, microsecond=0),
              ahora.replace(minute=0, second=0, microsecond=0)}:
        claves.extend(_list_glm(h))

    # Sin clave repetida: se consulta el listado de dos horas y un archivo
    # en el borde puede aparecer en ambas. Bajarlo dos veces seria pagar la
    # misma descarga dos veces, y con el enlace lento eso se nota.
    vistas: set[str] = set()
    ventana = []
    for t, k in claves:
        if desde <= t <= ahora and k not in vistas:
            vistas.add(k)
            ventana.append((t, k))
    ventana.sort()
    if not ventana:
        log.info("sin archivos GLM en la ventana")
        return []

    puntos: list[tuple[float, float]] = []
    fallos = 0
    leidos = 0
    # Del mas reciente al mas viejo: si hay que cortar, lo que se pierde es
    # lo antiguo, que es lo que menos importa para saber donde esta la
    # tormenta ahora.
    for _t, key in reversed(ventana):
        if limite is not None and time.monotonic() > limite:
            log.warning("rayos: presupuesto agotado, %s de %s archivos leidos",
                        leidos, len(ventana))
            break
        raw = http.get_bytes(f"{BUCKET}/{key}", timeout=30)
        leidos += 1
        if not raw:
            fallos += 1
            continue
        puntos.extend(_flashes(raw))

    log.info("rayos: %s destellos cerca en %s de %s archivos (%s fallaron)",
             len(puntos), leidos, len(ventana), fallos)
    return puntos


def _agrupar(puntos: list[tuple[float, float]]) -> list[list]:
    """Agrupa en rejilla y devuelve [lat, lon, cuantos]."""
    if not puntos:
        return []
    cubos: dict[tuple[int, int], int] = {}
    for lat, lon in puntos:
        clave = (int(round(lat / GRID_DEG)), int(round(lon / GRID_DEG)))
        cubos[clave] = cubos.get(clave, 0) + 1
    ordenado = sorted(cubos.items(), key=lambda kv: -kv[1])[:MAX_PUNTOS_POR_BLOQUE]
    return [[round(a * GRID_DEG, 4), round(b * GRID_DEG, 4), n]
            for (a, b), n in ordenado]


def update(limite: float | None = None) -> dict:
    """Añade el bloque actual y descarta lo que ya pasó de una hora."""
    ahora = datetime.now(timezone.utc)
    previo = store.load_json(config.LIGHTNING_JSON, {}) or {}
    bloques = previo.get("bloques", [])

    etiqueta = store.round_slot(ahora, minutes=15)
    if any(b.get("t") == etiqueta for b in bloques):
        log.info("el bloque %s ya estaba registrado", etiqueta)
    else:
        try:
            puntos = fetch_recent(15, limite=limite)
        except Exception as exc:
            log.error("descarga de rayos fallo: %s", exc)
            puntos = []
        bloques.append({"t": etiqueta, "puntos": _agrupar(puntos),
                        "total": len(puntos)})

    # conservar solo la ultima hora
    corte = ahora - timedelta(minutes=config.LIGHTNING_HISTORY_MIN)
    vivos = []
    for b in bloques:
        try:
            t = datetime.fromisoformat(b["t"])
        except (ValueError, KeyError):
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        if t >= corte:
            b["edad_min"] = int((ahora - t).total_seconds() / 60)
            vivos.append(b)
    vivos.sort(key=lambda b: b["t"])

    datos = {
        "actualizado": ahora.isoformat(),
        "bloques": vivos,
        "total_hora": sum(b.get("total", 0) for b in vivos),
    }
    store.save_json(config.LIGHTNING_JSON, datos)
    return datos


# ---------------------------------------------------------------------------
# Lectura de la tormenta: que tan cerca esta y si viene hacia aqui.
#
# El GLM ya deja una hora de historial en bloques de 15 minutos. Con eso se
# puede hacer algo que un solo cuadro no permite: distinguir una celda que se
# acerca de una que se aleja. Es la misma idea que el nowcasting por
# adveccion, pero sobre descargas electricas en vez de topes nubosos.
# ---------------------------------------------------------------------------

def _dist_km(lat: float, lon: float) -> float:
    """Distancia desde la ubicacion configurada, en kilometros."""
    lat1, lon1 = math.radians(config.LAT), math.radians(config.LON)
    lat2, lon2 = math.radians(lat), math.radians(lon)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = (math.sin(dlat / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(h))


@dataclass
class Tormenta:
    """Que esta haciendo la actividad electrica alrededor."""
    dist_cercano_km: float | None   # destello mas cercano del bloque actual
    dist_min_hora_km: float | None  # el mas cercano de toda la hora
    destellos_cerca: int            # en el bloque actual, dentro de RAYOS_CERCA_KM
    destellos_hora: int
    tendencia_km: float | None      # negativo = se acerca
    acercandose: bool
    minutos_sin_actividad: int | None
    fase: str                       # despejado | vigilando | acercandose | encima


def _dist_bloque(bloque: dict) -> float | None:
    """Distancia al destello mas cercano de un bloque, o None si no hubo."""
    puntos = bloque.get("puntos") or []
    if not puntos:
        return None
    return min(_dist_km(p[0], p[1]) for p in puntos)


def _cuantos_dentro(bloque: dict, km: float) -> int:
    total = 0
    for p in bloque.get("puntos") or []:
        if _dist_km(p[0], p[1]) <= km:
            total += int(p[2]) if len(p) > 2 else 1
    return total


def evaluar(datos: dict, fase_previa: str = "despejado") -> Tormenta:
    """Resume el estado de la tormenta a partir del historial de bloques.

    'fase_previa' importa por la histeresis: los umbrales para salir de un
    estado son mas amplios que los de entrar. Sin eso, una celda rondando
    justo en el limite mandaria un aviso cada quince minutos.
    """
    bloques = sorted(datos.get("bloques") or [], key=lambda b: b.get("t", ""))
    if not bloques:
        return Tormenta(None, None, 0, 0, None, False, None, "despejado")

    distancias = [(b, _dist_bloque(b)) for b in bloques]
    con_rayos = [(b, d) for b, d in distancias if d is not None]

    actual = distancias[-1][1]
    dist_min_hora = min((d for _, d in con_rayos), default=None)
    destellos_hora = sum(b.get("total", 0) for b in bloques)
    destellos_cerca = _cuantos_dentro(bloques[-1], config.RAYOS_CERCA_KM)

    # Cuanto lleva sin actividad relevante: bloques recientes sin nada
    # dentro del radio de vigilancia.
    minutos_sin = 0
    for b, d in reversed(distancias):
        if d is not None and d <= config.RAYOS_SALIR_LEJOS_KM:
            break
        minutos_sin += 15
    minutos_sin = minutos_sin if minutos_sin else None

    # Tendencia: cuanto cambia la distancia entre los bloques con actividad.
    # Se compara la primera mitad con la segunda para no depender de un solo
    # bloque, que puede tener un destello suelto muy lejos del cuerpo de la
    # celda.
    tendencia = None
    if len(con_rayos) >= 2:
        mitad = len(con_rayos) // 2
        antes = [d for _, d in con_rayos[:mitad or 1]]
        despues = [d for _, d in con_rayos[mitad:]]
        tendencia = (sum(despues) / len(despues)) - (sum(antes) / len(antes))

    # Un umbral de 5 km evita llamar "acercandose" al ruido de medicion.
    acercandose = tendencia is not None and tendencia < -5.0

    # --- fase, con histeresis segun de donde veniamos
    #
    # La referencia es la distancia del bloque MAS RECIENTE con actividad, no
    # el minimo de la hora. Usar el minimo de la hora tenia dos fallos: una
    # celda que ya se fue seguia contando como cercana durante 60 minutos -el
    # "ya paso" no llegaba nunca-, y una tormenta lejana actual quedaba
    # enmascarada por otra cercana de hace un rato.
    referencia = None
    edad_referencia = 0
    for i, (_b, d) in enumerate(reversed(distancias)):
        if d is not None:
            referencia, edad_referencia = d, i * 15
            break
    # Si lo ultimo que se vio ya es viejo, no describe el presente.
    if referencia is not None and edad_referencia >= config.RAYOS_DESPEJADO_MIN:
        referencia = None

    if referencia is None:
        fase = "despejado"
    elif fase_previa == "encima":
        fase = "encima" if referencia <= config.RAYOS_SALIR_CERCA_KM else (
            "acercandose" if referencia <= config.RAYOS_SALIR_LEJOS_KM else "despejado")
    elif referencia <= config.RAYOS_CERCA_KM:
        fase = "encima"
    elif referencia <= config.RAYOS_LEJOS_KM:
        fase = "acercandose" if acercandose else "vigilando"
    elif fase_previa in ("acercandose", "vigilando") and referencia <= config.RAYOS_SALIR_LEJOS_KM:
        fase = "vigilando"
    else:
        fase = "despejado"

    # Silencio suficiente manda sobre todo lo demas: si hace media hora que
    # no se ve nada cerca, la tormenta se acabo aunque la histeresis quisiera
    # mantenernos en alerta.
    if minutos_sin is not None and minutos_sin >= config.RAYOS_DESPEJADO_MIN:
        fase = "despejado"
    # Y al reves: no se declara el "ya paso" antes de tiempo.
    elif fase == "despejado" and minutos_sin is not None \
            and minutos_sin < config.RAYOS_DESPEJADO_MIN \
            and fase_previa in ("encima", "acercandose", "vigilando"):
        fase = "vigilando"

    return Tormenta(actual, dist_min_hora, destellos_cerca, destellos_hora,
                    tendencia, acercandose, minutos_sin, fase)


# ---------------------------------------------------------------------------
# La deriva REAL de la tormenta, medida con los rayos.
#
# El motor estima el movimiento por correlacion de fase sobre el infrarrojo, y
# el infrarrojo ve el TECHO de la nube, a 10-14 km de altura. Ese techo lo
# arrastra el viento en altura, que con cizalladura va mucho mas rapido y a
# menudo en otra direccion que la celda de abajo. El yunque se estira hacia
# donde sopla arriba mientras la celda que llueve va a su aire.
#
# Los rayos, en cambio, salen del nucleo convectivo. Donde hay descargas esta
# la celda de verdad, no su sombrero. Seguir el centroide de las descargas
# entre bloques de 15 minutos da la traslacion del nucleo.
#
# Alvaro lo noto mirando el cono contra las tormentas reales, antes de que
# ninguna medicion lo dijera.
#
# Esto NO sustituye al infrarrojo: sin rayos no hay deriva, y la mayor parte
# del tiempo no hay rayos. Es una correccion que aparece exactamente cuando
# hay una tormenta electrica, que es cuando importa acertar.
# ---------------------------------------------------------------------------

# Radio para seguir el MISMO complejo entre bloques. Con 60 km una celda a
# 60 km/h se sigue sin problema (15 km por bloque) y no se salta a otra
# tormenta del otro lado del dominio.
RADIO_SEGUIMIENTO_KM = 60.0
MIN_DESTELLOS = 4          # menos que esto es ruido, no un centroide


@dataclass
class Deriva:
    """Traslacion del nucleo convectivo, medida con descargas."""
    bearing_deg: float | None    # hacia donde va
    speed_kmh: float
    confianza: float             # 0-1
    pares: int                   # cuantos saltos entre bloques se usaron
    destellos: int

    @property
    def from_direction(self) -> str:
        if self.bearing_deg is None:
            return "sin movimiento definido"
        origen = (self.bearing_deg + 180.0) % 360.0
        puntos = ["norte", "noreste", "este", "sureste",
                  "sur", "suroeste", "oeste", "noroeste"]
        return puntos[int((origen + 22.5) % 360 // 45)]


def _mas_activo(puntos):
    """El punto con mas descargas: un ancla que esta seguro dentro de una celda."""
    mejor, cuenta = None, -1.0
    for p in puntos or []:
        n = float(p[2]) if len(p) > 2 else 1.0
        if n > cuenta:
            mejor, cuenta = (float(p[0]), float(p[1])), n
    return mejor


def _centroide(puntos, cerca_de=None, radio_km=RADIO_SEGUIMIENTO_KM):
    """Centroide ponderado por numero de destellos, opcionalmente local.

    'cerca_de' es lo que evita el fallo obvio: con dos tormentas en el
    dominio, el centroide global cae entre las dos y se mueve segun cual
    descargue mas, que no es el movimiento de ninguna.
    """
    tot = wlat = wlon = 0.0
    for p in puntos or []:
        lat, lon = float(p[0]), float(p[1])
        n = float(p[2]) if len(p) > 2 else 1.0
        if cerca_de is not None:
            dlat = (lat - cerca_de[0]) * 111.0
            dlon = ((lon - cerca_de[1]) * 111.0
                    * math.cos(math.radians(cerca_de[0])))
            if math.hypot(dlat, dlon) > radio_km:
                continue
        wlat += lat * n
        wlon += lon * n
        tot += n
    if tot <= 0:
        return None, 0.0
    return (wlat / tot, wlon / tot), tot


def deriva(datos: dict) -> Deriva:
    """Velocidad y rumbo del nucleo, a partir del historial de bloques."""
    bloques = sorted(datos.get("bloques") or [], key=lambda b: b.get("t", ""))
    con_rayos = [b for b in bloques if (b.get("puntos") or [])]
    if len(con_rayos) < 2:
        return Deriva(None, 0.0, 0.0, 0, 0)

    vectores, total_destellos = [], 0.0
    anterior = None
    for b in con_rayos:
        t = b.get("t")
        try:
            tt = datetime.fromisoformat(t)
            if tt.tzinfo is None:
                tt = tt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        # El primer centroide NO puede ser global. Con dos tormentas en el
        # dominio -pasa a diario en temporada- el centro de masa cae en el
        # hueco entre las dos, donde no hay nada, y el seguimiento arranca
        # anclado a un punto vacio: o no encuentra rayos cerca y se calla, o
        # peor, oscila entre las dos segun cual descargue mas y publica un
        # rumbo que no es el de ninguna.
        #
        # Se ancla en el grupo mas activo: el punto con mas descargas. No es
        # el centro exacto de la celda, pero esta DENTRO de una celda, que es
        # lo unico que hace falta para que el radio de seguimiento agarre la
        # tormenta correcta.
        ref = anterior[1] if anterior else _mas_activo(b.get("puntos"))
        c, n = _centroide(b.get("puntos"), cerca_de=ref)
        if c is None or n < MIN_DESTELLOS:
            continue
        if anterior is not None:
            t0, c0, _n0 = anterior
            dt = (tt - t0).total_seconds() / 60.0
            if 0 < dt <= 45:
                norte = (c[0] - c0[0]) * 111.0
                este = ((c[1] - c0[1]) * 111.0
                        * math.cos(math.radians(c0[0])))
                vectores.append((norte / dt, este / dt, n))
                total_destellos += n
        anterior = (tt, c, n)

    if not vectores:
        return Deriva(None, 0.0, 0.0, 0, int(total_destellos))

    # Mediana ponderada por destellos: un bloque con tres descargas no manda
    # sobre uno con doscientas.
    vs = sorted(vectores, key=lambda v: math.hypot(v[0], v[1]))
    peso_total = sum(v[2] for v in vs)
    acum, med = 0.0, vs[-1]
    for v in vs:
        acum += v[2]
        if acum >= peso_total / 2:
            med = v
            break
    vn, ve = med[0], med[1]
    speed_kmh = math.hypot(vn, ve) * 60.0

    # Una celda no viaja a 150 km/h. Si sale eso, el seguimiento salto de una
    # tormenta a otra y el dato no vale.
    if speed_kmh > 110.0:
        return Deriva(None, 0.0, 0.0, len(vectores), int(total_destellos))

    bearing = ((math.degrees(math.atan2(ve, vn)) + 360.0) % 360.0
               if speed_kmh > 2.0 else None)

    # Confianza: cuantos saltos coinciden entre si, y cuantos destellos los
    # respaldan. Un solo salto con cinco rayos no es una medicion.
    if len(vectores) >= 2:
        media_n = sum(v[0] for v in vectores) / len(vectores)
        media_e = sum(v[1] for v in vectores) / len(vectores)
        disp = sum(math.hypot(v[0] - media_n, v[1] - media_e)
                   for v in vectores) / len(vectores)
        acuerdo = 1.0 / (1.0 + disp * 60.0 / 25.0)
    else:
        acuerdo = 0.35
    # 80 descargas repartidas en varios bloques es una tormenta de verdad.
    # Con 15 el centroide se mueve por donde cayo un rayo suelto, no por
    # donde va la celda, y la confianza tiene que decirlo.
    respaldo = min(1.0, total_destellos / 80.0)
    conf = float(max(0.0, min(1.0, acuerdo * respaldo)))
    return Deriva(bearing, round(speed_kmh, 1), round(conf, 3),
                  len(vectores), int(total_destellos))


_CLAVE_DERIVA = "deriva_previa"


def deriva_persistente(d: "Deriva") -> bool:
    """¿Este rumbo coincide con el de hace quince minutos?

    La comprobacion que faltaba, y la medicion del 21 de septiembre de 2026 es
    la que la pidio. En 380 tormentas reales el rumbo por rayos saltaba entre
    bloques consecutivos -94 grados, luego 334, luego 86- y eso no lo hace una
    tormenta: lo hace un centroide brincando entre celdas de un mismo complejo.

    Una celda real gira despacio. Si dos estimaciones seguidas no se parecen,
    al menos una es ruido, y no hay forma de saber cual. Se exige acuerdo antes
    de dejar que el dato mande sobre el cono.

    El estado se guarda aqui y no en quien llama porque es de esta medida:
    quien la usa no tiene por que saber que necesita memoria.
    """
    previa = store.load_json(config.STATE_JSON, {}).get(_CLAVE_DERIVA) or {}
    estado = store.load_json(config.STATE_JSON, {})
    ahora = {"bearing": d.bearing_deg, "ts": datetime.now(timezone.utc).isoformat()}
    estado[_CLAVE_DERIVA] = ahora
    store.save_json(config.STATE_JSON, estado)

    if d.bearing_deg is None or previa.get("bearing") is None:
        return False
    try:
        t_prev = datetime.fromisoformat(previa["ts"])
        if t_prev.tzinfo is None:
            t_prev = t_prev.replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return False
    edad_min = (datetime.now(timezone.utc) - t_prev).total_seconds() / 60.0
    # Mas de media hora sin medir y ya no son estimaciones consecutivas: la
    # tormenta pudo cambiar de verdad en ese hueco.
    if not (0 < edad_min <= 35):
        return False
    dif = abs((d.bearing_deg - float(previa["bearing"]) + 180.0) % 360.0 - 180.0)
    if dif > 45.0:
        log.info("la deriva por rayos giro %.0f grados en %.0f min: no es "
                 "persistente, no se usa", dif, edad_min)
        return False
    return True
