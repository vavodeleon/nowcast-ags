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

    bearing = None
    if speed_kmh > 2.0:
        # fila crece hacia el sur -> componente norte = -vy
        bearing = (math.degrees(math.atan2(vx, -vy)) + 360.0) % 360.0

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

        dbz_equiv = (config.DBZ_TRACE
                     + score * (config.DBZ_STORM - config.DBZ_TRACE))
        nc.leads.append(LeadResult(
            lead_min=lead, score=score,
            peak_dbz_equiv=round(dbz_equiv, 1),
            hit_radius_km=round(radius_px * km_per_px, 1),
            in_domain=visible >= 0.35,
            visible_fraction=round(visible, 3)))

    _find_incoming_cell(nc, signal, motion, cy, cx, km_per_px)
    return nc


def _find_incoming_cell(nc: Nowcast, signal: np.ndarray, motion: Motion,
                        cy: float, cx: float, km_per_px: float) -> None:
    """Identifica la celda significativa mas cercana que viene hacia ti."""
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
    best: tuple[float, float, float] | None = None  # (eta, dist_km, intensidad)

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
            best = (eta, dist_km, intensity)

    if best:
        nc.nearest_cell_eta_min = round(best[0], 1)
        nc.nearest_cell_km = round(best[1], 1)
        nc.nearest_cell_intensity = round(best[2], 3)
