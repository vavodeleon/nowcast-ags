"""Configuración central del sistema de nowcasting."""
from __future__ import annotations

import os
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------- ubicación
# Zona sur de Aguascalientes. Es el punto medio de la franja donde suelo estar,
# redondeado a dos decimales a propósito: eso lo deja sobre una rejilla de
# ~1 km, así que señala una zona de la ciudad y no un domicilio.
#
# No se pierde nada por redondear. El píxel de GOES aquí mide 2.44 km y el
# cono de incertidumbre del nowcast arranca en 5 km, así que publicar más
# decimales sería fingir una precisión que el satélite no tiene.
LAT = 21.84
LON = -102.28
TZ = ZoneInfo("America/Mexico_City")
ELEVATION_M = 1880

# ---------------------------------------------------------------- dominio espacial
# Radio del "campo de visión" alrededor de casa. A 60 km/h una celda recorre
# 180 km en 3 h, así que 300 km de radio cubre el horizonte de 0-3 h con margen.
DOMAIN_RADIUS_KM = 300

# RainViewer: zoom 7 es el máximo público. Con tiles de 512 px eso da
# ~570 m/píxel a esta latitud, o sea ~290 km de ancho de imagen.
RV_ZOOM = 7
RV_SIZE = 512
RV_COLOR_SCHEME = 2  # "Universal Blue": el único esquema publicado, con tabla dBZ exacta
RV_SMOOTH = 0        # sin blur: el suavizado destruye los gradientes del flujo óptico
RV_SNOW = 0
RV_COLORS_CSV_URL = "https://www.rainviewer.com/files/rainviewer_api_colors_table.csv"
RV_COLORS_CACHE = None  # se define abajo, junto a DATA_DIR

# GOES-19 banda 13 (infrarrojo limpio, 10.3 µm) desde el bucket público de NOAA.
# Es la FUENTE PRINCIPAL: Aguascalientes está en un hueco de la red de radar,
# así que el satélite es lo único que ve la ciudad de forma fiable.
# A esta latitud el píxel de 2 km en el nadir se estira a ~3 km, así que
# 200 píxeles ≈ 600 km de ancho: sobra para el horizonte de 3 h.
IR_WINDOW_PX = 200

# Cobertura de radar: se mide EN el punto y en el corredor de donde vienen
# las celdas, no promediando todo el dominio. Promediar daba ~40% de cobertura
# para una ubicación que en realidad tiene cero.
RADAR_HOME_RADIUS_KM = 25.0

# ---------------------------------------------------------------- rayos
# GLM: el detector de relámpagos que va a bordo del mismo GOES-19.
LIGHTNING_RADIUS_DEG = 3.0     # ±3° ≈ ±330 km, igual que la ventana del mapa
LIGHTNING_HISTORY_MIN = 60     # una hora, en cuatro bloques de 15 min

# --- Avisos de tormenta electrica -------------------------------------
# Pensados para dar tiempo a preparar a un animal que se asusta con los
# truenos. Las distancias no son arbitrarias:
#   ~25 km es el alcance tipico del trueno audible. Mas alla se ve el
#   relampago pero rara vez se oye, y lo que asusta al gato es el ruido.
#   ~60 km da alrededor de una hora de margen con una celda moviendose a
#   40-50 km/h, que es lo normal en tormenta de verano.
RAYOS_LEJOS_KM = 60.0          # empieza a vigilar y avisa si se acerca
RAYOS_CERCA_KM = 25.0          # el trueno ya se oye
# Histeresis: para SALIR de un estado hace falta mas margen que para
# entrar. Sin esto, una celda oscilando alrededor del umbral mandaria
# avisos cada 15 minutos.
RAYOS_SALIR_CERCA_KM = 35.0
RAYOS_SALIR_LEJOS_KM = 75.0
RAYOS_DESPEJADO_MIN = 30       # silencio necesario para dar el "ya paso"
RAYOS_COOLDOWN_H = 1.5         # entre avisos del mismo tipo

# ---------------------------------------------------------------- horizontes
LEAD_TIMES_MIN = [15, 30, 45, 60, 90, 120, 180]

# ---------------------------------------------------------------- umbrales físicos
# dBZ: 20 = llovizna detectable, 35 = lluvia moderada, 45+ = tormenta fuerte
DBZ_TRACE = 20.0
DBZ_RAIN = 30.0
DBZ_STORM = 45.0

# Temperatura de brillo IR (K). Topes nubosos por debajo de 235 K (-38 °C)
# indican convección profunda; por debajo de 220 K, tormenta madura.
IR_CONVECTIVE_K = 235.0
IR_DEEP_K = 220.0

# ---------------------------------------------------------------- alertas
ALERT_PROB_THRESHOLD = 0.55   # probabilidad calibrada mínima para molestarte
ALERT_MAX_ETA_MIN = 90        # solo avisa si llega dentro de este plazo
ALERT_COOLDOWN_MIN = 45       # no repetir la misma alerta antes de esto

NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
# Canal aparte para las alertas de presión: así ella recibe solo eso y no
# las alertas de tormenta.
NTFY_TOPIC_SALUD = os.environ.get("NTFY_TOPIC_SALUD", "")

# ---------------------------------------------------------------- presión
# Umbrales en hPa de presión reducida a NIVEL DEL MAR. Ojo: la presión de
# estación aquí ronda los 805 hPa por los 1,880 m de altitud; los valores de
# la literatura están en nivel del mar y mezclarlos no tendría sentido.
#
# La evidencia asocia caídas de 5–10 hPa en 12–24 h con más ataques de
# migraña en personas susceptibles, aunque la sensibilidad individual varía
# mucho y los estudios no son unánimes.
PRESSURE_DROP_WATCH = 3.0     # vigilancia
PRESSURE_DROP_24H = 5.0       # riesgo alto: umbral principal
PRESSURE_DROP_SEVERE = 8.0    # riesgo muy alto
PRESSURE_DROP_3H = 2.5        # caída rápida en curso
PRESSURE_LOOKAHEAD_H = 36     # cuánto futuro se revisa buscando caídas

PRESSURE_ALERT_COOLDOWN_H = 20   # aviso anticipado: como mucho uno al día
PRESSURE_LIVE_COOLDOWN_H = 6     # confirmación en tiempo real

# ---------------------------------------------------------------- reporte diario
# El reporte matutino NO usa un cron propio: los cron de GitHub no se cumplen
# (medido: el cron diario no disparó ni una vez en su primer día). En su lugar,
# el ciclo del nowcast comprueba en cada pasada si toca enviarlo.
MORNING_HOUR = 6
MORNING_MINUTE = 30
MORNING_WINDOW_END_HOUR = 10   # si el sistema estuvo caído, aún lo manda

# ---------------------------------------------------------------- modelos numéricos
# Pesos iniciales por fuente. El IR arranca dominando porque el radar no
# cubre la ciudad; el sistema los ajusta solo con los aciertos acumulados.
DEFAULT_SOURCE_WEIGHTS = {"radar": 0.15, "ir": 0.60, "models": 0.25}

# Se consultan todos y el sistema aprende a cuál hacerle caso en Aguascalientes.
OPENMETEO_MODELS = [
    "ecmwf_ifs025",
    "gfs_seamless",
    "icon_seamless",
    "gem_seamless",
    "meteofrance_seamless",
]

# ---------------------------------------------------------------- rutas
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
PREDICTIONS_CSV = os.path.join(DATA_DIR, "predictions.csv")
OBSERVATIONS_CSV = os.path.join(DATA_DIR, "observations.csv")
CALIBRATION_JSON = os.path.join(DATA_DIR, "calibration.json")
RV_COLORS_CACHE = os.path.join(DATA_DIR, "rainviewer_colors.csv")
STATE_JSON = os.path.join(DATA_DIR, "state.json")
LATEST_JSON = os.path.join(ROOT, "docs", "latest.json")
HISTORY_JSON = os.path.join(ROOT, "docs", "history.json")
LIGHTNING_JSON = os.path.join(ROOT, "docs", "rayos.json")

HTTP_TIMEOUT = 30
HTTP_RETRIES = 3
USER_AGENT = "nowcast-ags/1.0 (personal precipitation nowcasting)"
