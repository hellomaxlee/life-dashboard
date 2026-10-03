"""The keys the engine owns in `daily_metrics` and `weekly_metrics`.

Other modules (ingest's `claude_week_*`, the summary's `summary_device_line`) share the same
JSON rows; the engine rewrites only these keys and leaves every other one as it found it.
`tools.replay` strips these keys when it compares ingested truth, and compares them only
after recomputing under the same clock.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

WELLNESS_KEYS = {
    "heart_rate_variability": "hrv_ms",
    "resting_heart_rate": "resting_hr",
    "vo2_max": "vo2_max",
    "time_in_daylight": "daylight_min",
}

DAILY_KEYS = frozenset(
    {
        "quality_workout",
        "workout_count",
        "workout_load",
        "workout_ids",
        "sleep_hours",
        "sleep_win",
        "steps",
        "books_ytd",
        "book_finished_today",
        "wins",
        "load_trimp",
        "load_acute",
        "load_chronic",
        "load_balance",
        "wellness_fact",
    }
    | set(WELLNESS_KEYS.values())
    | {f"{name}_baseline" for name in WELLNESS_KEYS.values()}
)

WEEKLY_KEYS = frozenset(
    {
        "quality_workouts",
        "week_hit",
        "weeks_hit_streak",
        "week_complete_at",
        "load_bar",
        "load_bar_source",
    }
)

STATE_TODAY = "last_today_local"
STATE_NOW = "last_now_utc"
STATE_CHECK = "last_calibration_check"


def round_half_up(value: float, places: int) -> float:
    """Round the way the hand arithmetic in the golden cases does: 3.665 -> 3.67, never 3.66."""
    quantum = Decimal(1).scaleb(-places)
    return float(Decimal(repr(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def round_to_step(value: float, step: float) -> float:
    return float(Decimal(repr(value / step)).quantize(Decimal(1), rounding=ROUND_HALF_UP)) * step
