"""Persistencia. Todo en CSV plano para que puedas abrirlo en Excel."""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone

from . import config

PRED_FIELDS = [
    "issued_utc", "valid_utc", "lead_min",
    "p_final", "p_radar", "p_ir", "p_models",
    "w_radar", "w_ir", "w_models",
    "score_radar", "score_ir",
    "motion_speed_kmh", "motion_from", "motion_conf", "growth",
    "cell_eta_min", "cell_km", "cell_intensity",
    "cape", "radar_coverage",
]

OBS_FIELDS = ["valid_utc", "rained", "mm", "peak_score", "source"]


def _ensure(path: str, fields: list[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=fields).writeheader()


def append_predictions(rows: list[dict]) -> None:
    if not rows:
        return
    _ensure(config.PREDICTIONS_CSV, PRED_FIELDS)
    with open(config.PREDICTIONS_CSV, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=PRED_FIELDS, extrasaction="ignore")
        for row in rows:
            w.writerow(row)


def append_observations(rows: list[dict]) -> None:
    if not rows:
        return
    _ensure(config.OBSERVATIONS_CSV, OBS_FIELDS)
    existing = {r["valid_utc"] for r in read_observations()}
    fresh = [r for r in rows if r["valid_utc"] not in existing]
    if not fresh:
        return
    with open(config.OBSERVATIONS_CSV, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=OBS_FIELDS, extrasaction="ignore")
        for row in fresh:
            w.writerow(row)


def read_predictions() -> list[dict]:
    if not os.path.exists(config.PREDICTIONS_CSV):
        return []
    with open(config.PREDICTIONS_CSV, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def read_observations() -> list[dict]:
    if not os.path.exists(config.OBSERVATIONS_CSV):
        return []
    with open(config.OBSERVATIONS_CSV, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False, default=str)


def prune(max_rows: int = 60000) -> None:
    """Evita que el repo crezca sin limite. ~1 año de datos cabe de sobra."""
    rows = read_predictions()
    if len(rows) <= max_rows:
        return
    keep = rows[-max_rows:]
    with open(config.PREDICTIONS_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=PRED_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(keep)


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def round_slot(dt: datetime, minutes: int = 15) -> str:
    """Normaliza a bloques de 15 min para poder cruzar prediccion y observacion."""
    dt = dt.replace(second=0, microsecond=0)
    dt = dt.replace(minute=(dt.minute // minutes) * minutes)
    return dt.isoformat()
