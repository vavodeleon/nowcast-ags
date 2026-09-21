"""Con el enlace lento, la corrida degrada en vez de morir.

Medido el 31 de agosto de 2026 con la subida de casa saturada: una corrida
que normalmente tarda 70 s tardo **893 s**. El servicio muere a los 900
(`TimeoutStartSec`), asi que estuvo a siete segundos de que systemd la matara
a media ejecucion, posiblemente con git a medio confirmar.

Subir el limite habria sido la respuesta facil y equivocada: el temporizador
dispara cada 15 minutos y systemd no arranca una corrida nueva mientras la
anterior sigue viva, asi que alargarla solo cambia el sintoma -de "se muere"
a "actualiza cada media hora"-.

La respuesta correcta es renunciar a lo opcional. Los rayos son ~45 de las
~53 peticiones de una corrida; media capa de rayos publicada a tiempo vale
mucho mas que un pronostico completo que nunca llega.
"""
from __future__ import annotations

import sys
import time

from nowcast import config, lightning

ok = True


def chk(nombre: str, condicion: bool, detalle: str = "") -> None:
    global ok
    print(f"  {'PASA' if condicion else 'FALLA'}  {nombre}"
          + (f"  [{detalle}]" if detalle else ""))
    if not condicion:
        ok = False


print("A. Sin límite, se leen todos los archivos (y ninguno dos veces)")
# El listado se consulta para DOS horas y el simulacro devuelve la misma
# lista en ambas. Que salgan 45 y no 90 comprueba que no se descarga dos
# veces la misma clave, que con el enlace lento costaria el doble.
# Se simula el bucket: 45 archivos, cada descarga tarda un poco.
ARCHIVOS = 45
pedidos: list[str] = []
RETRASO = [0.0]


def falso_listado(hora):
    """45 archivos dentro de la ventana, del mas nuevo al mas viejo.

    Se restan 5 segundos al mas reciente a proposito: este listado se
    construye DESPUES de que fetch_recent calculo su propio 'ahora', asi que
    un archivo con marca exacta de ahora quedaria en el futuro y el filtro
    lo descartaria. Es un artefacto del simulacro, no del codigo.
    """
    from datetime import datetime, timedelta, timezone
    base = datetime.now(timezone.utc) - timedelta(seconds=5)
    return [(base - timedelta(seconds=15 * i), f"clave-{i}")
            for i in range(ARCHIVOS)]


def falso_get(url, **kw):
    pedidos.append(url)
    if RETRASO[0]:
        time.sleep(RETRASO[0])
    return b""          # _flashes devuelve [] con esto, y no pasa nada


lightning._list_glm = falso_listado
lightning.http.get_bytes = falso_get
lightning._flashes = lambda raw: []

pedidos.clear()
lightning.fetch_recent(15)
chk("se piden los 45 archivos", len(pedidos) == ARCHIVOS, f"{len(pedidos)}")

print("\nB. Con el presupuesto agotado, se corta y se devuelve lo que hay")
pedidos.clear()
RETRASO[0] = 0.01
limite = time.monotonic() + 0.05        # da para unos pocos, no para 45
puntos = lightning.fetch_recent(15, limite=limite)
chk("no se piden los 45", len(pedidos) < ARCHIVOS, f"{len(pedidos)} de {ARCHIVOS}")
chk("pero sí se pidió alguno", len(pedidos) > 0, f"{len(pedidos)}")
chk("no lanza excepción, devuelve lista", isinstance(puntos, list))
RETRASO[0] = 0.0

print("\nC. Se leen los archivos MÁS RECIENTES primero")
# Si hay que cortar, lo que se pierde debe ser lo antiguo: para saber dónde
# está la tormenta ahora, el archivo de hace 15 minutos no aporta casi nada.
pedidos.clear()
RETRASO[0] = 0.01
lightning.fetch_recent(15, limite=time.monotonic() + 0.05)
RETRASO[0] = 0.0
# 'clave-0' es el más reciente en el listado falso.
chk("el primero pedido es el más reciente",
    pedidos and pedidos[0].endswith("clave-0"),
    pedidos[0].split("/")[-1] if pedidos else "ninguno")

print("\nD. Un límite ya vencido no descarga nada")
pedidos.clear()
lightning.fetch_recent(15, limite=time.monotonic() - 1)
chk("cero peticiones", len(pedidos) == 0, f"{len(pedidos)}")

print("\nE. El presupuesto deja margen sobre el límite de systemd")
# 900 s es TimeoutStartSec en deploy/nowcast.service. El presupuesto tiene
# que dejar sitio para lo que viene DESPUÉS de agotarse: render, publicar,
# git push. Si no, degradar no sirve de nada.
servicio = open("deploy/nowcast.service", encoding="utf-8").read()
import re
m = re.search(r"TimeoutStartSec=(\d+)", servicio)
tope = int(m.group(1)) if m else 0
chk("el servicio tiene un límite declarado", tope > 0, f"{tope} s")
chk("el presupuesto es holgadamente menor",
    config.PRESUPUESTO_S < tope * 0.6,
    f"presupuesto {config.PRESUPUESTO_S} s de {tope} s")
print(f"     margen para render, commit y push: {tope - config.PRESUPUESTO_S} s")

print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
