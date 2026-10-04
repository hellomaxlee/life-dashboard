"""DayView: everything the three screens show, as plain typed values.

The renderer never queries or computes a metric. It is handed a DayView, and every field
that is None is drawn as a stated fallback ("NO DATA"), never as a blank and never as a
guess. Two builders make one: `load_fixture` here (a hand-written file under fixtures/days/)
and `view_from_db` in view_db.py (what is stored for a date). Both go through
`view_from_metrics`, so a fixture's `daily_metrics` / `weekly_metrics` objects have the same
shape as the table rows. This module imports no database or ingest code.

Keys read. Phase 2 (metrics) and Phase 3 (summary) are expected to write the ones marked
with their phase; until they do, the screens show the fallback.

daily_metrics.metrics_json, row keyed by the home-timezone day:
  quality_workout          bool   a quality workout landed that day (Phase 2)
  sleep_hours              float  hours asleep in the session that woke that day (Phase 2).
                                  When present and valid it takes precedence; only when it is
                                  absent does view_db fall back, as an interim, to the longest
                                  `sleep_sessions.asleep_s` for the wake day
  steps                    int    (Phase 2, optional); absent: `steps_daily.steps`
  books_ytd                int    books read in the row's calendar year, as of that day (Phase 2)
  summary_device_line      str    the one-sentence device summary, at most 110 chars (Phase 3)
  claude_week_used_pct     float  seven-day window, 0 to 100 (written today by ingest)
  claude_week_resets_at    str    UTC ISO or null (written today by ingest)
  claude_week_captured_at  str    UTC ISO (written today by ingest)

weekly_metrics.metrics_json, row keyed by the Monday that starts the week:
  quality_workouts         int    quality workouts so far that week (Phase 2)
  weeks_hit_streak         int    consecutive completed weeks at target, as of that week (Phase 2)

Ranges. A value of the wrong type or outside its range is a missing value, drawn as NO DATA,
never clamped into a plausible-looking number:
  claude_week_used_pct   0 to 100 inclusive, no tolerance: the feed documents 0 to 100, so
                         100.5 is a broken reading, not a full one
  sleep_hours            0 to 24 inclusive
  counts (steps, books_ytd, quality_workouts, weeks_hit_streak)   whole numbers, 0 or more
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import Settings
from app.timeutil import from_utc_iso, to_utc_iso

QUALITY_WORKOUT = "quality_workout"
BOOK_FINISHED = "book_finished_today"
WORKOUT_COUNT = "workout_count"
WORKOUT_LOAD = "workout_load"
WEEK_LOAD_BAR = "load_bar"
SLEEP_HOURS = "sleep_hours"
STEPS = "steps"
BOOKS_YTD = "books_ytd"
SUMMARY_LINE = "summary_device_line"
CLAUDE_USED_PCT = "claude_week_used_pct"
CLAUDE_RESETS_AT = "claude_week_resets_at"
CLAUDE_CAPTURED_AT = "claude_week_captured_at"
WEEK_QUALITY_WORKOUTS = "quality_workouts"
WEEKS_HIT_STREAK = "weeks_hit_streak"

MAX_SLEEP_HOURS = 24.0


@dataclass(frozen=True)
class ClaudeUsage:
    used_pct: float | None = None
    resets_at_utc: str | None = None
    captured_at_utc: str | None = None


@dataclass(frozen=True)
class DayView:
    day_local: str
    home_tz: str = "America/New_York"
    week_target: int = 3
    week_dots: int | None = None
    streak_weeks: int | None = None
    today_dot: bool | None = None
    workout_count: int | None = None
    workout_load: float | None = None
    load_bar: float | None = None
    day_shown: str | None = None
    book_finished: bool = False
    sleep_hours: float | None = None
    sleep_target_hours: float = 7.0
    steps: int | None = None
    as_of_utc: str | None = None
    books_ytd: int | None = None
    books_target: int = 12
    summary_line: str | None = None
    claude: ClaudeUsage = field(default_factory=ClaudeUsage)
    stale_hours: int = 24


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def valid_percent(value: float | None) -> bool:
    return value is not None and math.isfinite(value) and 0.0 <= value <= 100.0


def valid_sleep_hours(value: float | None) -> bool:
    return value is not None and math.isfinite(value) and 0.0 <= value <= MAX_SLEEP_HOURS


def has_health_data(view: DayView) -> bool:
    """A push has covered this day: it has sleep, steps or a workout. A row with only
    `workout_count` 0 says nothing; the engine writes that before any push arrives."""
    return (
        valid_sleep_hours(view.sleep_hours)
        or valid_count(view.steps)
        or view.today_dot is True
        or bool(view.workout_count)
    )


def valid_count(value: int | None) -> bool:
    return value is not None and not isinstance(value, bool) and value >= 0


def _whole(value: Any) -> int | None:
    number = _number(value)
    if number is None or number < 0 or number != int(number):
        return None
    return int(number)


def _sleep(value: Any) -> float | None:
    number = _number(value)
    return number if valid_sleep_hours(number) else None


def _utc_iso(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return to_utc_iso(from_utc_iso(value))
    except ValueError:
        return None


def claude_from_metrics(daily: dict[str, Any]) -> ClaudeUsage:
    """The seven-day keys and nothing else; a row without a valid percent is no reading."""
    used = _number(daily.get(CLAUDE_USED_PCT))
    if not valid_percent(used):
        return ClaudeUsage()
    return ClaudeUsage(
        used, _utc_iso(daily.get(CLAUDE_RESETS_AT)), _utc_iso(daily.get(CLAUDE_CAPTURED_AT))
    )


def week_start(day_local: str) -> str:
    day = date.fromisoformat(day_local)
    return (day - timedelta(days=day.weekday())).isoformat()


def view_from_metrics(
    day_local: str,
    daily: dict[str, Any],
    weekly: dict[str, Any],
    settings: Settings,
    *,
    as_of_utc: str | None = None,
    stored_sleep_hours: float | None = None,
    stored_steps: int | None = None,
    claude: ClaudeUsage | None = None,
) -> DayView:
    """Build the view. A `daily` value wins over its `stored_*` interim fallback.

    `day_local` is stored in canonical YYYY-MM-DD form; ValueError if it is not a date.
    """
    day_local = date.fromisoformat(day_local).isoformat()
    quality = daily.get(QUALITY_WORKOUT)
    summary = daily.get(SUMMARY_LINE)
    sleep = _sleep(daily.get(SLEEP_HOURS))
    steps = _whole(daily.get(STEPS))
    return DayView(
        day_local=day_local,
        home_tz=settings.home_tz,
        week_target=settings.week_target,
        week_dots=_whole(weekly.get(WEEK_QUALITY_WORKOUTS)),
        streak_weeks=_whole(weekly.get(WEEKS_HIT_STREAK)),
        today_dot=quality if isinstance(quality, bool) else None,
        workout_count=_whole(daily.get(WORKOUT_COUNT)),
        workout_load=_number(daily.get(WORKOUT_LOAD)),
        load_bar=_number(weekly.get(WEEK_LOAD_BAR)),
        book_finished=daily.get(BOOK_FINISHED) is True,
        sleep_hours=sleep if sleep is not None else _sleep(stored_sleep_hours),
        sleep_target_hours=settings.sleep_target_hours,
        steps=steps if steps is not None else _whole(stored_steps),
        as_of_utc=_utc_iso(as_of_utc),
        books_ytd=_whole(daily.get(BOOKS_YTD)),
        books_target=settings.books.target_per_year,
        summary_line=summary.strip() if isinstance(summary, str) and summary.strip() else None,
        claude=claude if claude is not None else claude_from_metrics(daily),
        stale_hours=settings.claude_usage.stale_hours,
    )


def load_fixture(path: Path, settings: Settings) -> tuple[DayView, datetime]:
    """Read a fixtures/days file. Returns the view and the fixture's own "now" (UTC).

    ValueError naming the file if its `day_local` or `now_utc` is missing or not a real date.
    """
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError(f"{path.name}: not a JSON object")
    day_local, now_text = record.get("day_local"), record.get("now_utc")
    try:
        day_local = date.fromisoformat(day_local).isoformat()
    except (TypeError, ValueError):
        raise ValueError(f"{path.name}: day_local {day_local!r} is not a valid date") from None
    try:
        now = from_utc_iso(now_text)
    except (TypeError, ValueError):
        raise ValueError(f"{path.name}: now_utc {now_text!r} is not a UTC ISO time") from None
    view = view_from_metrics(
        day_local,
        record.get("daily_metrics") or {},
        record.get("weekly_metrics") or {},
        settings,
        as_of_utc=record.get("as_of_utc"),
    )
    return view, now
