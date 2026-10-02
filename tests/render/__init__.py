from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.config import Settings
from app.render.view import DayView, load_fixture

DAYS = Path(__file__).resolve().parent.parent.parent / "fixtures" / "days"
SNAPSHOTS = Path(__file__).resolve().parent / "snapshots"

WEEK_41 = "train__all-sources__alive__base"
NO_READING = "rest__sleep-missing__broken-last-week__base"
STALE = "travel__health-delayed__alive__off"
RED_91 = "race-week__workout-without-hr__never-started__peak"
WEEK_COMPLETE = "train__all-sources__alive__peak"
COMBOS = (WEEK_41, NO_READING, STALE, RED_91, WEEK_COMPLETE)


def load(combo: str, settings: Settings) -> tuple[DayView, datetime]:
    return load_fixture(DAYS / f"{combo}.json", settings)
