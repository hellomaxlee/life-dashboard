"""DayView: everything the three screens show, as plain typed values.

The renderer never queries or computes a metric. It is handed a DayView, and every field
that is None is drawn as a stated fallback ("NO DATA"), never as a blank and never as a
guess. Two builders make one: `load_fixture` (a hand-written file under fixtures/days/) and
`view_from_db` (what is stored for a date). Both go through `view_from_metrics`, so a
fixture's `daily_metrics` / `weekly_metrics` objects have the same shape as the table rows.

Keys this module reads. Phase 2 (metrics) and Phase 3 (summary) are expected to write the
ones marked with their phase; until they do, the screens show the fallback.

daily_metrics.metrics_json, row keyed by the home-timezone day:
  quality_workout          bool   a quality workout landed that day (Phase 2)
  sleep_hours              float  hours asleep in the session that woke that day (Phase 2);
                                  absent: the longest `sleep_sessions.asleep_s` for the wake day
  steps                    int    (Phase 2, optional); absent: `steps_daily.steps`
  books_ytd                int    books read in the row's calendar year, as of that day (Phase 2)
  summary_device_line      str    the one-sentence device summary, at most 110 chars (Phase 3)
  claude_week_used_pct     float  seven-day window, 0 to 100 (written today by ingest)
  claude_week_resets_at    str    UTC ISO or null (written today by ingest)
  claude_week_captured_at  str    UTC ISO (written today by ingest)

weekly_metrics.metrics_json, row keyed by the Monday that starts the week:
  quality_workouts         int    quality workouts so far that week (Phase 2)
  weeks_hit_streak         int    consecutive completed weeks at target, as of that week (Phase 2)

The Claude reading is filed under the day it was captured, so `view_from_db` takes the
latest reading on or before the requested day; the Week screen marks it stale by its age.
"as of" is the newest parsed Health push in `raw_archive` received before that day ended.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import Settings
from app.ingest.claude_usage import CAPTURED_AT, RESETS_AT, USED_PCT
from app.ingest.health import SOURCE as HEALTH_SOURCE
from app.timeutil import from_utc_iso, to_utc_iso

QUALITY_WORKOUT = "quality_workout"
SLEEP_HOURS = "sleep_hours"
STEPS = "steps"
BOOKS_YTD = "books_ytd"
SUMMARY_LINE = "summary_device_line"
WEEK_QUALITY_WORKOUTS = "quality_workouts"
WEEKS_HIT_STREAK = "weeks_hit_streak"

CLAUDE_LOOKBACK_DAYS = 30


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
    return float(value)


def _whole(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number >= 0 else None


def _utc_iso(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return to_utc_iso(from_utc_iso(value))
    except ValueError:
        return None


def claude_from_metrics(daily: dict[str, Any]) -> ClaudeUsage:
    """The seven-day keys and nothing else; a row without a percent is no reading."""
    used = _number(daily.get(USED_PCT))
    if used is None:
        return ClaudeUsage()
    return ClaudeUsage(used, _utc_iso(daily.get(RESETS_AT)), _utc_iso(daily.get(CAPTURED_AT)))


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
    date.fromisoformat(day_local)
    quality = daily.get(QUALITY_WORKOUT)
    summary = daily.get(SUMMARY_LINE)
    sleep = _number(daily.get(SLEEP_HOURS))
    steps = _whole(daily.get(STEPS))
    return DayView(
        day_local=day_local,
        home_tz=settings.home_tz,
        week_target=settings.week_target,
        week_dots=_whole(weekly.get(WEEK_QUALITY_WORKOUTS)),
        streak_weeks=_whole(weekly.get(WEEKS_HIT_STREAK)),
        today_dot=quality if isinstance(quality, bool) else None,
        sleep_hours=sleep if sleep is not None else stored_sleep_hours,
        sleep_target_hours=settings.sleep_target_hours,
        steps=steps if steps is not None else stored_steps,
        as_of_utc=_utc_iso(as_of_utc),
        books_ytd=_whole(daily.get(BOOKS_YTD)),
        books_target=settings.books.target_per_year,
        summary_line=summary.strip() if isinstance(summary, str) and summary.strip() else None,
        claude=claude if claude is not None else claude_from_metrics(daily),
        stale_hours=settings.claude_usage.stale_hours,
    )


def load_fixture(path: Path, settings: Settings) -> tuple[DayView, datetime]:
    """Read a fixtures/days file. Returns the view and the fixture's own "now" (UTC)."""
    record = json.loads(path.read_text(encoding="utf-8"))
    view = view_from_metrics(
        record["day_local"],
        record.get("daily_metrics") or {},
        record.get("weekly_metrics") or {},
        settings,
        as_of_utc=record.get("as_of_utc"),
    )
    return view, from_utc_iso(record["now_utc"])


def _metrics_row(conn: sqlite3.Connection, table: str, key_column: str, key: str) -> dict:
    row = conn.execute(
        f'SELECT metrics_json FROM "{table}" WHERE "{key_column}" = ?', (key,)
    ).fetchone()
    if row is None:
        return {}
    loaded = json.loads(row["metrics_json"])
    return loaded if isinstance(loaded, dict) else {}


def _latest_claude(conn: sqlite3.Connection, day_local: str) -> ClaudeUsage:
    rows = conn.execute(
        "SELECT metrics_json FROM daily_metrics WHERE day_local <= ? "
        "ORDER BY day_local DESC LIMIT ?",
        (day_local, CLAUDE_LOOKBACK_DAYS),
    ).fetchall()
    for row in rows:
        loaded = json.loads(row["metrics_json"])
        if isinstance(loaded, dict):
            reading = claude_from_metrics(loaded)
            if reading.used_pct is not None:
                return reading
    return ClaudeUsage()


def _day_end_utc(day_local: str, home_tz: str) -> str:
    next_day = date.fromisoformat(day_local) + timedelta(days=1)
    return to_utc_iso(datetime.combine(next_day, time.min, ZoneInfo(home_tz)))


def view_from_db(conn: sqlite3.Connection, settings: Settings, day_local: str) -> DayView:
    """Build the view from what is stored for a home-timezone day. Reads only; computes nothing."""
    daily = _metrics_row(conn, "daily_metrics", "day_local", day_local)
    weekly = _metrics_row(conn, "weekly_metrics", "week_start_local", week_start(day_local))
    sleep = conn.execute(
        "SELECT MAX(asleep_s) AS s FROM sleep_sessions WHERE wake_day_local = ?", (day_local,)
    ).fetchone()
    steps = conn.execute(
        "SELECT steps FROM steps_daily WHERE day_local = ?", (day_local,)
    ).fetchone()
    push = conn.execute(
        "SELECT MAX(received_at_utc) AS t FROM raw_archive "
        "WHERE source = ? AND parsed_ok = 1 AND received_at_utc < ?",
        (HEALTH_SOURCE, _day_end_utc(day_local, settings.home_tz)),
    ).fetchone()
    return view_from_metrics(
        day_local,
        daily,
        weekly,
        settings,
        as_of_utc=push["t"],
        stored_sleep_hours=sleep["s"] / 3600 if sleep["s"] is not None else None,
        stored_steps=int(steps["steps"]) if steps is not None else None,
        claude=_latest_claude(conn, day_local),
    )
