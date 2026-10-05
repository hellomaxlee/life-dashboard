"""Build a DayView from the database. Kept apart from view.py so renderers load no db code.

Reads only; computes nothing and carries nothing forward.

The contract with the metrics engine (Phase 2) and the summary (Phase 3). The view looks up
exactly two rows: `daily_metrics` for the requested home-timezone day and `weekly_metrics`
for the Monday that starts its week. It never falls back to yesterday's row or last week's,
with one stated exception: the Today screen's day facts (see `view_from_db`).
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

The month feature is `app.month.store.load_feature` for the requested day's month, always
the requested day's even when the Today screen falls back to yesterday. Whatever goes wrong
reading it (no row, no table yet, a row that no longer parses) is no feature, and the Month
screen draws its calendar; it never costs the other screens their view.

The city status is `app.city.store.load_status` for the requested day, on the same terms:
the requested day's even when Today falls back, and anything that goes wrong reading it (the
module or its table not there yet, a row that does not parse, something that is not a
CityStatus) is no city status, which the City screen states as "CITY NO DATA".
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.city.model import CityStatus
from app.config import Settings
from app.ingest.health import SOURCE as HEALTH_SOURCE
from app.month.spec import MonthFeature
from app.render.view import (
    ClaudeUsage,
    DayView,
    claude_from_metrics,
    has_health_data,
    view_from_metrics,
    week_start,
)
from app.timeutil import to_utc_iso

CLAUDE_LOOKBACK_DAYS = 30
log = logging.getLogger(__name__)


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


def month_feature_for(conn: sqlite3.Connection, day_local: str) -> MonthFeature | None:
    """The stored feature for the day's month, or None. Never raises."""
    try:
        from app.month.store import load_feature

        return load_feature(conn, day_local[:7])
    except (ImportError, sqlite3.Error, ValueError, TypeError, KeyError, IndexError) as exc:
        log.warning("month feature for %s not loaded: %s: %s", day_local, type(exc).__name__, exc)
        return None


def city_for(conn: sqlite3.Connection, day_local: str) -> CityStatus | None:
    """The stored city status for the day, or None. Never raises."""
    try:
        from app.city.store import load_status

        status = load_status(conn, day_local)
    except (ImportError, sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        log.warning("city status for %s not loaded: %s: %s", day_local, type(exc).__name__, exc)
        return None
    return status if isinstance(status, CityStatus) else None


def view_from_db(conn: sqlite3.Connection, settings: Settings, day_local: str) -> DayView:
    """Build the view from what is stored for a home-timezone day. ValueError if not a date.

    Health pushes cover whole days ending yesterday, so the requested day usually has no
    sleep, steps or workout yet. The Today screen's day facts then come from the day before,
    when that day has them, and `day_shown` names it so the screen is headed YESTERDAY. The
    finished-book flag moves with them, so every small win belongs to the day shown.
    Nothing else falls back: week, streak, books, summary, Claude, the month feature and the
    city status stay the requested day's.
    """
    day_local = date.fromisoformat(day_local).isoformat()
    view = _view_for(conn, settings, day_local)
    view = replace(
        view, month_feature=month_feature_for(conn, day_local), city=city_for(conn, day_local)
    )
    if has_health_data(view):
        return view
    try:
        before = (date.fromisoformat(day_local) - timedelta(days=1)).isoformat()
    except OverflowError:
        return view
    prior = _view_for(conn, settings, before)
    if not has_health_data(prior):
        return view
    return replace(
        view,
        day_shown=before,
        sleep_hours=prior.sleep_hours,
        steps=prior.steps,
        today_dot=prior.today_dot,
        workout_count=prior.workout_count,
        workout_load=prior.workout_load,
        load_bar=prior.load_bar,
        book_finished=prior.book_finished,
    )


def _view_for(conn: sqlite3.Connection, settings: Settings, day_local: str) -> DayView:
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
