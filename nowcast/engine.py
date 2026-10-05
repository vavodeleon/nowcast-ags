"""Motor de nowcasting: seguimiento de celdas y extrapolacion.

Es la version automatica de lo que haces a mano: mirar los ultimos cuadros
del satelite/radar, ver hacia donde se mueven las celdas, y proyectar si
alguna te va a pasar encima.

Metodo: correlacion de fase (FFT) por cuadrantes para estimar el campo de
movimiento, y adveccion semi-lagrangiana hacia atras para evaluar que habra
sobre tus coordenadas dentro de N minutos. Es el nucleo de lo que hacen
pysteps y los sistemas operativos de nowcasting, sin las partes que
requieren datos que no son publicos.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from . import config
from .sources import Frame

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- utilidades

def to_signal(frame: Frame) -> np.ndarray:
    """Normaliza cualquier campo a 'intensidad de lluvia' en [0, 1]."""
    data = frame.data
    if frame.kind == "radar":
        sig = (data - config.DBZ_TRACE) / (config.DBZ_STORM - config.DBZ_TRACE)
    else:  # infrarrojo: mas frio = tope mas alto = mas convectivo
        sig = ((config.IR_CONVECTIVE_K - data)
               / (config.IR_CONVECTIVE_K - config.IR_DEEP_K))
    sig = np.nan_to_num(sig, nan=0.0, posinf=0.0, neginf=0.0)
    return np.clip(sig, 0.0, 1.0).astype(np.float32)


def _phase_correlate(a: np.ndarray, b: np.ndarray) -> tuple[float, float, float]:
    """Desplazamiento (dy, dx) de a -> b y su confianza [0, 1]."""
    if a.shape != b.shape or a.size == 0:
        return 0.0, 0.0, 0.0
    if np.std(a) < 1e-6 or np.std(b) < 1e-6:
        return 0.0, 0.0, 0.0

    # ventana de Hann para evitar el artefacto de los bordes
    wy = np.hanning(a.shape[0])[:, None]
    wx = np.hanning(a.shape[1])[None, :]
    win = wy * wx

    fa = np.fft.rfft2((a - a.mean()) * win)
    fb = np.fft.rfft2((b - b.mean()) * win)
    cross = fa.conj() * fb
    mag = np.abs(cross)
    mag[mag < 1e-12] = 1e-12
    corr = np.fft.irfft2(cross / mag, s=a.shape)

    peak = int(np.argmax(corr))
    py, px = np.unravel_index(peak, corr.shape)

    # confianza: cuanto sobresale el pico frente al ruido de fondo
    peak_val = float(corr[py, px])
    background = float(np.mean(np.abs(corr)))
    conf = 0.0 if background <= 0 else min(1.0, max(0.0,
                                                    (peak_val / background - 1.0) / 40.0))

    # refinamiento subpixel por parabola sobre los vecinos
    def _sub(idx: int, size: int, axis: int) -> float:
        im1 = corr[(py - 1) % size, px] if axis == 0 else corr[py, (px - 1) % size]
        ip1 = corr[(py + 1) % size, px] if axis == 0 else corr[py, (px + 1) % size]
        denom = im1 - 2 * peak_val + ip1
        delta = 0.0 if abs(denom) < 1e-12 else 0.5 * (im1 - ip1) / denom
        return idx + max(-1.0, min(1.0, delta))

    fy = _sub(py, a.shape[0], 0)
    fx = _sub(px, a.shape[1], 1)

    # envolver al rango [-N/2, N/2)
    if fy > a.shape[0] / 2:
        fy -= a.shape[0]
    if fx > a.shape[1] / 2:
        fx -= a.shape[1]
    return float(fy), float(fx), conf


@dataclass
class Motion:
    vy_px_min: float = 0.0      # positivo = hacia el sur (fila creciente)
    vx_px_min: float = 0.0      # positivo = hacia el este
    speed_kmh: float = 0.0
    bearing_deg: float | None = None   # direccion HACIA la que se mueve
    confidence: float = 0.0

    @property
    def from_direction(self) -> str:
        """De donde viene, en lenguaje humano."""
        if self.bearing_deg is None:
            return "sin movimiento definido"
        origin = (self.bearing_deg + 180.0) % 360.0
        puntos = ["norte", "noreste", "este", "sureste",
                  "sur", "suroeste", "oeste", "noroeste"]
        return puntos[int((origin + 22.5) % 360 // 45)]


def estimate_motion(frames: list[Frame]) -> Motion:
    """Velocidad de traslacion de las celdas, en px/min."""
    if len(frames) < 2:
        return Motion()

    signals = [to_signal(f) for f in frames]
    est: list[tuple[float, float, float]] = []

    for i in range(len(frames) - 1):
        dt = (frames[i + 1].time - frames[i].time).total_seconds() / 60.0
        if dt <= 0:
            continue
        a, b = signals[i], signals[i + 1]
        if max(a.max(), b.max()) < 0.02:
            continue  # cielo despejado: nada que seguir

        h, w = a.shape
        # global + cuatro cuadrantes; la mediana descarta estimaciones locas
        regions = [
            (slice(0, h), slice(0, w)),
            (slice(0, h // 2), slice(0, w // 2)),
            (slice(0, h // 2), slice(w // 2, w)),
            (slice(h // 2, h), slice(0, w // 2)),
            (slice(h // 2, h), slice(w // 2, w)),
        ]
        for ry, rx in regions:
            sub_a, sub_b = a[ry, rx], b[ry, rx]
            if max(sub_a.max(), sub_b.max()) < 0.02:
                continue
            dy, dx, conf = _phase_correlate(sub_a, sub_b)
            if conf <= 0.01:
                continue
            # recencia: los cuadros mas nuevos pesan mas
            recency = (i + 1) / len(frames)
            est.append((dy / dt, dx / dt, conf * recency))

    if not est:
        return Motion()

    arr = np.array(est)
    weights = arr[:, 2]
    # mediana ponderada, robusta a valores atipicos
    vy = _weighted_median(arr[:, 0], weights)
    vx = _weighted_median(arr[:, 1], weights)

    km_per_px = frames[-1].km_per_px
    speed_kmh = math.hypot(vy, vx) * km_per_px * 60.0

    # descartar velocidades no fisicas para conveccion (> 120 km/h)
    if speed_kmh > 120.0:
        scale = 120.0 / speed_kmh
        vy, vx, speed_kmh = vy * scale, vx * scale, 120.0

    # --- el limite de resolucion, que estaba callado
    #
    # Medido el 21 de septiembre de 2026 con 380 tormentas reales: el rumbo
    # del infrarrojo y el de los rayos discrepaban 91 grados de media. Noventa
    # grados es exactamente lo que dan dos angulos INDEPENDIENTES AL AZAR. No
    # eran dos medidas distintas de lo mismo: al menos una no medía nada.
    #
    # El motivo es aritmetica de pixeles. GOES da un cuadro cada 15 minutos con
    # 2.44 km por pixel, asi que una celda a 7 km/h se desplaza 0.7 px entre
    # cuadros. Por debajo de un pixel la correlacion de fase no puede dar una
    # direccion: devuelve el ruido del refinamiento subpixel, y lo devuelve con
    # la misma cara de dato que un desplazamiento de veinte pixeles.
    #
    # Antes el umbral era 2 km/h, o sea 0.2 px: tres cuartas partes de los
    # casos medidos caian en la zona donde el rumbo es inventado. De ahi que el
    # cono apuntara "completamente a otro lado", que es como lo describio
    # Alvaro antes de que ningun numero lo dijera.
    #
    # Ahora se exigen MOTION_MIN_PX pixeles de desplazamiento. Por debajo, el
    # rumbo es None: no hay direccion que dar, y decirlo es la respuesta
    # correcta. Cuesta quedarse sin cono los dias tranquilos; el precio de la
    # alternativa es una flecha segura de si misma apuntando a cualquier lado.
    dt_tipico = 15.0
    desplaz_px = math.hypot(vy, vx) * dt_tipico
    bearing = None
    if desplaz_px >= config.MOTION_MIN_PX:
        # fila crece hacia el sur -> componente norte = -vy
        bearing = (math.degrees(math.atan2(vx, -vy)) + 360.0) % 360.0
    elif speed_kmh > 2.0:
        log.info("movimiento de %.0f km/h = %.1f px en 15 min: por debajo de "
                 "la resolucion, no se publica rumbo", speed_kmh, desplaz_px)

    spread = float(np.std(arr[:, 0]) + np.std(arr[:, 1]))
    agreement = 1.0 / (1.0 + spread * km_per_px * 60.0 / 15.0)
    conf = float(np.clip(np.average(arr[:, 2], weights=weights) * 3.0, 0, 1))

    return Motion(vy_px_min=float(vy), vx_px_min=float(vx),
                  speed_kmh=float(speed_kmh), bearing_deg=bearing,
                  confidence=float(np.clip(conf * agreement, 0.0, 1.0)))


def motion_en(frames: list[Frame], cy: float, cx: float,
              radio_px: float = 40.0) -> Motion:
    """El movimiento ALREDEDOR de un punto, no el del dominio entero.

    Esta funcion existe por la tormenta del 21 de septiembre de 2026. Alvaro
    veia celdas con rayos llegando desde el este mientras el sistema insistia
    en "vienen del oeste". No era un signo invertido ni cizalladura: el archivo
    de descargas mostraba el grueso de la actividad a 100 km al OESTE de la
    ciudad, y esa era la que mandaba en el promedio.

    La ventana del infrarrojo mide 488 km de lado. `estimate_motion` saca UNA
    mediana ponderada sobre todo ese dominio -global mas cuatro cuadrantes- y
    con dos sistemas dentro moviendose distinto, esa mediana no describe a
    ninguno de los dos. Describe un promedio continental que no le pasa por
    encima a nadie.

    Para el dato que de verdad importa -por donde va LA celda que viene- hay
    que medir donde esta esa celda. Eso es lo que hace esto: la misma
    correlacion de fase, sobre un recorte centrado en ella.

    El radio por defecto, 40 px, son ~100 km: suficiente para que una celda a
    50 km/h no se salga del recorte entre cuadros, y bastante mas pequeño que
    la distancia tipica entre sistemas distintos.
    """
    if len(frames) < 2:
        return Motion()
    h, w = frames[-1].data.shape
    r = int(max(16, min(radio_px, min(h, w) / 2)))
    y0, y1 = int(max(0, cy - r)), int(min(h, cy + r))
    x0, x1 = int(max(0, cx - r)), int(min(w, cx + r))
    # Un recorte demasiado pegado al borde no tiene con que correlacionar.
    if (y1 - y0) < 16 or (x1 - x0) < 16:
        return Motion()

    recortes = []
    for f in frames:
        sub = Frame(time=f.time, data=f.data[y0:y1, x0:x1],
                    km_per_px=f.km_per_px, center_lat=f.center_lat,
                    center_lon=f.center_lon, kind=f.kind)
        recortes.append(sub)
    return estimate_motion(recortes)


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values)
    v, w = values[order], weights[order]
    if w.sum() <= 0:
        return float(np.median(values))
    cum = np.cumsum(w) / w.sum()
    return float(v[int(np.searchsorted(cum, 0.5))])


def growth_rate(frames: list[Frame]) -> float:
    """Tendencia de intensidad del dominio: >1 creciendo, <1 disipandose."""
    if len(frames) < 2:
        return 1.0
    means = []
    for f in frames:
        sig = to_signal(f)
        active = sig[sig > 0.05]
        means.append(float(active.mean()) if active.size else 0.0)
    if means[0] <= 1e-6 or means[-1] <= 1e-6:
        return 1.0
    minutes = (frames[-1].time - frames[0].time).total_seconds() / 60.0
    if minutes <= 0:
        return 1.0
    per_30 = (means[-1] / means[0]) ** (30.0 / minutes)
    # limites fisicos: una celda no duplica ni desaparece en media hora
    return float(np.clip(per_30, 0.55, 1.8))


# ---------------------------------------------------------------- prediccion

@dataclass
class LeadResult:
    lead_min: int
    score: float                  # intensidad esperada [0, 1]
    peak_dbz_equiv: float
    hit_radius_km: float          # radio del cono de incertidumbre
    in_domain: bool = True        # False = el origen cae fuera de lo que veo
    visible_fraction: float = 1.0 # cuanto del cono cae dentro de la imagen


@dataclass
class Nowcast:
    kind: str
    motion: Motion
    growth: float
    leads: list[LeadResult] = field(default_factory=list)
    current_score: float = 0.0
    nearest_cell_km: float | None = None
    nearest_cell_eta_min: float | None = None
    nearest_cell_intensity: float = 0.0
    # Donde esta la celda y cuanto puede desviarse antes de llegar. Sin esto,
    # la pagina solo puede escribir "a 31 km del noroeste", que es correcto y
    # no le dice nada a quien no esta acostumbrado a leer un mapa.
    nearest_cell_lat: float | None = None
    nearest_cell_lon: float | None = None
    nearest_cell_radio_km: float | None = None
    # El movimiento medido DONDE ESTA la celda, que puede no tener nada que
    # ver con el del dominio entero.
    nearest_cell_bearing: float | None = None
    nearest_cell_kmh: float = 0.0
    # El camino que seguira la celda, en (lat, lon), si se pudo integrar sobre
    # un campo de movimiento. Puede ser curvo: es lo que permite dibujar una
    # tormenta que gira. Vacio si se uso la recta de siempre.
    nearest_cell_trayectoria: list = field(default_factory=list)
    # ¿El movimiento dominante es un giro? Para no decir "vienen del oeste"
    # cuando nada se traslada.
    giro: bool = False
    valid_time: str = ""

    def score_at(self, lead_min: int) -> float:
        for lead in self.leads:
            if lead.lead_min == lead_min:
                return lead.score
        return 0.0

    def sees(self, lead_min: int) -> bool:
        """¿El sistema realmente alcanza a ver el origen de ese horizonte?"""
        for lead in self.leads:
            if lead.lead_min == lead_min:
                return lead.in_domain
        return False


def _sample_disc(field_arr: np.ndarray, cy: float, cx: float,
                 radius_px: float) -> float:
    """Percentil 90 dentro de un disco: 'que tan fuerte es lo que me puede caer'.

    Usamos p90 y no el maximo porque un solo pixel de ruido no deberia
    disparar una alerta, ni el promedio diluir una celda pequena pero real.
    """
    return _sample_disc_full(field_arr, cy, cx, radius_px)[0]


def _sample_disc_full(field_arr: np.ndarray, cy: float, cx: float,
                      radius_px: float) -> tuple[float, float]:
    """Como _sample_disc, pero devuelve tambien que fraccion del disco es visible.

    La fraccion importa: si el origen de lo que llegaria en 3 h cae fuera de
    la imagen, un score de 0 no significa 'no va a llover', significa
    'no alcanzo a ver'. Confundir ambas cosas es como mienten las apps.
    """
    h, w = field_arr.shape
    r = max(1.0, radius_px)
    y0, y1 = int(max(0, np.floor(cy - r))), int(min(h, np.ceil(cy + r + 1)))
    x0, x1 = int(max(0, np.floor(cx - r))), int(min(w, np.ceil(cx + r + 1)))

    total_area = math.pi * r * r
    if y0 >= y1 or x0 >= x1:
        return 0.0, 0.0

    patch = field_arr[y0:y1, x0:x1]
    yy, xx = np.ogrid[y0:y1, x0:x1]
    mask = (yy - cy) ** 2 + (xx - cx) ** 2 <= r * r
    vals = patch[mask]
    if vals.size == 0:
        return 0.0, 0.0
    visible = min(1.0, vals.size / total_area) if total_area > 0 else 0.0
    return float(np.percentile(vals, 90)), float(visible)


def run_nowcast(frames: list[Frame]) -> Nowcast | None:
    """Extrapola las celdas y evalua que pasa sobre tus coordenadas."""
    if len(frames) < 2:
        return None

    latest = frames[-1]
    signal = to_signal(latest)
    motion = estimate_motion(frames)
    growth = growth_rate(frames)
    cy, cx = latest.center_px
    km_per_px = latest.km_per_px

    nc = Nowcast(kind=latest.kind, motion=motion, growth=growth,
                 valid_time=latest.time.isoformat())
    nc.current_score = _sample_disc(signal, cy, cx, 3.0)

    for lead in config.LEAD_TIMES_MIN:
        # adveccion hacia atras: lo que estara aqui en +lead esta ahora
        # a -v*lead de aqui
        sy = cy - motion.vy_px_min * lead
        sx = cx - motion.vx_px_min * lead

        # cono de incertidumbre: crece con la distancia recorrida y se
        # ensancha mas cuando la estimacion de movimiento es dudosa
        travel_px = math.hypot(motion.vy_px_min, motion.vx_px_min) * lead
        base_px = 5.0 / km_per_px                 # 5 km de error minimo
        spread = 0.25 + 0.5 * (1.0 - motion.confidence)
        radius_px = base_px + travel_px * spread

        raw, visible = _sample_disc_full(signal, sy, sx, radius_px)

        # el factor de crecimiento se amortigua con el plazo: extrapolar una
        # tendencia de 30 min a 3 h no tiene ningun respaldo fisico
        damped_growth = 1.0 + (growth - 1.0) * math.exp(-lead / 90.0)
        adjusted = raw * (damped_growth ** min(lead / 30.0, 3.0))

        skill = math.exp(-lead / 150.0)           # e-folding ~2.5 h
        score = float(np.clip(adjusted * (0.35 + 0.65 * skill), 0.0, 1.0))

        # Aqui hubo, del 21 al 28 de septiembre de 2026, un ajuste del
        # infrarrojo por compacidad del campo y enfriamiento local, para no
        # confundir una celda con el yunque de una tormenta lejana. Se retiro
        # porque no gano: -0.2 puntos de separacion y Brier 0.0366 contra
        # 0.0364 sin el, en 748 casos. La decision estaba tomada de antemano
        # -"si no gana por unos puntos claros, se quita en vez de afinarlo"- y
        # el razonamiento completo quedo en el comentario de PRED_FIELDS.
        # Si alguien vuelve a tener la idea, que empiece leyendo eso.

        dbz_equiv = (config.DBZ_TRACE
                     + score * (config.DBZ_STORM - config.DBZ_TRACE))
        nc.leads.append(LeadResult(
            lead_min=lead, score=score,
            peak_dbz_equiv=round(dbz_equiv, 1),
            hit_radius_km=round(radius_px * km_per_px, 1),
            in_domain=visible >= 0.35,
            visible_fraction=round(visible, 3)))

    _find_incoming_cell(nc, signal, motion, cy, cx, km_per_px, latest, frames)
    return nc


def recolocar_celda(nc: Nowcast, frames: list[Frame], motion: Motion) -> None:
    """Vuelve a elegir la celda que viene, con otro vector de movimiento.

    Existe porque hay dos medidas del movimiento y una es mejor que la otra
    para esta pregunta concreta. La correlacion de fase sobre el infrarrojo
    sigue el TECHO de la nube, que a 10-14 km de altura lo arrastra el viento
    en altura; con cizalladura el yunque se va por su lado mientras la celda
    que llueve va por el suyo. Los rayos salen del nucleo, asi que su deriva
    es la de la tormenta.

    Solo se recalcula la celda y su cono. El resto del pronostico -los scores
    por horizonte- sigue con el movimiento del infrarrojo a proposito: ahi se
    advecta el campo de topes nubosos, y para mover topes nubosos el viento
    de los topes nubosos es el correcto.
    """
    if not frames:
        return
    latest = frames[-1]
    signal = to_signal(latest)
    cy, cx = latest.center_px
    nc.nearest_cell_km = None
    nc.nearest_cell_eta_min = None
    nc.nearest_cell_intensity = 0.0
    nc.nearest_cell_lat = None
    nc.nearest_cell_lon = None
    nc.nearest_cell_radio_km = None
    nc.nearest_cell_bearing = None
    nc.nearest_cell_kmh = 0.0
    nc.nearest_cell_trayectoria = []
    _find_incoming_cell(nc, signal, motion, cy, cx, latest.km_per_px, latest,
                        frames)


def _px_a_latlon(frame, cy, cx, km_per_px, py, px) -> list:
    """Pixel -> [lat, lon], con la misma aproximacion plana de la celda."""
    norte_km = (cy - py) * km_per_px
    este_km = (px - cx) * km_per_px
    coslat = max(0.2, math.cos(math.radians(frame.center_lat)))
    return [round(frame.center_lat + norte_km / 111.0, 4),
            round(frame.center_lon + este_km / (111.0 * coslat), 4)]


def _longitud_km(camino, km_per_px, hasta=None) -> float:
    total = 0.0
    for (t0, y0, x0), (t1, y1, x1) in zip(camino, camino[1:]):
        if hasta is not None and t0 >= hasta:
            break
        total += math.hypot(y1 - y0, x1 - x0) * km_per_px
    return total


def _por_campo(fld, gy, gx, cy, cx, km_per_px, max_eta, confianza):
    """Sigue una celda por el campo y decide si pasa por la ciudad.

    Devuelve (eta, movimiento inicial, camino) o None si no viene. El
    criterio es el mismo de la recta -un pasillo de 15 km mas el 30% de lo
    recorrido-, pero midiendo distancia al camino curvo y recorrido a lo largo
    de el.
    """
    from . import campo as _campo
    camino = _campo.trayectoria(fld, gy, gx, max_eta, paso_min=5.0)
    dist = [math.hypot(y - cy, x - cx) * km_per_px for _t, y, x in camino]
    k = int(np.argmin(dist))
    if k == 0:
        return None                      # ya esta lo mas cerca que va a estar
    t_k = camino[k][0]
    recorrido = _longitud_km(camino, km_per_px, hasta=t_k)
    if dist[k] > 15.0 + 0.3 * recorrido:
        return None                      # pasa de largo
    # El movimiento con que arranca: rumbo y velocidad del primer tramo, que
    # es lo que una persona ve ahora mismo en el cielo.
    (_t0, y0, x0), (t1, y1, x1) = camino[0], camino[1]
    vy, vx = (y1 - y0) / t1, (x1 - x0) / t1
    kmh = math.hypot(vy, vx) * km_per_px * 60.0
    rumbo = ((math.degrees(math.atan2(vx, -vy)) + 360.0) % 360.0
             if math.hypot(vy, vx) * 15 >= config.MOTION_MIN_PX else None)
    inicial = Motion(vy_px_min=vy, vx_px_min=vx, speed_kmh=kmh,
                     bearing_deg=rumbo, confidence=confianza)
    # El camino se corta media hora despues de pasar por la ciudad: lo de
    # mas alla no le importa a nadie aqui y solo alarga el dibujo.
    fin = min(len(camino), k + 7)
    return t_k, inicial, camino[:fin]


def _find_incoming_cell(nc: Nowcast, signal: np.ndarray, motion: Motion,
                        cy: float, cx: float, km_per_px: float,
                        frame=None, frames: list[Frame] | None = None) -> None:
    """Identifica la celda significativa mas cercana que viene hacia ti.

    Cada candidata se evalua con el movimiento medido DONDE ELLA ESTA, no con
    el del dominio entero. La diferencia dejo de ser teorica el 21 de
    septiembre de 2026: con un complejo grande a 100 km al oeste y celdas
    llegando por el este, la mediana global decia "del oeste" -correcto para
    el complejo, inutil para lo que se le venia encima a Alvaro- y el sistema
    publicaba eso con total seguridad.

    Una ventana de 488 km de lado tiene sitio de sobra para dos sistemas que
    van cada uno a lo suyo. Un solo vector para toda ella describe un promedio
    que no le pasa por encima a nadie.
    """
    threshold = 0.35
    mask = signal >= threshold
    if not mask.any():
        return

    # limpiar pixeles sueltos antes de etiquetar
    mask = ndimage.binary_opening(mask, structure=np.ones((3, 3)))
    labels, count = ndimage.label(mask)
    if count == 0:
        return

    # Candidatas: con tamaño suficiente y ordenadas por cercania. Se limita el
    # numero porque cada una cuesta una correlacion de fase mas, y en un Pi 3
    # con el presupuesto de tiempo encima eso no es gratis. Las lejanas
    # tampoco aportan: su ETA caeria fuera del horizonte util de todas formas.
    candidatas = []
    for idx in range(1, count + 1):
        cell = labels == idx
        area_px = int(cell.sum())
        if area_px * (km_per_px ** 2) < 25:   # ignorar celdas < 25 km2
            continue
        gy, gx = ndimage.center_of_mass(cell)
        dist_km = math.hypot(cy - gy, cx - gx) * km_per_px
        candidatas.append((dist_km, float(gy), float(gx),
                           float(signal[cell].mean())))
    candidatas.sort()
    candidatas = candidatas[:config.CELDAS_A_EVALUAR]

    # (eta, dist_km, intensidad, gy, gx, movimiento usado)
    best = None

    # --- el campo de movimiento, si hay nubes suficientes para medirlo
    #
    # Con campo, cada celda se sigue paso a paso por el camino que le marca
    # el sitio donde esta en cada momento: si el sistema gira, el camino
    # gira. Sin campo -poca nube, o un solo cuadro util- se vuelve a la recta
    # de siempre, que para una traslacion limpia da lo mismo.
    from . import campo as _campo
    fld = _campo.medir(frames) if frames else None
    usar_campo = fld is not None and fld.util
    nc.giro = _campo.es_giro(fld) if usar_campo else False
    max_eta = max(config.LEAD_TIMES_MIN) * 1.5

    for dist_km, gy, gx, intensity in candidatas:
        if usar_campo:
            r = _por_campo(fld, gy, gx, cy, cx, km_per_px, max_eta,
                           motion.confidence or 0.5)
            if r is None:
                continue
            eta, usar, camino = r
            if best is None or eta < best[0]:
                best = (eta, dist_km, intensity, gy, gx, usar, camino)
            continue

        # --- el movimiento de ESTA celda, medido donde ella esta
        local = motion_en(frames, gy, gx) if frames else Motion()
        usar = local if local.bearing_deg is not None else motion
        if usar.bearing_deg is None:
            # Ni local ni global saben hacia donde va. Sin rumbo no hay
            # "viene hacia aca", y adivinarlo es inventar lo que importa.
            continue

        speed_px_min = math.hypot(usar.vy_px_min, usar.vx_px_min)
        if speed_px_min < 1e-4:
            continue
        dy, dx = cy - gy, cx - gx
        # proyeccion del vector celda->casa sobre la direccion del viento
        along = (dy * usar.vy_px_min + dx * usar.vx_px_min) / speed_px_min
        if along <= 0:
            continue          # se aleja
        # distancia perpendicular: ¿realmente pasa por encima?
        cross = abs(dy * usar.vx_px_min - dx * usar.vy_px_min) / speed_px_min
        corridor_km = 15.0 + 0.3 * (along * km_per_px)
        if cross * km_per_px > corridor_km:
            continue          # pasa de largo
        eta = along / speed_px_min

        if best is None or eta < best[0]:
            best = (eta, dist_km, intensity, gy, gx, usar, None)

    if best and best[0] <= max_eta:
        nc.nearest_cell_eta_min = round(best[0], 1)
        nc.nearest_cell_km = round(best[1], 1)
        nc.nearest_cell_intensity = round(best[2], 3)
        usado = best[5]
        nc.nearest_cell_bearing = (round(usado.bearing_deg, 1)
                                   if usado.bearing_deg is not None else None)
        nc.nearest_cell_kmh = round(usado.speed_kmh, 1)

        # --- donde esta, en coordenadas, y cuanto puede desviarse
        #
        # La rejilla del infrarrojo esta orientada al norte en esta ventana, y
        # a esta latitud un grado de longitud mide cos(lat) veces uno de
        # latitud. Es una aproximacion plana, y a 100 km el error es de
        # centenares de metros: irrelevante al lado de un cono de
        # incertidumbre que mide decenas de kilometros. Usar algo mas fino
        # seria precision falsa.
        if frame is not None:
            _eta, _d, _i, gy, gx, _m, camino = best
            norte_km = (cy - gy) * km_per_px
            este_km = (gx - cx) * km_per_px
            lat = frame.center_lat + norte_km / 111.0
            coslat = max(0.2, math.cos(math.radians(frame.center_lat)))
            lon = frame.center_lon + este_km / (111.0 * coslat)
            nc.nearest_cell_lat = round(lat, 4)
            nc.nearest_cell_lon = round(lon, 4)
            if camino:
                nc.nearest_cell_trayectoria = [
                    _px_a_latlon(frame, cy, cx, km_per_px, py, px)
                    for _t, py, px in camino]

        # El radio del cono con la MISMA formula que usan los horizontes, para
        # que el dibujo y los numeros no puedan contradecirse: 5 km de error
        # minimo mas lo que se ensancha con el recorrido, y se ensancha mas
        # cuanto menos fiable es la estimacion de movimiento.
        usado = best[5]
        if best[6]:
            # Con camino integrado, lo recorrido es la longitud del camino
            # hasta el punto mas cercano, no una recta: en un giro son cosas
            # muy distintas.
            recorrido_km = _longitud_km(best[6], km_per_px, hasta=best[0])
        else:
            v_px_min = math.hypot(usado.vy_px_min, usado.vx_px_min)
            recorrido_km = v_px_min * km_per_px * best[0]
        spread = 0.25 + 0.5 * (1.0 - usado.confidence)
        nc.nearest_cell_radio_km = round(5.0 + recorrido_km * spread, 1)
