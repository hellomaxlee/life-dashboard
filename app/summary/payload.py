"""The grounded payload: every number the summary may say, read from the metrics tables.

The line shown on day D describes D - 1, the latest complete day (health pushes cover whole
days ending yesterday; ruling 2026-10-02). `build_payload(conn, settings, shown_day)` reads
only: the described day's `daily_metrics` row, the `weekly_metrics` rows for the described
day's week and the two weeks before it, the title of a book whose effective date
(`app.metrics.books.effective_date`, home timezone) is the described day, and the described
day's workout minutes from `activities` when the engine did not write `workout_minutes`.
`targets.load_bar` is the described week's `load_bar` as the engine stored it.
Everything else is a label derived in code (cell, lens, dot word, weekday, week relation);
labels carry no numbers. `numbers()` is the set the grounding gate checks against.

Values are shaped to what the device shows: sleep truncated to one decimal like the
renderer (6.97 is 6.9, never 7.0), loads whole, balance to one decimal.

Day type `travel` and `race-week` and season `peak` / `off` need a signal the data does not
carry: they are read from an optional `day_flags` list in the described day's row and never
inferred. `cell.inferred` names every label that was defaulted.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.config import Settings
from app.metrics.books import effective_date
from app.timeutil import from_utc_iso, local_day, to_utc_iso

LENSES = (
    "control",
    "habit",
    "non-attachment",
    "self-awareness",
    "resilience",
    "rest-as-work",
    "plain gratitude",
)
DAY_TYPES = ("train", "rest", "race-week", "travel")
COMPLETENESS = ("all-sources", "workout-without-hr", "sleep-missing", "health-delayed")
STREAK_STATES = ("alive", "broken-last-week", "never-started")
SEASONS = ("base", "peak", "off")
ORDINALS = ("no", "first", "second", "third", "fourth", "fifth", "sixth", "seventh")
WELLNESS_METRICS = ("hrv_ms", "resting_hr", "vo2_max", "daylight_min")


@dataclass(frozen=True)
class Cell:
    day_type: str
    completeness: str
    streak_state: str
    season: str
    inferred: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return f"{self.day_type} / {self.completeness} / {self.streak_state} / {self.season}"


@dataclass(frozen=True)
class Payload:
    day_local: str
    lens: str
    cell: Cell
    data: dict[str, Any] = field(default_factory=dict)

    def numbers(self) -> frozenset[float]:
        return frozenset(_walk_numbers(self.data))

    def text(self) -> str:
        return json.dumps(self.data, sort_keys=True, indent=1)

    @property
    def book_title(self) -> str | None:
        return self.data.get("day", {}).get("book_title")


def lens_for(day: date) -> str:
    return LENSES[day.toordinal() % len(LENSES)]


def described_day(shown_day: str) -> str:
    return (date.fromisoformat(shown_day) - timedelta(days=1)).isoformat()


def _row_json(conn: sqlite3.Connection, table: str, key: str, value: str) -> dict[str, Any] | None:
    row = conn.execute(f'SELECT metrics_json FROM "{table}" WHERE {key} = ?', (value,)).fetchone()
    if row is None:
        return None
    try:
        parsed = json.loads(row["metrics_json"])
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return float(value)


def _num(value: Any, digits: int) -> float | int | None:
    number = _finite(value)
    if number is None:
        return None
    if digits == 0:
        return int(round(number))
    return round(number, digits)


def _truncated(value: Any) -> float | None:
    number = _finite(value)
    if number is None or number < 0:
        return None
    return int(number * 10 + 1e-6) / 10


def _flag(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _flags(row: dict[str, Any] | None) -> set[str]:
    raw = (row or {}).get("day_flags")
    if not isinstance(raw, list):
        return set()
    return {str(item).strip().lower() for item in raw if isinstance(item, str)}


def _book_title(conn: sqlite3.Connection, settings: Settings, day_local: str) -> str | None:
    target = date.fromisoformat(day_local)
    rows = conn.execute("SELECT title, read_at, date_added FROM books ORDER BY title").fetchall()
    for row in rows:
        if effective_date(row["read_at"], row["date_added"], settings) == target:
            return str(row["title"])
    return None


def _workout_minutes(conn: sqlite3.Connection, day_local: str, tz: str) -> int | None:
    zone = ZoneInfo(tz)
    start = datetime.combine(date.fromisoformat(day_local), datetime.min.time(), zone)
    rows = conn.execute(
        "SELECT start_utc, duration_s FROM activities WHERE start_utc >= ? AND start_utc < ?",
        (to_utc_iso(start - timedelta(days=1)), to_utc_iso(start + timedelta(days=2))),
    ).fetchall()
    total = sum(
        int(r["duration_s"])
        for r in rows
        if local_day(from_utc_iso(r["start_utc"]), tz) == day_local
    )
    return int(round(total / 60)) if total else None


def _day_block(row: dict[str, Any] | None, book_title: str | None) -> dict[str, Any]:
    row = row or {}
    wins = row.get("wins")
    return {
        "quality_workout": _flag(row.get("quality_workout")),
        "workout_count": _num(row.get("workout_count"), 0),
        "workout_load": _num(row.get("workout_load"), 0),
        "workout_minutes": _num(row.get("workout_minutes"), 0),
        "sleep_hours": _truncated(row.get("sleep_hours")),
        "sleep_win": _flag(row.get("sleep_win")),
        "steps": _num(row.get("steps"), 0),
        "book_finished": _flag(row.get("book_finished_today")),
        "book_title": book_title,
        "wins": [str(w) for w in wins if isinstance(w, str)] if isinstance(wins, list) else [],
    }


def _week_block(
    this_week: dict[str, Any] | None,
    last_week: dict[str, Any] | None,
    target: int,
    relation: str,
) -> dict[str, Any]:
    this_week = this_week or {}
    last_week = last_week or {}
    dots = _num(this_week.get("quality_workouts"), 0)
    return {
        "relation": relation,
        "quality_workouts": dots,
        "target": target,
        "dots_word": _ordinal(dots),
        "week_hit": _flag(this_week.get("week_hit")),
        "weeks_hit_streak": _num(this_week.get("weeks_hit_streak"), 0),
        "previous_week_quality_workouts": _num(last_week.get("quality_workouts"), 0),
        "previous_week_hit": _flag(last_week.get("week_hit")),
    }


def _ordinal(dots: int | float | None) -> str | None:
    if isinstance(dots, int) and 0 <= dots < len(ORDINALS):
        return ORDINALS[dots]
    return None


def _load_block(row: dict[str, Any] | None) -> dict[str, Any]:
    row = row or {}
    return {
        "trimp": _num(row.get("load_trimp"), 0),
        "acute": _num(row.get("load_acute"), 0),
        "chronic": _num(row.get("load_chronic"), 0),
        "balance": _num(row.get("load_balance"), 1),
    }


def _wellness_fact(row: dict[str, Any] | None) -> dict[str, Any] | None:
    fact = (row or {}).get("wellness_fact")
    if not isinstance(fact, dict) or fact.get("metric") not in WELLNESS_METRICS:
        return None
    digits = 1 if fact["metric"] == "vo2_max" else 0
    value = _num(fact.get("value"), digits)
    if value is None:
        return None
    return {
        "metric": fact["metric"],
        "value": value,
        "baseline": _num(fact.get("baseline"), digits),
        "direction": str(fact.get("direction", "")),
    }


def has_health_data(row: dict[str, Any] | None) -> bool:
    """Evidence the day's health push arrived. `workout_count: 0` is not evidence: the
    engine writes it on the rollover row before any push."""
    if not row:
        return False
    if _finite(row.get("sleep_hours")) is not None or _finite(row.get("steps")) is not None:
        return True
    return (_finite(row.get("workout_count")) or 0) > 0 or row.get("quality_workout") is True


def classify(
    day_row: dict[str, Any] | None,
    week: dict[str, Any] | None,
    previous_week: dict[str, Any] | None,
    week_before: dict[str, Any] | None,
) -> Cell:
    """The fixture-matrix cell for the described day.

    Streak (Ingrid, 2026-10-02): alive when the described week's streak is above zero;
    broken-last-week only when the previous week closed with `week_hit` false and the week
    before it carried a streak above zero; otherwise never-started (neutral, no break wording).
    """
    inferred: list[str] = []
    flags = _flags(day_row)
    row = day_row or {}
    worked = row.get("quality_workout") is True or (_finite(row.get("workout_count")) or 0) > 0
    if "travel" in flags:
        day_type = "travel"
    elif "race-week" in flags or "race_week" in flags:
        day_type = "race-week"
    else:
        day_type = "train" if worked else "rest"
        inferred.append("day_type")

    if not has_health_data(day_row):
        completeness = "health-delayed"
    elif worked and row.get("quality_workout") is not True and row.get("workout_load") is None:
        completeness = "workout-without-hr"
    elif _finite(row.get("sleep_hours")) is None:
        completeness = "sleep-missing"
    else:
        completeness = "all-sources"

    streak = _finite((week or {}).get("weeks_hit_streak")) or 0
    before = _finite((week_before or {}).get("weeks_hit_streak")) or 0
    if streak > 0:
        streak_state = "alive"
    elif (previous_week or {}).get("week_hit") is False and before > 0:
        streak_state = "broken-last-week"
    else:
        streak_state = "never-started"

    if "peak" in flags:
        season = "peak"
    elif "off" in flags or "off-season" in flags:
        season = "off"
    else:
        season = "base"
        inferred.append("season")
    return Cell(day_type, completeness, streak_state, season, tuple(inferred))


def build_payload(conn: sqlite3.Connection, settings: Settings, shown_day: str) -> Payload:
    shown = date.fromisoformat(shown_day)
    day = shown - timedelta(days=1)
    monday = day - timedelta(days=day.weekday())
    shown_monday = shown - timedelta(days=shown.weekday())
    relation = "this week" if monday == shown_monday else "last week"

    day_row = _row_json(conn, "daily_metrics", "day_local", day.isoformat())
    week_row = _row_json(conn, "weekly_metrics", "week_start_local", monday.isoformat())
    previous = _row_json(
        conn, "weekly_metrics", "week_start_local", (monday - timedelta(days=7)).isoformat()
    )
    before = _row_json(
        conn, "weekly_metrics", "week_start_local", (monday - timedelta(days=14)).isoformat()
    )

    finished = (day_row or {}).get("book_finished_today") is True
    title = _book_title(conn, settings, day.isoformat()) if finished else None
    block = _day_block(day_row, title)
    if block["workout_minutes"] is None and (block["workout_count"] or 0) > 0:
        block["workout_minutes"] = _workout_minutes(conn, day.isoformat(), settings.home_tz)
    week = _week_block(week_row, previous, settings.week_target, relation)
    cell = classify(day_row, week_row, previous, before)

    data: dict[str, Any] = {
        "shown_on": shown_day,
        "describes": day.isoformat(),
        "describes_relation": "yesterday",
        "weekday": day.strftime("%A"),
        "cell": {
            "day_type": cell.day_type,
            "completeness": cell.completeness,
            "streak_state": cell.streak_state,
            "season": cell.season,
            "inferred": list(cell.inferred),
        },
        "lens": lens_for(shown),
        "day": block,
        "week": week,
        "load": _load_block(day_row),
        "books": {
            "ytd": _num((day_row or {}).get("books_ytd"), 0),
            "target": settings.books.target_per_year,
        },
        "targets": {
            "sleep_hours": _truncated(settings.sleep_target_hours),
            "load_bar": _num((week_row or {}).get("load_bar"), 0),
        },
    }
    fact = _wellness_fact(day_row)
    if fact is not None:
        data["wellness_fact"] = fact
    return Payload(shown_day, lens_for(shown), cell, data)


def _walk_numbers(value: Any) -> set[float]:
    found: set[float] = set()
    if isinstance(value, bool):
        return found
    if isinstance(value, int | float):
        found.add(float(value))
    elif isinstance(value, dict):
        for item in value.values():
            found |= _walk_numbers(item)
    elif isinstance(value, list | tuple):
        for item in value:
            found |= _walk_numbers(item)
    return found
