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


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values)
    v, w = values[order], weights[order]
    if w.sum() <= 0:
        return float(np.median(values))
    cum = np.cumsum(w) / w.sum()
    return float(v[int(np.searchsorted(cum, 0.5))])


def _compacidad(signal: np.ndarray, cy: float, cx: float,
                km_per_px: float) -> float:
    """¿Es un nucleo convectivo o un manto liso? 1 nucleo, 0 yunque.

    El problema de fondo del infrarrojo, ya nombrado en `overhead.py` para el
    presente pero no para el pronostico: el IR mide la temperatura del TECHO de
    la nube, y el yunque de una tormenta a 100 km esta igual de frio que la
    celda que la genero. Igual de frio, muchisimo mas extenso, y no moja.

    Con un umbral de temperatura no se distinguen: hay que mirar la FORMA. Un
    nucleo es compacto -muy frio en el centro, bastante mas templado a 60-90 km-
    y un yunque es plano: todo su entorno esta igual de frio.

    Medido el 21 de septiembre de 2026, esto no era una preocupacion teorica: en
    las 36 correcciones humanas el infrarrojo tenia separacion **-19.3%**, o sea
    que decia MAS probabilidad cuando no llovia. Estaba cantando yunques.
    """
    nucleo, _ = _sample_disc_full(signal, cy, cx, max(2.0, 15.0 / km_per_px))
    r_int, r_ext = 60.0 / km_per_px, 95.0 / km_per_px
    h, w = signal.shape
    y0, y1 = int(max(0, cy - r_ext)), int(min(h, cy + r_ext + 1))
    x0, x1 = int(max(0, cx - r_ext)), int(min(w, cx + r_ext + 1))
    if y0 >= y1 or x0 >= x1:
        return 1.0
    yy, xx = np.ogrid[y0:y1, x0:x1]
    d2 = (yy - cy) ** 2 + (xx - cx) ** 2
    anillo = signal[y0:y1, x0:x1][(d2 >= r_int ** 2) & (d2 <= r_ext ** 2)]
    if anillo.size < 20:
        # Sin anillo suficiente no se puede opinar de la forma. Devolver 1.0
        # -"asumimos nucleo"- es lo conservador aqui: deja el score como
        # estaba en vez de castigarlo por falta de datos.
        return 1.0
    contraste = float(nucleo - np.mean(anillo))
    # 0.5 en unidades de senal son ~7.5 K, la mitad del contraste que
    # `overhead.py` exige para llamar nucleo a algo. Escala, no umbral: aqui
    # interesa graduar, no decidir.
    return float(np.clip(contraste / 0.5, 0.0, 1.0))


def _tendencia_lagrangiana(frames: list[Frame], cy: float, cx: float,
                           radius_px: float, motion: Motion,
                           lead: int) -> float:
    """¿La nube que llegara aqui se esta enfriando, o ya venia hecha?

    Se compara la parcela con SIGO MISMA un cuadro antes, siguiendo el
    movimiento -no el pixel-. Esa distincion es la que hace que la cuenta
    signifique algo: mirar el mismo pixel mide el paso de la nube por encima,
    que es la adveccion que el motor ya modela aparte.

    Una celda convectiva enfria su tope varios grados cada quince minutos
    mientras crece. Un yunque ya frio no cambia. Y una celda en disipacion se
    calienta. Es la version LOCAL de `growth_rate`, que promedia el dominio
    entero y por eso no puede distinguir una celda que crece de otra que muere
    a cien kilometros.
    """
    if len(frames) < 2:
        return 0.0
    dt = (frames[-1].time - frames[-2].time).total_seconds() / 60.0
    if dt <= 0:
        return 0.0
    ahora = to_signal(frames[-1])
    antes = to_signal(frames[-2])
    # Donde estaba esa misma parcela un cuadro antes: un dt mas atras en el
    # camino que ya se retrocedio para el plazo.
    sy = cy - motion.vy_px_min * (lead + dt)
    sx = cx - motion.vx_px_min * (lead + dt)
    s_ahora, vis_a = _sample_disc_full(ahora, cy - motion.vy_px_min * lead,
                                       cx - motion.vx_px_min * lead, radius_px)
    s_antes, vis_b = _sample_disc_full(antes, sy, sx, radius_px)
    if vis_a < 0.35 or vis_b < 0.35:
        return 0.0
    return float((s_ahora - s_antes) * (15.0 / dt))


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
    score: float                  # intensidad esperada [0, 1], ya ajustada
    peak_dbz_equiv: float
    hit_radius_km: float          # radio del cono de incertidumbre
    in_domain: bool = True        # False = el origen cae fuera de lo que veo
    visible_fraction: float = 1.0 # cuanto del cono cae dentro de la imagen
    # Lo que decia el brillo a secas, antes de mirar si la nube crece o si es
    # compacta. Se conserva para poder comparar las dos versiones sobre los
    # mismos casos en vez de creer que el ajuste ayudo.
    score_crudo: float = 0.0
    tendencia: float = 0.0        # +: la nube de ese parcela se esta enfriando
    compacidad: float = 1.0       # 1 nucleo compacto, 0 manto liso (yunque)


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
    valid_time: str = ""

    def score_at(self, lead_min: int) -> float:
        for lead in self.leads:
            if lead.lead_min == lead_min:
                return lead.score
        return 0.0

    def score_crudo_at(self, lead_min: int) -> float:
        """El score sin el ajuste por crecimiento y forma. Solo para medir."""
        for lead in self.leads:
            if lead.lead_min == lead_min:
                return lead.score_crudo
        return 0.0

    def detalle_at(self, lead_min: int) -> tuple[float, float]:
        for lead in self.leads:
            if lead.lead_min == lead_min:
                return lead.tendencia, lead.compacidad
        return 0.0, 1.0

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
        score_crudo = float(np.clip(adjusted * (0.35 + 0.65 * skill), 0.0, 1.0))

        # --- ajuste por crecimiento local y forma, solo en el infrarrojo
        #
        # El radar mide lluvia directamente y no tiene el problema del yunque;
        # aplicarle esto seria castigarlo por un defecto que no tiene.
        tendencia, compacidad, factor = 0.0, 1.0, 1.0
        if latest.kind != "radar":
            tendencia = _tendencia_lagrangiana(frames, cy, cx, radius_px,
                                               motion, lead)
            compacidad = _compacidad(signal, sy, sx, km_per_px)
            # Monotono creciente en las dos cosas, y eso es lo unico que hace
            # falta que sea correcto: la isotonica recalibra la mezcla despues,
            # asi que un sesgo global de escala se absorbe solo. Lo que la
            # calibracion NO puede arreglar es el orden, y el orden es lo que
            # esto cambia.
            factor = float(np.clip(0.55 + 0.9 * compacidad + 2.0 * tendencia,
                                   0.40, 1.40))
        # El factor se calcula DENTRO de la rama a proposito. Estaba fuera, con
        # compacidad=1.0 de valor neutro, y eso daba 1.45 -o sea un empujon del
        # 40% al radar, la fuente que no debia tocarse-. Un valor "neutro" que
        # no produce factor 1.0 no es neutro; lo encontro la prueba F.
        score = float(np.clip(score_crudo * factor, 0.0, 1.0))

        dbz_equiv = (config.DBZ_TRACE
                     + score * (config.DBZ_STORM - config.DBZ_TRACE))
        nc.leads.append(LeadResult(
            lead_min=lead, score=score,
            peak_dbz_equiv=round(dbz_equiv, 1),
            hit_radius_km=round(radius_px * km_per_px, 1),
            in_domain=visible >= 0.35,
            visible_fraction=round(visible, 3),
            score_crudo=score_crudo,
            tendencia=round(tendencia, 4),
            compacidad=round(compacidad, 3)))

    _find_incoming_cell(nc, signal, motion, cy, cx, km_per_px, latest)
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
    _find_incoming_cell(nc, signal, motion, cy, cx, latest.km_per_px, latest)


def _find_incoming_cell(nc: Nowcast, signal: np.ndarray, motion: Motion,
                        cy: float, cx: float, km_per_px: float,
                        frame=None) -> None:
    """Identifica la celda significativa mas cercana que viene hacia ti."""
    # Sin rumbo no hay "hacia ti". Elegir una celda igualmente seria inventar
    # la parte que importa: cual de todas viene, y cuando. Mejor no señalar
    # ninguna que señalar una al azar con un cono de aspecto convincente.
    if motion.bearing_deg is None:
        return
    threshold = 0.35
    mask = signal >= threshold
    if not mask.any():
        return

    # limpiar pixeles sueltos antes de etiquetar
    mask = ndimage.binary_opening(mask, structure=np.ones((3, 3)))
    labels, count = ndimage.label(mask)
    if count == 0:
        return

    speed_px_min = math.hypot(motion.vy_px_min, motion.vx_px_min)
    # (eta, dist_km, intensidad, gy, gx)
    best: tuple[float, float, float, float, float] | None = None

    for idx in range(1, count + 1):
        cell = labels == idx
        area_px = int(cell.sum())
        if area_px * (km_per_px ** 2) < 25:   # ignorar celdas < 25 km2
            continue
        gy, gx = ndimage.center_of_mass(cell)
        dy, dx = cy - gy, cx - gx
        dist_km = math.hypot(dy, dx) * km_per_px
        intensity = float(signal[cell].mean())

        if speed_px_min < 1e-4:
            eta = float("inf")
        else:
            # proyeccion del vector celda->casa sobre la direccion del viento
            along = (dy * motion.vy_px_min + dx * motion.vx_px_min) / speed_px_min
            if along <= 0:
                continue          # se aleja
            # distancia perpendicular: ¿realmente pasa por encima?
            cross = abs(dy * motion.vx_px_min - dx * motion.vy_px_min) / speed_px_min
            corridor_km = 15.0 + 0.3 * (along * km_per_px)
            if cross * km_per_px > corridor_km:
                continue          # pasa de largo
            eta = along / speed_px_min

        if eta == float("inf"):
            continue
        if best is None or eta < best[0]:
            best = (eta, dist_km, intensity, float(gy), float(gx))

    # Un ETA mas alla del horizonte util no es informacion, es aritmetica.
    # Una celda a 180 km moviendose a 10 km/h "llega en 18 horas": para
    # entonces se habra disipado y nacido otras tres. Reportarlo seria
    # exactamente el tipo de precision falsa que hace inutiles a las apps.
    max_eta = max(config.LEAD_TIMES_MIN) * 1.5
    if best and best[0] <= max_eta:
        nc.nearest_cell_eta_min = round(best[0], 1)
        nc.nearest_cell_km = round(best[1], 1)
        nc.nearest_cell_intensity = round(best[2], 3)

        # --- donde esta, en coordenadas, y cuanto puede desviarse
        #
        # La rejilla del infrarrojo esta orientada al norte en esta ventana, y
        # a esta latitud un grado de longitud mide cos(lat) veces uno de
        # latitud. Es una aproximacion plana, y a 100 km el error es de
        # centenares de metros: irrelevante al lado de un cono de
        # incertidumbre que mide decenas de kilometros. Usar algo mas fino
        # seria precision falsa.
        if frame is not None:
            _eta, _d, _i, gy, gx = best
            norte_km = (cy - gy) * km_per_px
            este_km = (gx - cx) * km_per_px
            lat = frame.center_lat + norte_km / 111.0
            coslat = max(0.2, math.cos(math.radians(frame.center_lat)))
            lon = frame.center_lon + este_km / (111.0 * coslat)
            nc.nearest_cell_lat = round(lat, 4)
            nc.nearest_cell_lon = round(lon, 4)

        # El radio del cono con la MISMA formula que usan los horizontes, para
        # que el dibujo y los numeros no puedan contradecirse: 5 km de error
        # minimo mas lo que se ensancha con el recorrido, y se ensancha mas
        # cuanto menos fiable es la estimacion de movimiento.
        recorrido_km = speed_px_min * km_per_px * best[0]
        spread = 0.25 + 0.5 * (1.0 - motion.confidence)
        nc.nearest_cell_radio_km = round(5.0 + recorrido_km * spread, 1)
