"""Cliente HTTP con reintentos. Todas las fuentes son públicas y sin API key."""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

from . import config

log = logging.getLogger(__name__)

_session = requests.Session()
_session.headers.update({"User-Agent": config.USER_AGENT})


def get_bytes(url: str, *, timeout: int | None = None) -> bytes | None:
    """Descarga binaria (tiles PNG). Devuelve None si falla tras los reintentos."""
    timeout = timeout or config.HTTP_TIMEOUT
    for attempt in range(config.HTTP_RETRIES):
        try:
            r = _session.get(url, timeout=timeout)
            if r.status_code == 200 and r.content:
                return r.content
            # 404 en un frame concreto es normal (aún no publicado): no reintentar
            if r.status_code == 404:
                log.debug("404 %s", url)
                return None
            log.warning("HTTP %s en %s", r.status_code, url)
        except requests.RequestException as exc:
            log.warning("fallo de red (%s/%s) %s: %s",
                        attempt + 1, config.HTTP_RETRIES, url, exc)
        time.sleep(1.5 * (attempt + 1))
    return None


def get_json(url: str, *, timeout: int | None = None) -> Any | None:
    timeout = timeout or config.HTTP_TIMEOUT
    for attempt in range(config.HTTP_RETRIES):
        try:
            r = _session.get(url, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            log.warning("HTTP %s en %s", r.status_code, url)
        except (requests.RequestException, ValueError) as exc:
            log.warning("fallo de red (%s/%s) %s: %s",
                        attempt + 1, config.HTTP_RETRIES, url, exc)
        time.sleep(1.5 * (attempt + 1))
    return None


def post(url: str, data: bytes, headers: dict[str, str]) -> bool:
    try:
        r = _session.post(url, data=data, headers=headers,
                          timeout=config.HTTP_TIMEOUT)
        return r.status_code < 300
    except requests.RequestException as exc:
        log.error("POST falló %s: %s", url, exc)
        return False
