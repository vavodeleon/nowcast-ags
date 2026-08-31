"""Distinguir el yunque de una tormenta lejana de una celda que si llueve.

Este archivo existe por un falso positivo real: el sistema decia "esta
lloviendo" cuando solo estaba nublado. El infrarrojo no puede distinguir por
temperatura una celda activa del yunque de una tormenta a 100 km -los dos
estan igual de frios- pero si por la FORMA del campo.
"""
import sys
from datetime import datetime, timezone
import numpy as np
sys.path.insert(0, ".")
ok = True
def chk(n, c, d=""):
    global ok
    print(f"  {'PASA' if c else 'FALLA'}  {n}" + (f"  [{d}]" if d else ""))
    if not c: ok = False

from nowcast import overhead, config
from nowcast.sources import Frame

KM = 2.45
N = 200

def campo(fn):
    yy, xx = np.mgrid[0:N, 0:N].astype(float)
    d_km = np.hypot(yy - N/2, xx - N/2) * KM
    return Frame(time=datetime.now(timezone.utc), data=fn(d_km).astype(np.float32),
                 km_per_px=KM, center_lat=config.LAT, center_lon=config.LON, kind="ir")

print("A. Yunque: frio, amplio y liso (NO llueve)")
# 222 K uniforme en 200 km: igual de frio que una tormenta, pero sin estructura
yunque = campo(lambda d: np.where(d < 200, 222.0, 265.0))
a = overhead.assess(yunque, [], None)
print(f"     BT {a.bt_encima:.0f} K · contraste {a.contraste:.1f} K · frio alrededor {a.fraccion_fria:.0%}")
chk("lo reconoce como yunque", a.forma == "yunque", a.forma)
chk("NO dice que llueve", a.lloviendo is False)
chk("lo explica en palabras", "no lloviendo" in a.estado.lower() or "lejana" in a.estado.lower(), a.estado)

print("\nB. Nucleo convectivo: frio, compacto y con contraste (SI llueve)")
nucleo = campo(lambda d: np.clip(205 + d * 0.75, 205, 272))
a = overhead.assess(nucleo, [], None)
print(f"     BT {a.bt_encima:.0f} K · contraste {a.contraste:.1f} K · frio alrededor {a.fraccion_fria:.0%}")
chk("lo reconoce como nucleo", a.forma == "nucleo", a.forma)
chk("SI dice que llueve", a.lloviendo is True)
chk("dice por que", "núcleo" in a.corroborado_por or "nucleo" in a.corroborado_por,
    a.corroborado_por)

print("\nC. Cielo despejado")
a = overhead.assess(campo(lambda d: np.full_like(d, 288.0)), [], None)
chk("no inventa nubes", a.forma == "despejado", a.forma)
chk("no dice que llueve", a.lloviendo is False)
chk("lo dice claro", a.estado == "Despejado", a.estado)

print("\nD. Nube media, ni frio ni despejado")
a = overhead.assess(campo(lambda d: np.full_like(d, 250.0)), [], None)
chk("nublado sin alarma", a.forma == "nubes" and not a.lloviendo, f"{a.forma}/{a.lloviendo}")

print("\nE. Corroboracion independiente")
# el mismo yunque, pero con rayos justo encima -> ahora si es tormenta
bloques = [{"edad_min": 3, "puntos": [[config.LAT + 0.05, config.LON, 12]]}]
a = overhead.assess(yunque, bloques, None)
chk("rayos cerca confirman lluvia", a.lloviendo is True)
chk("cuenta los rayos", a.rayos_cerca == 12, str(a.rayos_cerca))
chk("cita la corroboracion", "rayos" in a.corroborado_por, a.corroborado_por)

# rayos lejanos NO deben confirmar nada
lejos = [{"edad_min": 3, "puntos": [[config.LAT + 3.0, config.LON + 3.0, 99]]}]
a = overhead.assess(yunque, lejos, None)
chk("rayos lejanos no cuentan", a.rayos_cerca == 0 and not a.lloviendo,
    f"{a.rayos_cerca} rayos")

# rayos viejos tampoco
viejos = [{"edad_min": 55, "puntos": [[config.LAT, config.LON, 99]]}]
chk("rayos de hace una hora no cuentan",
    overhead.assess(yunque, viejos, None).rayos_cerca == 0)

# precipitacion observada si confirma
a = overhead.assess(yunque, [], 1.4)
chk("precipitacion observada confirma", a.lloviendo is True)
chk("la cita con los milimetros", "mm observados" in a.corroborado_por, a.corroborado_por)
chk("una traza no confirma", overhead.assess(yunque, [], 0.05).lloviendo is False)

print("\nF. Sin satelite")
a = overhead.assess(None, [], 2.0)
chk("solo con lluvia observada basta", a.lloviendo is True)
chk("sin nada, no afirma nada", overhead.assess(None, [], None).lloviendo is False)

print("\nG. Lectura de tus correcciones")
from nowcast import feedback
casos = [
    ("lluvia: si @ 2026-08-18T14:30:00Z", 1),
    ("lluvia: no @ 2026-08-18T14:30:00Z", 0),
    ("lluvia: sí @ 2026-08-18T14:30:00Z", 1),
    ("LLUVIA: NO @ 2026-08-18T14:30:00Z\n\nEl sistema decía: Está lloviendo", 0),
]
for texto, esperado in casos:
    m = feedback._CUERPO_RE.search(texto)
    got = None if not m else (0 if m.group(1).lower() == "no" else 1)
    chk(f"lee «{texto.splitlines()[0][:34]}»", got == esperado, str(got))
chk("ignora texto sin formato", feedback._CUERPO_RE.search("hola que tal") is None)

print("\nH. Lo manual manda sobre lo automatico")
from nowcast import verify, store
import inspect
src = inspect.getsource(verify.run)
chk("verify respeta las observaciones manuales", "manual" in src)



# ---------------- el falso positivo real del 25 de agosto
print("\nI. El caso que reportaste: cielo despejado, decia que llovia")
# Reproduccion: cielo templado encima (sin nube alta) pero Open-Meteo colando
# un pronostico de 0.2 mm como si fuera observacion.
despejado = campo(lambda d: np.full_like(d, 285.0))
a = overhead.assess(despejado, [], 0.2)
chk("con cielo despejado NO puede llover", a.lloviendo is False,
    f"lloviendo={a.lloviendo} razon={a.corroborado_por!r}")
chk("lo reporta como despejado", a.estado == "Despejado", a.estado)

# ni siquiera una lluvia observada fuerte vence al veto
a = overhead.assess(despejado, [], 5.0)
chk("5 mm 'observados' con cielo limpio no vencen al veto", a.lloviendo is False)

# ni rayos mal ubicados
bloques = [{"edad_min": 2, "puntos": [[config.LAT, config.LON, 30]]}]
a = overhead.assess(despejado, bloques, None)
chk("ni rayos con cielo limpio", a.lloviendo is False, a.corroborado_por)

# nube media (250 K): tampoco basta para corroborar
media = campo(lambda d: np.full_like(d, 252.0))
a = overhead.assess(media, [], 1.0)
chk("nube media no basta para corroborar", a.lloviendo is False, a.estado)

# pero con nube alta de verdad, la lluvia observada SI cuenta
alta = campo(lambda d: np.where(d < 150, 232.0, 258.0))
a = overhead.assess(alta, [], 1.0)
chk("con nube alta, 1 mm observado si confirma", a.lloviendo is True,
    a.corroborado_por)
chk("cita los milimetros", "mm" in a.corroborado_por, a.corroborado_por)

# umbral de traza
a = overhead.assess(alta, [], 0.15)
chk("0.15 mm sigue siendo traza", a.lloviendo is False, a.corroborado_por)

print("\nJ. La lluvia 'observada' no puede venir del futuro")
import inspect
from nowcast import sources as _s
src = inspect.getsource(_s.precip_hora_actual)
chk("pide forecast_hours=0", "forecast_hours=0" in src)
chk("descarta explicitamente el futuro", "t > ahora" in src)
chk("run.py ya no toma el maximo de una ventana",
    "max(vals)" not in inspect.getsource(__import__("nowcast.run", fromlist=["run"]).build_forecast))



# ---------------------------------------------------------------------------
print("\nZ. El aviso no puede anunciar el futuro ignorando el presente")
# Caso real, 30 de agosto de 2026, tormenta con granizo:
#
#   19:45  ahora: Esta lloviendo | BT 244 K
#   20:02  notificacion enviada: "Lluvia probable - en ~90 min"
#
# Diecisiete minutos despues de escribir en su propio registro que estaba
# lloviendo, el sistema aviso de lluvia para dentro de hora y media. La causa
# era que maybe_alert solo leia 'probabilities' y nunca 'ahora'.
from nowcast import config as _cfg, notify as _nt   # noqa: E402

_enviados: list = []
_nt.send = lambda titulo, cuerpo, **kw: (_enviados.append((titulo, cuerpo)), True)[1]
_nt._cooldown_ok = lambda clave, minutos=None: True
_nt._mark = lambda clave: None
_cfg.NTFY_TOPIC = "canal-de-prueba"

LLOVIENDO = {
    "probabilities": {"30": 0.4, "60": 0.51, "90": 0.6, "120": 0.5, "180": 0.4},
    "cell_eta_min": None, "motion_speed_kmh": 35, "motion_from": "noroeste",
    "confidence": "baja",
    "ahora": {"lloviendo": True, "estado": "Está lloviendo",
              "corroborado_por": "lluvia medida"},
    "rayos": {"fase": "encima", "dist_km": 10.0},
}

_enviados.clear()
_nt.maybe_alert(LLOVIENDO)
chk("con lluvia en curso, avisa", len(_enviados) == 1, f"{len(_enviados)}")
titulo, cuerpo = _enviados[-1] if _enviados else ("", "")
chk("el titulo NO promete lluvia para dentro de un rato",
    "min" not in titulo, titulo)
chk("el titulo dice que esta lloviendo",
    "lloviendo" in titulo.lower(), titulo)
chk("reconoce la tormenta encima", "Tormenta" in titulo, titulo)
chk("el cuerpo lo dice primero",
    cuerpo.splitlines()[0].startswith("Está lloviendo"), cuerpo.splitlines()[0])
chk("y menciona los rayos cerca", "10 km" in cuerpo, cuerpo)

print("\n   El mensaje que habria llegado anoche:")
print(f"     {titulo}")
for linea in cuerpo.splitlines():
    if linea.strip():
        print(f"       {linea}")

# Sin lluvia en curso, el comportamiento de siempre.
SECO = dict(LLOVIENDO, ahora={"lloviendo": False, "estado": "Despejado"},
            rayos={"fase": "despejado"}, cell_eta_min=40)
_enviados.clear()
_nt.maybe_alert(SECO)
titulo2 = _enviados[-1][0] if _enviados else ""
chk("sin lluvia, sigue hablando del futuro", "min" in titulo2, titulo2)
chk("y no dice que este lloviendo", "lloviendo" not in titulo2.lower(), titulo2)

# Lluvia en curso con pronostico bajo: igual hay que decirlo.
FLOJO = dict(LLOVIENDO, probabilities={"30": 0.1, "60": 0.1, "90": 0.1})
_enviados.clear()
_nt.maybe_alert(FLOJO)
chk("lluvia en curso avisa aunque el pronostico sea bajo",
    len(_enviados) == 1, f"{len(_enviados)}")

print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
