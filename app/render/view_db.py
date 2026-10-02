"""Build a DayView from the database. Kept apart from view.py so renderers load no db code.

Reads only; computes nothing and carries nothing forward.

The contract with the metrics engine (Phase 2) and the summary (Phase 3). The view looks up
exactly two rows: `daily_metrics` for the requested home-timezone day and `weekly_metrics`
for the Monday that starts its week. It never falls back to yesterday's row or last week's.
So the engine must write the new day's row at rollover (00:00 local) with the values that
carry over (`books_ytd`, and the summary once there is one), and the new week's row on Monday
00:00 local (`quality_workouts` = 0, `weeks_hit_streak` as it stands). Until those rows
exist, the Books count, the summary, the week count and the streak show NO DATA. That is
deliberate: a carried number would be the renderer inventing a metric. A test pins it.

Three things are read outside those two rows, all stated on the frame:
- the Claude reading is filed under the day it was captured, so the view takes the latest
  reading on or before the requested day and the Week screen marks it stale by its age;
- "as of" is the newest parsed Health push in `raw_archive` received before that day ended.
  A past day whose numbers arrived only with a later push has no such push; the Today screen
  then leaves the as-of line out rather than print "NO PUSH YET" beside real numbers;
- sleep and steps come from `daily_metrics` when Phase 2 has written them; the raw tables
  are an interim fallback only (see view.py).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import Settings
from app.ingest.health import SOURCE as HEALTH_SOURCE
from app.render.view import ClaudeUsage, DayView, claude_from_metrics, view_from_metrics, week_start
from app.timeutil import to_utc_iso

CLAUDE_LOOKBACK_DAYS = 30


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


def _day_end_utc(day_local: str, home_tz: str) -> str | None:
    """The first instant after the day, or None for a day at the edge of the calendar."""
    try:
        next_day = date.fromisoformat(day_local) + timedelta(days=1)
        return to_utc_iso(datetime.combine(next_day, time.min, ZoneInfo(home_tz)))
    except (OverflowError, ValueError):
        return None


def _last_push(conn: sqlite3.Connection, day_local: str, home_tz: str) -> str | None:
    day_end = _day_end_utc(day_local, home_tz)
    if day_end is None:
        return None
    row = conn.execute(
        "SELECT MAX(received_at_utc) AS t FROM raw_archive "
        "WHERE source = ? AND parsed_ok = 1 AND received_at_utc < ?",
        (HEALTH_SOURCE, day_end),
    ).fetchone()
    return row["t"]


def view_from_db(conn: sqlite3.Connection, settings: Settings, day_local: str) -> DayView:
    """Build the view from what is stored for a home-timezone day. ValueError if not a date."""
    day_local = date.fromisoformat(day_local).isoformat()
    daily = _metrics_row(conn, "daily_metrics", "day_local", day_local)
    weekly = _metrics_row(conn, "weekly_metrics", "week_start_local", week_start(day_local))
    sleep = conn.execute(
        "SELECT MAX(asleep_s) AS s FROM sleep_sessions WHERE wake_day_local = ?", (day_local,)
    ).fetchone()
    steps = conn.execute(
        "SELECT steps FROM steps_daily WHERE day_local = ?", (day_local,)
    ).fetchone()
    return view_from_metrics(
        day_local,
        daily,
        weekly,
        settings,
        as_of_utc=_last_push(conn, day_local, settings.home_tz),
        stored_sleep_hours=sleep["s"] / 3600 if sleep["s"] is not None else None,
        stored_steps=int(steps["steps"]) if steps is not None else None,
        claude=_latest_claude(conn, day_local),
    )
