"""Build a DayView from the database. Kept apart from view.py so renderers load no db code.

Reads only; computes nothing. The Claude reading is filed under the day it was captured, so
the view takes the latest reading on or before the requested day and the Week screen marks
it stale by its age. "As of" is the newest parsed Health push in `raw_archive` received before
that day ended. Sleep and steps come from `daily_metrics` when Phase 2 has written them; the
raw tables are an interim fallback only (see view.py).
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


def _day_end_utc(day_local: str, home_tz: str) -> str:
    next_day = date.fromisoformat(day_local) + timedelta(days=1)
    return to_utc_iso(datetime.combine(next_day, time.min, ZoneInfo(home_tz)))


def view_from_db(conn: sqlite3.Connection, settings: Settings, day_local: str) -> DayView:
    """Build the view from what is stored for a home-timezone day."""
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
