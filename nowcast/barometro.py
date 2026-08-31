"""El barometro fisico de la malla LoRa, como fuente local de presion.

El servicio de Meshtastic que corre en este mismo Raspberry guarda las
lecturas del sensor en `~/clima/clima.db`. Aqui se leen, en modo estricto de
solo lectura, para usarlas como fuente primaria de presion.

## Por que vale la pena, si ya hay presion de Open-Meteo

Open-Meteo entrega un **modelo interpolado** a la coordenada: resolucion de
kilometros, cadencia de una hora, y con el retraso propio de un modelo. El
sensor mide **el aire de esta casa, ahora**, cada 60 segundos, y sigue
midiendo aunque se caiga el internet. Para una migrana lo que importa es lo
segundo. Y la cadencia de un minuto frente a una hora cambia de verdad lo que
se puede detectar: un frente que baja 2 hPa en cuarenta minutos es un solo
salto ilegible en la serie horaria, y una pendiente clara en la del sensor.

## Las dos trampas

**Presion de estacion, no de nivel del mar.** El sensor marca ~815 hPa porque
Aguascalientes esta a unos 1880 m. Open-Meteo entrega ~1015 hPa porque reduce
a nivel del mar. Mezclarlas sin convertir daria un salto de 200 hPa que el
detector de frentes leeria como una catastrofe.

La conversion no se hace con la formula barometrica y una altitud supuesta:
el resultado dependeria de un numero que nadie midio y del error de
calibracion del sensor. Se estima el factor **comparando las dos series en el
periodo que se solapan**. Asi el sistema se calibra solo, y si algun dia se
mueve el sensor de sitio, se recalibra sin que nadie toque nada.

Para lo que de verdad importa -cuanto y que tan rapido cambia- el factor es
casi irrelevante: ronda 1.24, asi que una caida de 2 hPa medida en el sensor
son 2.5 hPa a nivel del mar. Pero se aplica igual, porque los umbrales estan
escritos en unidades de nivel del mar.

**Ruido.** Un BMP280 tiene ruido de decimas de hPa entre muestras
consecutivas. Con 60 muestras por hora, tomar una sola como "el valor de
ahora" invita a falsos positivos. Se usa la mediana de una ventana de
minutos, que es lo que el runbook de la malla ya recomendaba para sus propias
alertas.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone

from . import config

log = logging.getLogger(__name__)

VENTANA_MEDIANA_MIN = 10


def _mediana(valores: list[float]) -> float | None:
    if not valores:
        return None
    orden = sorted(valores)
    n = len(orden)
    return orden[n // 2] if n % 2 else (orden[n // 2 - 1] + orden[n // 2]) / 2


def leer(horas: int = 30) -> list[tuple[datetime, float]]:
    """Lecturas (instante, hPa de estacion) de las ultimas 'horas'.

    Devuelve lista vacia ante cualquier problema, nunca lanza: esta es una
    fuente opcional y el sistema tiene que seguir funcionando sin ella.
    """
    ruta = config.CLIMA_DB
    if not ruta or not os.path.exists(ruta):
        return []

    desde = int((datetime.now(timezone.utc) - timedelta(hours=horas)).timestamp())
    try:
        # mode=ro es importante: la base la escribe otro servicio y no
        # queremos ni crearla si falta ni bloquearlo si esta ocupado.
        con = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True, timeout=2.0)
        try:
            filas = con.execute(
                "select ts, presion from observaciones "
                "where presion is not null and ts >= ? order by ts",
                (desde,)).fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        log.info("no se pudo leer el barometro de la malla: %s", exc)
        return []

    salida = []
    for ts, presion in filas:
        try:
            salida.append((datetime.fromtimestamp(int(ts), timezone.utc),
                           float(presion)))
        except (TypeError, ValueError):
            continue
    return salida


def valor_en(serie: list[tuple[datetime, float]], objetivo: datetime,
             ventana_min: int = VENTANA_MEDIANA_MIN) -> float | None:
    """Mediana de las lecturas alrededor de un instante.

    Mediana y no promedio: si una lectura sale disparatada -pasa con los I2C
    y los paquetes de radio a medias- el promedio se la lleva y la mediana la
    ignora.
    """
    margen = timedelta(minutes=ventana_min)
    cerca = [v for t, v in serie if abs(t - objetivo) <= margen]
    return _mediana(cerca)


def factor_a_nivel_del_mar(serie: list[tuple[datetime, float]],
                           msl_modelo: float | None) -> float | None:
    """Cuanto hay que multiplicar la presion de estacion para compararla.

    Se estima con el dato del modelo en el mismo momento en vez de con la
    formula barometrica, que exigiria conocer la altitud exacta y suponer
    que el sensor no tiene desviacion. Esto absorbe las dos cosas.
    """
    if not serie or not msl_modelo:
        return None
    ahora = valor_en(serie, datetime.now(timezone.utc), ventana_min=30)
    if not ahora or ahora <= 0:
        return None
    factor = msl_modelo / ahora
    # Cordura: a nivel del mar seria 1.0 y en el altiplano mexicano ~1.25.
    # Fuera de ese rango, algo no es lo que creemos -otra unidad, otro
    # sensor- y es preferible no usarlo que publicar un disparate.
    if not (0.95 <= factor <= 1.45):
        log.warning("factor de reduccion fuera de rango (%.3f); "
                    "se ignora el barometro local", factor)
        return None
    return factor


def resumen(serie: list[tuple[datetime, float]]) -> dict:
    """Cadencia y frescura, para saber si la fuente es de fiar."""
    if not serie:
        return {"muestras": 0, "edad_min": None, "cadencia_s": None}
    ahora = datetime.now(timezone.utc)
    edad = (ahora - serie[-1][0]).total_seconds() / 60
    huecos = [(serie[i + 1][0] - serie[i][0]).total_seconds()
              for i in range(len(serie) - 1)]
    return {
        "muestras": len(serie),
        "edad_min": round(edad, 1),
        "cadencia_s": round(_mediana(huecos) or 0),
        "horas": round((serie[-1][0] - serie[0][0]).total_seconds() / 3600, 1),
    }


def utilizable(serie: list[tuple[datetime, float]]) -> bool:
    """¿Sirve como fuente primaria ahora mismo?

    Se exige que este fresca y que haya historia suficiente para medir una
    hora de cambio. Un sensor que dejo de reportar hace media hora no puede
    decir que esta pasando.
    """
    if len(serie) < 10:
        return False
    r = resumen(serie)
    return (r["edad_min"] is not None
            and r["edad_min"] <= config.BAROMETRO_MAX_EDAD_MIN
            and r["horas"] >= 1.5)
