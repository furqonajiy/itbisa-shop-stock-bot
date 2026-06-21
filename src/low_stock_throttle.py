"""
low_stock_throttle.py
---------------------
Caps the /stock_low report to at most once per LOW_STOCK_MIN_INTERVAL_HOURS.

The report is a full scan of both platform catalogs, so we don't want it to run
on every Telegram trigger. State lives in data/low_stock_throttle.json on the
bot-state branch:

  {"last_run_at": "<iso-utc>" | null}

The Telegram bot Worker is stateless, so the cap is enforced here (the workflow
still spins up on each trigger, but a throttled trigger skips the scan and just
replies that the report was already generated).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src import config

_STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "low_stock_throttle.json"


def load(path=_STATE_PATH) -> dict:
    """Loads throttle state. Returns an empty state if missing/unreadable."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return {"last_run_at": None}
    return {"last_run_at": data.get("last_run_at")}


def save(state: dict, path=_STATE_PATH) -> None:
    """Atomically writes throttle state."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"last_run_at": state.get("last_run_at")}, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def window_open(
        state: dict,
        now: datetime | None = None,
        min_interval_hours: int = config.LOW_STOCK_MIN_INTERVAL_HOURS,
) -> bool:
    """True if at least `min_interval_hours` have elapsed since `last_run_at`
    (or there has never been a run). Pure given `now`."""
    last = state.get("last_run_at")
    if not last:
        return True
    if now is None:
        now = datetime.now(timezone.utc)
    try:
        last_dt = datetime.fromisoformat(last)
    except (ValueError, TypeError):
        return True
    return (now - last_dt) >= timedelta(hours=min_interval_hours)
