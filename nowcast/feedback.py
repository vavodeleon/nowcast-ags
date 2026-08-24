"""Tus correcciones desde la pagina, convertidas en verdad de referencia.

Los botones "Si llueve" / "No llueve" del tablero abren un issue de GitHub
ya rellenado. Este modulo los lee, los convierte en observaciones y los
cierra.

Por que issues y no algo mas directo: la pagina es estatica, no tiene
servidor. Mandar el dato a un canal abierto significaria poner una clave de
escritura en un repo publico, y cualquiera podria inyectar observaciones
falsas y envenenar la calibracion. Un issue exige que la persona este
identificada en GitHub, queda registrado y es reversible.

Una observacion tuya vale mas que cualquier sensor: es alguien mirando por
la ventana. Por eso se marca con source="manual" y verify.py tiene
prohibido sobrescribirla.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone

from . import config, http, store

log = logging.getLogger(__name__)

API = "https://api.github.com"
ETIQUETA = "observacion"

# "lluvia: si @ 2026-08-18T14:30:00Z"
_CUERPO_RE = re.compile(
    r"lluvia:\s*(si|s\u00ed|no)\b.*?@\s*([0-9T:\-]+Z?)", re.IGNORECASE | re.DOTALL)


def _repo() -> str | None:
    return os.environ.get("GITHUB_REPOSITORY")


def _cabeceras() -> dict:
    h = {"Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _leer_issues() -> list[dict]:
    repo = _repo()
    if not repo:
        return []
    url = f"{API}/repos/{repo}/issues?state=open&labels={ETIQUETA}&per_page=50"
    try:
        import requests
        r = requests.get(url, headers=_cabeceras(), timeout=config.HTTP_TIMEOUT)
        if r.status_code != 200:
            log.warning("no se pudieron leer issues: HTTP %s", r.status_code)
            return []
        return r.json()
    except Exception as exc:
        log.warning("fallo leyendo issues: %s", exc)
        return []


def _cerrar(numero: int, comentario: str) -> None:
    repo, tok = _repo(), os.environ.get("GITHUB_TOKEN")
    if not repo or not tok:
        return
    try:
        import requests
        requests.post(f"{API}/repos/{repo}/issues/{numero}/comments",
                      headers=_cabeceras(), json={"body": comentario},
                      timeout=config.HTTP_TIMEOUT)
        requests.patch(f"{API}/repos/{repo}/issues/{numero}",
                       headers=_cabeceras(),
                       json={"state": "closed", "state_reason": "completed"},
                       timeout=config.HTTP_TIMEOUT)
        log.info("issue #%s procesado y cerrado", numero)
    except Exception as exc:
        log.warning("no se pudo cerrar el issue #%s: %s", numero, exc)


def procesar() -> int:
    """Convierte los issues abiertos en observaciones. Devuelve cuantos."""
    issues = _leer_issues()
    if not issues:
        return 0

    filas: list[dict] = []
    procesados: list[tuple[int, str]] = []

    for issue in issues:
        if issue.get("pull_request"):
            continue
        texto = f"{issue.get('title','')}\n{issue.get('body','') or ''}"
        m = _CUERPO_RE.search(texto)
        if not m:
            log.info("issue #%s no tiene el formato esperado", issue.get("number"))
            continue

        llovio = 0 if m.group(1).lower() == "no" else 1
        crudo = m.group(2).strip()
        try:
            t = datetime.fromisoformat(crudo.replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
        except ValueError:
            t = datetime.now(timezone.utc)

        slot = store.round_slot(t)
        filas.append({"valid_utc": slot, "rained": llovio, "mm": "",
                      "peak_score": "", "source": "manual"})
        procesados.append((issue["number"],
                           f"Registrado: {'llovió' if llovio else 'no llovió'} "
                           f"a las {slot}. Gracias — esto entra directo en la "
                           f"calibración y pesa más que cualquier sensor."))

    if filas:
        _guardar(filas)
    for numero, comentario in procesados:
        _cerrar(numero, comentario)

    log.info("feedback: %s observaciones manuales incorporadas", len(filas))
    return len(filas)


def _guardar(filas: list[dict]) -> None:
    """Escribe las observaciones manuales, pisando lo que hubiera automatico."""
    import csv

    existentes = store.read_observations()
    por_slot = {r["valid_utc"]: r for r in existentes}
    for f in filas:
        por_slot[f["valid_utc"]] = f      # lo manual manda

    ordenadas = sorted(por_slot.values(), key=lambda r: r["valid_utc"])
    with open(config.OBSERVATIONS_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=store.OBS_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(ordenadas)
