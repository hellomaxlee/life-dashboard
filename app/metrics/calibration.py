"""The load bar and its calibration (notes.txt § Goal model, "Load bar calibration").

The bar starts as the placeholder `workout.load_bar`. As soon as `calibration_min_runs` runs
of at least 4 miles with HR samples exist, the bar becomes the median of their loads rounded
to the nearest 5, decided at the moment the last of them ended, and applies from the Monday
after that week. Every `recalibrate_months` after the last decision the trailing window of
such runs is checked (runs that ended after the last decision, in the `recalibrate_months`
before the check date): at least `calibration_min_runs` of them and a different median
changes the bar from the following week; otherwise it is kept and the next check is another
period on. Stored history is never rewritten: a late-arriving run cannot change a decision already
made, only the ones still to come.

Every timestamp a decision carries is derived from the data, never from the wall clock, so
a recompute from scratch writes the same history row.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from app.config import Settings
from app.metrics.calendar import add_months, local_date, local_midnight_iso, next_week_start
from app.metrics.keys import round_to_step

MILE_M = 1609.344
CALIBRATION_MILES = 4.0
ROUND_STEP = 5.0
PLACEHOLDER = "placeholder"
CALIBRATED = "calibrated"


@dataclass(frozen=True)
class BarEntry:
    effective_from_week: str
    value: float
    source_ids: tuple[str, ...]
    decided_at_utc: str

    def as_row(self) -> dict[str, object]:
        return {
            "effective_from_week": self.effective_from_week,
            "value": self.value,
            "source_ids": list(self.source_ids),
            "decided_at_utc": self.decided_at_utc,
        }


@dataclass(frozen=True)
class RunLoad:
    id: str
    end_utc: str
    load: float


def is_calibration_run(type_: str, distance_m: float | None, load: float | None) -> bool:
    return (
        type_ == "run"
        and distance_m is not None
        and distance_m >= CALIBRATION_MILES * MILE_M
        and load is not None
    )


def median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def bar_value(runs: list[RunLoad]) -> float:
    return round_to_step(median([r.load for r in runs]), ROUND_STEP)


def plan_calibration(
    stored: list[BarEntry], runs: list[RunLoad], settings: Settings, today: date
) -> list[BarEntry]:
    """Stored entries, then every new decision the runs support up to `today`."""
    tz = settings.home_tz
    min_runs = settings.workout.calibration_min_runs
    months = settings.workout.recalibrate_months
    ordered = sorted(runs, key=lambda r: (r.end_utc, r.id))
    entries = list(stored)

    if not entries:
        if len(ordered) < min_runs:
            return entries
        first = ordered[:min_runs]
        decided = first[-1].end_utc
        entries.append(
            BarEntry(
                next_week_start(local_date(decided, tz)).isoformat(),
                bar_value(first),
                tuple(r.id for r in first),
                decided,
            )
        )

    current = entries[-1].value
    decided_at = entries[-1].decided_at_utc
    check = add_months(local_date(decided_at, tz), months)
    while check <= today:
        window_start = add_months(check, -months)
        window = [
            r
            for r in ordered
            if r.end_utc > decided_at and window_start <= local_date(r.end_utc, tz) < check
        ]
        if len(window) >= min_runs:
            value = bar_value(window)
            if value != current:
                entries.append(
                    BarEntry(
                        next_week_start(check).isoformat(),
                        value,
                        tuple(r.id for r in window),
                        local_midnight_iso(check, tz),
                    )
                )
                current = value
        check = add_months(check, months)
    return entries


def bar_for_week(entries: list[BarEntry], week_start: str, placeholder: float) -> tuple[float, str]:
    """The bar in force for the week starting `week_start`, and where it came from."""
    in_force = [e for e in entries if e.effective_from_week <= week_start]
    if not in_force:
        return placeholder, PLACEHOLDER
    return in_force[-1].value, CALIBRATED


def entry_from_row(row: dict) -> BarEntry:
    return BarEntry(
        str(row["effective_from_week"]),
        float(row["value"]),
        tuple(json.loads(row["source_ids_json"])),
        str(row["decided_at_utc"]),
    )
