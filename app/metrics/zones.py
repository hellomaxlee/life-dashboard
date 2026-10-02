"""HR zones and the Edwards effort load (notes.txt § Goal model, "Quality workout", "HR zones").

Zone lower bounds are hr_max x pct rounded half up to a whole bpm, which is the table in the
goal model (189: Z1 95, Z2 113, Z3 132, Z4 151, Z5 170). Below Z1 scores 0.

Time per zone comes from sample spacing: a sample stands for the time from its timestamp to
the next sample's, and the last sample runs to the workout's end. Any one interval is capped
at `max_gap_s`; the rest of a longer gap is unknown and scores nothing. Time before the
first sample is likewise unscored. A workout with no samples has no load (None), not zero.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.metrics.keys import round_half_up
from app.timeutil import from_utc_iso

ZONE_WEIGHTS = (1, 2, 3, 4, 5)


@dataclass(frozen=True)
class Sample:
    ts_utc: str
    bpm: float | None


def zone_floors(hr_max: int, zones_pct: tuple[float, ...]) -> tuple[int, ...]:
    return tuple(int(hr_max * pct + 0.5) for pct in zones_pct)


def zone_of(bpm: float, floors: tuple[int, ...]) -> int:
    """0 below Z1, else 1..5."""
    zone = 0
    for floor in floors:
        if bpm >= floor:
            zone += 1
    return zone


def zone_seconds(
    samples: list[Sample], end_utc: str, floors: tuple[int, ...], max_gap_s: int
) -> list[float]:
    """Seconds spent in each of Z1..Z5 (index 0 = Z1)."""
    ordered = sorted(samples, key=lambda s: s.ts_utc)
    end = from_utc_iso(end_utc)
    seconds = [0.0] * len(floors)
    for i, sample in enumerate(ordered):
        start = from_utc_iso(sample.ts_utc)
        stop = from_utc_iso(ordered[i + 1].ts_utc) if i + 1 < len(ordered) else end
        span = min((stop - start).total_seconds(), float(max_gap_s))
        if span <= 0 or sample.bpm is None:
            continue
        zone = zone_of(sample.bpm, floors)
        if zone:
            seconds[zone - 1] += span
    return seconds


def edwards_load(
    samples: list[Sample], end_utc: str, floors: tuple[int, ...], max_gap_s: int
) -> float | None:
    """Minutes in Z1..Z5 weighted 1..5, to one decimal. None when there are no samples."""
    if not samples:
        return None
    seconds = zone_seconds(samples, end_utc, floors, max_gap_s)
    total = sum(weight * s / 60.0 for weight, s in zip(ZONE_WEIGHTS, seconds, strict=True))
    return round_half_up(total, 1)
