"""reloj.json: lo mínimo para un widget o un reloj.

`latest.json` pesa ~18 KB y casi todo es para la página: la serie de presión,
la de temperatura, el desempeño por plazo. Un reloj que lo baja cada 15
minutos gasta batería en datos que no va a mostrar. Este archivo trae solo lo
que cabe en una pantalla chica, en menos de 1 KB.

## El veredicto se decide aquí, no en cada cliente

La frase de arriba -«No va a llover», «Lluvia en ~40 min»- la calculaba solo
la página, en JavaScript. Si el widget y el reloj la recalcularan cada uno en
Kotlin, habría tres copias de la misma regla, y tarde o temprano una diría
«Puede llover» mientras otra dice «Poco probable». Así que se calcula una vez,
aquí, con la misma lógica que la página, y los clientes solo la muestran.

`test_reloj.py` ejecuta la función de la página (con node) y esta sobre los
mismos casos y exige que coincidan. Si alguien cambia una sin la otra, la
suite lo dice.

## Contrato

Versión en "v". Los clientes ignoran claves que no conocen; una clave que
cambie de significado sube la versión. "No sé" es null, nunca 0.
"""
from __future__ import annotations

import math

VERSION = 1

PLAZOS = (15, 30, 45, 60, 90, 120, 180)

_PUNTOS = ["norte", "noreste", "este", "sureste",
           "sur", "suroeste", "oeste", "noroeste"]


def _hay(v) -> bool:
    if v is None:
        return False
    try:
        return not math.isnan(float(v))
    except (TypeError, ValueError):
        return False


def _redondea(x: float) -> int:
    """Math.round de JavaScript: .5 sube siempre. round() de Python no."""
    return int(math.floor(float(x) + 0.5))


def veredicto(d: dict) -> dict:
    """Copia fiel de `veredicto(d)` de docs/index.html.

    Devuelve {icono, titular, apoyo, color}; `color` es el nombre de la
    variable CSS sin el prefijo (rojo, naranja, amarillo, azul, verde, muted).
    """
    probs = d.get("probabilities") or {}
    p60 = probs.get("60")
    vals = [float(v) for v in probs.values() if _hay(v)]
    eta = d.get("cell_eta_min")
    a = d.get("ahora") or {}

    def r(ic, t, s, c):
        return {"icono": ic, "titular": t, "apoyo": s, "color": c}

    if not vals:
        return r("🛰️", "Sin datos ahora",
                 "Ninguna fuente respondió en la última corrida.", "muted")
    if a.get("lloviendo"):
        return r("🌧️", "Está lloviendo",
                 f"Confirmado por {a.get('corroborado_por')}.", "azul")
    if a.get("forma") == "yunque":
        return r("☁️", "Nublado, no lloviendo",
                 "Es nube alta extendida desde una tormenta lejana. No moja.",
                 "muted")
    if _hay(eta) and float(eta) <= 90 and _hay(p60) and float(p60) >= .45:
        return r("⛈️", f"Lluvia en ~{_redondea(eta)} min",
                 f"Viene una celda desde el {d.get('motion_from')}, a "
                 f"{_redondea(d.get('motion_speed_kmh') or 0)} km/h.", "rojo")
    mx = max(vals)
    if _hay(p60) and float(p60) >= .70:
        return r("🌧️", "Va a llover", "Alta probabilidad en la próxima hora.", "rojo")
    if _hay(p60) and float(p60) >= .45:
        return r("🌦️", "Puede llover", "Conviene llevar paraguas por si acaso.", "naranja")
    if mx >= .45:
        return r("☁️", "Sin lluvia por ahora",
                 "Pero hay celdas cerca; la cosa puede cambiar.", "amarillo")
    if _hay(p60) and float(p60) >= .25:
        return r("🌤️", "Poco probable", "No parece que vaya a llover pronto.", "azul")
    return r("☀️", "No va a llover", "Despejado en las próximas horas.", "verde")


def _desde(rumbo) -> str | None:
    """Rumbo de avance (hacia dónde va) -> de dónde viene, en 8 puntos."""
    if not _hay(rumbo):
        return None
    origen = (float(rumbo) + 180.0) % 360.0
    return _PUNTOS[int((origen + 22.5) % 360 // 45)]


def compacto(d: dict) -> dict:
    """De latest.json a reloj.json."""
    probs = d.get("probabilities") or {}
    celda = d.get("celda") or None
    c = None
    if celda and _hay(celda.get("km")):
        c = {
            "eta_min": (_redondea(celda["eta_min"])
                        if _hay(celda.get("eta_min")) else None),
            "km": _redondea(celda["km"]),
            # El de la celda si lo hay; si no, el general. Igual que el cono.
            "desde": _desde(celda.get("rumbo")) or d.get("motion_from"),
            "intensidad": celda.get("intensidad"),
        }
    nb = d.get("nube_baja") or {}
    frac = nb.get("frac")
    issued_local = d.get("issued_local") or ""
    return {
        "v": VERSION,
        "emitido_utc": d.get("issued_utc"),
        # Solo la hora: es lo que cabe y lo que se lee de un vistazo.
        "hora": issued_local[-5:] if issued_local else None,
        **veredicto(d),
        "p": [{"min": m,
               "p": (round(float(probs[str(m)]), 3)
                     if _hay(probs.get(str(m))) else None)}
              for m in PLAZOS if str(m) in probs],
        "p60": (round(float(probs["60"]), 3) if _hay(probs.get("60")) else None),
        "celda": c,
        "giro": bool(d.get("motion_giro")),
        "nube_baja": (None if not _hay(frac) else float(frac) >= 0.3),
        "degradado": bool(d.get("degradado")),
    }
