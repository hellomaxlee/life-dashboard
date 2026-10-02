"""The grounded payload: every number the summary may say, read from the metrics tables.

Reads only `daily_metrics.metrics_json` (today and yesterday), `weekly_metrics.metrics_json`
(this week's Monday and last week's), the title of a book finished today from `books`, and
the day's workout minutes from `activities` when the engine did not write `workout_minutes`.
Everything else in the payload is a label derived in code (cell, lens, dot word, weekday);
labels carry no numbers. `numbers(payload)` is the set the grounding gate checks against.

Day type `travel` and `race-week` and season `peak` / `off` need a signal the data does not
carry: they are read from an optional `day_flags` list in the day's metrics row and never
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


def lens_for(day: date) -> str:
    return LENSES[day.toordinal() % len(LENSES)]


def _row_json(conn: sqlite3.Connection, table: str, key: str, value: str) -> dict[str, Any] | None:
    row = conn.execute(f'SELECT metrics_json FROM "{table}" WHERE {key} = ?', (value,)).fetchone()
    if row is None:
        return None
    try:
        parsed = json.loads(row["metrics_json"])
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _num(value: Any, digits: int | None) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    if digits is None:
        return int(round(value))
    rounded = round(float(value), digits)
    return int(rounded) if rounded == int(rounded) and digits == 0 else rounded


def _flag(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _flags(row: dict[str, Any] | None) -> set[str]:
    raw = (row or {}).get("day_flags")
    if not isinstance(raw, list):
        return set()
    return {str(item).strip().lower() for item in raw if isinstance(item, str)}


def _book_title(conn: sqlite3.Connection, day_local: str) -> str | None:
    row = conn.execute(
        "SELECT title FROM books WHERE substr(COALESCE(read_at, date_added), 1, 10) = ? "
        "ORDER BY title LIMIT 1",
        (day_local,),
    ).fetchone()
    return str(row["title"]) if row is not None else None


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
    block: dict[str, Any] = {
        "quality_workout": _flag(row.get("quality_workout")),
        "workout_count": _num(row.get("workout_count"), 0),
        "workout_load": _num(row.get("workout_load"), 0),
        "workout_minutes": _num(row.get("workout_minutes"), 0),
        "sleep_hours": _num(row.get("sleep_hours"), 1),
        "sleep_win": _flag(row.get("sleep_win")),
        "steps": _num(row.get("steps"), 0),
        "book_finished_today": _flag(row.get("book_finished_today")),
        "book_title": book_title,
        "wins": [str(w) for w in wins if isinstance(w, str)] if isinstance(wins, list) else [],
    }
    return block


def _week_block(this_week: dict[str, Any] | None, last_week: dict[str, Any] | None, target: int):
    this_week = this_week or {}
    last_week = last_week or {}
    dots = _num(this_week.get("quality_workouts"), 0)
    return {
        "quality_workouts": dots,
        "target": target,
        "dots_word": _ordinal(dots),
        "week_hit": _flag(this_week.get("week_hit")),
        "weeks_hit_streak": _num(this_week.get("weeks_hit_streak"), 0),
        "last_week_quality_workouts": _num(last_week.get("quality_workouts"), 0),
        "last_week_hit": _flag(last_week.get("week_hit")),
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
    value, baseline = _num(fact.get("value"), digits), _num(fact.get("baseline"), digits)
    if value is None or baseline is None:
        return None
    return {
        "metric": fact["metric"],
        "value": value,
        "baseline": baseline,
        "direction": str(fact.get("direction", "")),
    }


def classify(
    today: dict[str, Any] | None,
    week: dict[str, Any],
    streak_ever_hit: bool,
) -> Cell:
    """The fixture-matrix cell for a day, from its metrics and the week's state."""
    inferred: list[str] = []
    flags = _flags(today)
    workouts = today.get("workout_count") if today else None
    worked = bool(today and (today.get("quality_workout") or (workouts or 0) > 0))
    if "travel" in flags:
        day_type = "travel"
    elif "race-week" in flags or "race_week" in flags:
        day_type = "race-week"
    else:
        day_type = "train" if worked else "rest"
        inferred.append("day_type")

    has_health = today is not None and any(
        today.get(k) is not None for k in ("sleep_hours", "steps", "workout_count")
    )
    if not has_health:
        completeness = "health-delayed"
    elif worked and not today.get("quality_workout") and today.get("workout_load") is None:
        completeness = "workout-without-hr"
    elif today.get("sleep_hours") is None:
        completeness = "sleep-missing"
    else:
        completeness = "all-sources"

    streak = week.get("weeks_hit_streak") or 0
    if streak > 0:
        streak_state = "alive"
    elif streak_ever_hit:
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


def _ever_hit(conn: sqlite3.Connection, before_monday: str) -> bool:
    row = conn.execute(
        "SELECT metrics_json FROM weekly_metrics WHERE week_start_local < ?", (before_monday,)
    ).fetchall()
    for r in row:
        try:
            if json.loads(r["metrics_json"]).get("week_hit") is True:
                return True
        except (TypeError, ValueError, AttributeError):
            continue
    return False


def build_payload(
    conn: sqlite3.Connection,
    settings: Settings,
    day_local: str,
    now_local_hour: int | None = None,
) -> Payload:
    day = date.fromisoformat(day_local)
    monday = day - timedelta(days=day.weekday())
    last_monday = monday - timedelta(days=7)
    yesterday = day - timedelta(days=1)

    today_row = _row_json(conn, "daily_metrics", "day_local", day_local)
    yesterday_row = _row_json(conn, "daily_metrics", "day_local", yesterday.isoformat())
    week_row = _row_json(conn, "weekly_metrics", "week_start_local", monday.isoformat())
    last_week_row = _row_json(conn, "weekly_metrics", "week_start_local", last_monday.isoformat())

    title = _book_title(conn, day_local) if (today_row or {}).get("book_finished_today") else None
    today = _day_block(today_row, title)
    if today["workout_minutes"] is None and (today["workout_count"] or 0) > 0:
        today["workout_minutes"] = _workout_minutes(conn, day_local, settings.home_tz)
    week = _week_block(week_row, last_week_row, settings.week_target)
    cell = classify(today_row, week, _ever_hit(conn, monday.isoformat()))

    data: dict[str, Any] = {
        "day": day_local,
        "weekday": day.strftime("%A"),
        "time_of_day": _time_of_day(now_local_hour),
        "cell": {
            "day_type": cell.day_type,
            "completeness": cell.completeness,
            "streak_state": cell.streak_state,
            "season": cell.season,
            "inferred": list(cell.inferred),
        },
        "lens": lens_for(day),
        "today": today,
        "yesterday": {
            "quality_workout": _flag((yesterday_row or {}).get("quality_workout")),
            "workout_load": _num((yesterday_row or {}).get("workout_load"), 0),
        },
        "week": week,
        "load": _load_block(today_row),
        "books": {
            "ytd": _num((today_row or {}).get("books_ytd"), 0),
            "target": settings.books.target_per_year,
        },
        "targets": {"sleep_hours": round(settings.sleep_target_hours, 1)},
    }
    fact = _wellness_fact(today_row)
    if fact is not None:
        data["wellness_fact"] = fact
    return Payload(day_local, lens_for(day), cell, data)


def _time_of_day(hour: int | None) -> str:
    if hour is None:
        return "morning"
    return "morning" if hour < 12 else "evening"


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
