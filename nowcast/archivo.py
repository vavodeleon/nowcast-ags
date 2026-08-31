"""Historial de cuadros de satelite y rayos, para animar y revisar tormentas.

## Por que esta organizado asi

La pagina se sirve desde GitHub Pages, que no permite listar un directorio:
hay que publicar un indice. Y el indice es lo delicado, porque cualquier
archivo que se reescriba en cada corrida entra 96 veces al dia al historial
de git.

De ahi la estructura:

    docs/hist/dias.json          los dias disponibles (cambia una vez al dia)
    docs/hist/2026-08-26.json    indice del dia (crece; se reescribe 96 veces)
    docs/hist/2026-08-26/1245.png    el cuadro (se escribe una vez)
    docs/hist/2026-08-26/1245.r.json rayos de ese instante (una vez)

El indice diario ronda los 3 KB llenos, asi que sus 96 revisiones diarias
pesan ~290 KB al dia. Un indice global unico habria pesado 9 KB y, al
reescribirse igual de seguido, habria metido ~300 MB al año en el
repositorio. Partirlo por dia es lo que hace esto viable.

## Lo que este historial NO cambia

Los cuadros no aumentan el ritmo de crecimiento del repositorio. Hoy
`satelite.png` ya entra a git 96 veces al dia como blobs distintos; guardarlos
con nombre propio en vez de sobrescribir el mismo archivo cuesta lo mismo. La
poda a 7 dias saca los viejos del sitio publicado -que es lo que se descarga
al abrir la pagina- pero no del historial de git, porque git no olvida.

El crecimiento de ~3 GB al año sigue siendo un problema pendiente y aparte.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timedelta, timezone

from . import config, store

log = logging.getLogger(__name__)

RAIZ = os.path.join(os.path.dirname(config.LATEST_JSON), "hist")
DIAS_JSON = os.path.join(RAIZ, "dias.json")


def _dia_hora(t: datetime) -> tuple[str, str]:
    """('2026-08-26', '1245') en hora local, que es como se piensa un dia."""
    local = t.astimezone(config.TZ)
    return local.strftime("%Y-%m-%d"), local.strftime("%H%M")


def _indice_dia(dia: str) -> str:
    return os.path.join(RAIZ, f"{dia}.json")


def guardar(t: datetime, rgba_png: str | None, bounds: list | None,
            puntos_rayos: list | None) -> None:
    """Archiva el cuadro de este instante junto con sus rayos.

    'rgba_png' es la ruta del PNG que ya se genero para la pagina principal;
    se copia, no se vuelve a renderizar. Reproyectar cuesta varios segundos en
    un Raspberry Pi 3 y el resultado seria identico.
    """
    if not bounds:
        return
    dia, hora = _dia_hora(t)
    carpeta = os.path.join(RAIZ, dia)
    os.makedirs(carpeta, exist_ok=True)

    entrada = {"t": hora}

    if rgba_png and os.path.exists(rgba_png):
        destino = os.path.join(carpeta, f"{hora}.png")
        try:
            shutil.copyfile(rgba_png, destino)
        except OSError as exc:
            log.warning("no se pudo archivar el cuadro: %s", exc)
            return
    else:
        return

    if puntos_rayos:
        try:
            with open(os.path.join(carpeta, f"{hora}.r.json"), "w") as fh:
                json.dump(puntos_rayos, fh, separators=(",", ":"))
            entrada["r"] = sum(int(p[2]) if len(p) > 2 else 1
                               for p in puntos_rayos)
        except OSError as exc:
            log.warning("no se pudieron archivar los rayos: %s", exc)

    _añadir_al_indice(dia, entrada, bounds)


def _añadir_al_indice(dia: str, entrada: dict, bounds: list) -> None:
    indice = store.load_json(_indice_dia(dia), None) or {"cuadros": []}
    indice["bounds"] = bounds          # el ultimo manda: el encuadre no cambia
    cuadros = [c for c in indice.get("cuadros", []) if c.get("t") != entrada["t"]]
    cuadros.append(entrada)
    cuadros.sort(key=lambda c: c["t"])
    indice["cuadros"] = cuadros
    store.save_json(_indice_dia(dia), indice)

    dias = store.load_json(DIAS_JSON, None) or []
    if dia not in dias:
        dias.append(dia)
        dias.sort()
        store.save_json(DIAS_JSON, dias)


def podar(dias_a_conservar: int | None = None) -> int:
    """Borra los dias que ya pasaron del limite. Devuelve cuantos borro."""
    limite = dias_a_conservar or config.HIST_DIAS
    corte = (datetime.now(timezone.utc).astimezone(config.TZ)
             - timedelta(days=limite)).strftime("%Y-%m-%d")

    dias = store.load_json(DIAS_JSON, None) or []
    vivos, muertos = [], []
    for d in dias:
        (muertos if d < corte else vivos).append(d)

    for d in muertos:
        shutil.rmtree(os.path.join(RAIZ, d), ignore_errors=True)
        try:
            os.remove(_indice_dia(d))
        except OSError:
            pass

    # Puede haber carpetas de dias que no esten en el indice, por ejemplo si
    # una corrida murio a medias. Se limpian igual: si no, crecen para siempre
    # sin que nada las mencione.
    if os.path.isdir(RAIZ):
        for nombre in os.listdir(RAIZ):
            ruta = os.path.join(RAIZ, nombre)
            if os.path.isdir(ruta) and nombre < corte:
                shutil.rmtree(ruta, ignore_errors=True)
                if nombre not in muertos:
                    muertos.append(nombre)

    if muertos:
        store.save_json(DIAS_JSON, vivos)
        log.info("historial: %s dia(s) podado(s), quedan %s",
                 len(muertos), len(vivos))
    return len(muertos)
