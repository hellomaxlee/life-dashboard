"""Wellness signals, baselines and the one daily fact (notes.txt § Goal model, "Wellness
signals").

A baseline is the mean of the metric's values on the `baseline_days` days before the day
(the day itself excluded), null with fewer than `MIN_BASELINE_VALUES` values. A fact is
stated only on a band crossing, and only one per day, chosen by the configured priority:
HRV at or below baseline x (1 + hrv_pct/100); resting HR at or above baseline + rhr_bpm;
daylight below daylight_min minutes. On an ordinary day the fact is null.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.config import Settings
from app.metrics.keys import WELLNESS_KEYS, round_half_up

MIN_BASELINE_VALUES = 7
BELOW = "below"
ABOVE = "above"

Series = dict[str, dict[date, float]]


def baseline(values: dict[date, float], day: date, window_days: int) -> float | None:
    prior = [v for d, v in values.items() if day - timedelta(days=window_days) <= d < day]
    if len(prior) < MIN_BASELINE_VALUES:
        return None
    return round_half_up(sum(prior) / len(prior), 1)


def crossing(
    name: str, value: float, base: float | None, settings: Settings
) -> dict[str, object] | None:
    bands = settings.wellness.bands
    if name == "hrv_ms":
        if base is not None and value <= base * (1 + bands.hrv_pct / 100):
            return _fact(name, value, base, BELOW)
    elif name == "resting_hr":
        if base is not None and value >= base + bands.rhr_bpm:
            return _fact(name, value, base, ABOVE)
    elif name == "daylight_min" and value < bands.daylight_min:
        return _fact(name, value, base, BELOW)
    return None


def _fact(name: str, value: float, base: float | None, direction: str) -> dict[str, object]:
    return {"metric": name, "value": value, "baseline": base, "direction": direction}


def wellness_json(series: Series, day: date, settings: Settings) -> dict[str, object]:
    """The day's values, baselines and fact, keyed as the daily row stores them."""
    out: dict[str, object] = {}
    window = settings.wellness.baseline_days
    facts: dict[str, dict[str, object]] = {}
    for name in WELLNESS_KEYS.values():
        values = series.get(name, {})
        value = values.get(day)
        base = baseline(values, day, window)
        out[name] = value
        out[f"{name}_baseline"] = base
        if value is not None:
            fact = crossing(name, value, base, settings)
            if fact is not None:
                facts[name] = fact
    chosen = next((facts[n] for n in settings.metrics.wellness_priority if n in facts), None)
    out["wellness_fact"] = chosen
    return out
