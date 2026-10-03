"""Recompute every daily and weekly metrics row from the stored tables.

`recompute` is the one function that touches the database. It reads active (not withdrawn)
activities and their HR series through `app.ingest.activities`, and
sleep, steps, wellness, books, health pushes and the load-bar history, computes every row
from the first stored day through today, and writes the changed rows and any new
calibration in one `BEGIN IMMEDIATE` transaction. It rewrites only the keys in
`keys.DAILY_KEYS` / `keys.WEEKLY_KEYS`; whatever else a row holds (ingest's Claude usage,
the summary's line) is kept. Run twice on the same data and clock it changes nothing.

A workout belongs to the home-timezone day its start falls on, and to that day's week.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from app.config import Settings
from app.ingest.activities import HrSampleRow, active_activities_sql, active_hr_samples
from app.ingest.health import SOURCE as HEALTH_SOURCE
from app.metrics import books as books_rules
from app.metrics.calendar import days_between, end_of_day_utc, local_date, week_start, weeks_between
from app.metrics.calibration import (
    BarEntry,
    Check,
    RunLoad,
    bar_for_week,
    entry_from_row,
    is_calibration_run,
    plan_with_checks,
)
from app.metrics.keys import STATE_CHECK, STATE_NOW, STATE_TODAY, WELLNESS_KEYS, round_half_up
from app.metrics.load import load_rows
from app.metrics.sleep import SleepFragment, night_seconds
from app.metrics.weekly import WeekInput, WeekRow, week_rows
from app.metrics.wellness import Series, wellness_json
from app.metrics.wins import sleep_win, wins
from app.metrics.zones import Sample, edwards_load, zone_floors
from app.timeutil import from_utc_iso, local_day, now_utc, to_utc_iso

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Activity:
    id: str
    type: str
    start_utc: str
    end_utc: str
    distance_m: float | None
    hr_incomplete: bool
    samples: tuple[Sample, ...]
    day_local: date


@dataclass
class Inputs:
    activities: list[Activity] = field(default_factory=list)
    sleep_s: dict[date, int] = field(default_factory=dict)
    steps: dict[date, int] = field(default_factory=dict)
    wellness: Series = field(default_factory=dict)
    book_dates: list[date] = field(default_factory=list)
    pushes_utc: list[datetime] = field(default_factory=list)
    bar_history: list[BarEntry] = field(default_factory=list)

    def first_day(self) -> date | None:
        candidates = [a.day_local for a in self.activities]
        candidates += list(self.sleep_s) + list(self.steps)
        for values in self.wellness.values():
            candidates += list(values)
        return min(candidates) if candidates else None


@dataclass(frozen=True)
class RecomputeResult:
    today_local: str
    now_utc: str
    first_day: str
    days_written: int
    weeks_written: int
    calibrations_added: int


def _sample_bpm(row: HrSampleRow) -> float | None:
    if row.bpm_avg is not None:
        return float(row.bpm_avg)
    if row.bpm_min is not None and row.bpm_max is not None:
        return (float(row.bpm_min) + float(row.bpm_max)) / 2
    return None


def read_inputs(conn: sqlite3.Connection, settings: Settings) -> Inputs:
    tz = settings.home_tz
    inputs = Inputs()
    samples: dict[str, list[Sample]] = {}
    for activity_id, series in active_hr_samples(conn).items():
        samples[activity_id] = [Sample(row.ts_utc, _sample_bpm(row)) for row in series]
    for row in conn.execute(
        active_activities_sql(
            "id, type, start_utc, end_utc, distance_m, hr_incomplete", order="start_utc, id"
        )
    ):
        inputs.activities.append(
            Activity(
                row["id"],
                str(row["type"]),
                row["start_utc"],
                row["end_utc"],
                float(row["distance_m"]) if row["distance_m"] is not None else None,
                bool(row["hr_incomplete"]),
                tuple(samples.get(row["id"], ())),
                local_date(row["start_utc"], tz),
            )
        )
    fragments = [
        SleepFragment(
            date.fromisoformat(row["wake_day_local"]),
            row["start_utc"],
            int(row["asleep_s"]),
            str(row["source"]),
        )
        for row in conn.execute(
            "SELECT wake_day_local, start_utc, asleep_s, source FROM sleep_sessions "
            "WHERE asleep_s IS NOT NULL ORDER BY wake_day_local, start_utc, source"
        )
    ]
    inputs.sleep_s = night_seconds(fragments, tz)
    for row in conn.execute("SELECT day_local, steps FROM steps_daily"):
        inputs.steps[date.fromisoformat(row["day_local"])] = int(row["steps"])
    for row in conn.execute("SELECT day_local, metric, value FROM wellness_daily"):
        name = WELLNESS_KEYS.get(row["metric"])
        if name is not None:
            inputs.wellness.setdefault(name, {})[date.fromisoformat(row["day_local"])] = float(
                row["value"]
            )
    for row in conn.execute("SELECT read_at, date_added FROM books"):
        when = books_rules.effective_date(row["read_at"], row["date_added"], settings)
        if when is not None:
            inputs.book_dates.append(when)
    for row in conn.execute(
        "SELECT received_at_utc FROM raw_archive AS r WHERE source = ? AND parsed_ok = 1 "
        "AND EXISTS (SELECT 1 FROM ingest_log AS l "
        "WHERE l.raw_archive_id = r.id AND l.workouts_seen > 0)",
        (HEALTH_SOURCE,),
    ):
        inputs.pushes_utc.append(from_utc_iso(row["received_at_utc"]))
    for row in conn.execute("SELECT * FROM load_bar_history ORDER BY effective_from_week"):
        inputs.bar_history.append(entry_from_row(dict(row)))
    return inputs


def activity_loads(activities: list[Activity], settings: Settings) -> dict[str, float | None]:
    """Edwards load per activity id; None without samples or when ingest flagged the trace."""
    floors = zone_floors(settings.hr_max, settings.zones_pct)
    gap = settings.metrics.max_sample_gap_s
    loads: dict[str, float | None] = {}
    for activity in activities:
        if activity.hr_incomplete or not activity.samples:
            loads[activity.id] = None
        else:
            loads[activity.id] = edwards_load(list(activity.samples), activity.end_utc, floors, gap)
    return loads


def _merge_row(
    conn: sqlite3.Connection, table: str, key_column: str, key: str, values: dict[str, object]
) -> bool:
    row = conn.execute(
        f'SELECT metrics_json FROM "{table}" WHERE "{key_column}" = ?', (key,)
    ).fetchone()
    existing: dict[str, object] = {}
    if row is not None:
        loaded = json.loads(row["metrics_json"])
        if isinstance(loaded, dict):
            existing = loaded
    merged = {**existing, **values}
    encoded = json.dumps(merged, sort_keys=True)
    if row is not None and encoded == row["metrics_json"]:
        return False
    conn.execute(
        f'INSERT INTO "{table}" ("{key_column}", metrics_json) VALUES (?, ?) '
        f'ON CONFLICT ("{key_column}") DO UPDATE SET metrics_json = excluded.metrics_json',
        (key, encoded),
    )
    return True


def _write_calibrations(conn: sqlite3.Connection, entries: list[BarEntry]) -> int:
    added = 0
    for entry in entries:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO load_bar_history "
            "(effective_from_week, value, source_ids_json, decided_at_utc) VALUES (?, ?, ?, ?)",
            (
                entry.effective_from_week,
                entry.value,
                json.dumps(list(entry.source_ids)),
                entry.decided_at_utc,
            ),
        )
        added += cursor.rowcount
    return added


def _resolve_clock(
    settings: Settings, today_local: str | None, now: datetime | None
) -> tuple[date, datetime]:
    if now is None and today_local is None:
        moment = now_utc()
        return date.fromisoformat(local_day(moment, settings.home_tz)), moment
    if now is None:
        day = date.fromisoformat(today_local or "")
        return day, end_of_day_utc(day, settings.home_tz).astimezone(UTC)
    moment = now.astimezone(UTC)
    day = (
        date.fromisoformat(today_local)
        if today_local
        else date.fromisoformat(local_day(moment, settings.home_tz))
    )
    return day, moment


def compute_rows(
    inputs: Inputs, settings: Settings, today: date, now: datetime
) -> tuple[list[BarEntry], list[WeekRow], dict[str, dict[str, object]], list[Check]]:
    """Pure: the calibration history, the week rows, the daily JSON per day and the
    load-bar re-checks since the last decision."""
    tz = settings.home_tz
    now_iso = to_utc_iso(now)
    activities = [a for a in inputs.activities if a.day_local <= today]
    loads = activity_loads(activities, settings)
    runs = [
        RunLoad(a.id, a.end_utc, loads[a.id] or 0.0)
        for a in activities
        if is_calibration_run(a.type, a.distance_m, loads[a.id]) and a.end_utc <= now_iso
    ]
    history, checks = plan_with_checks(inputs.bar_history, runs, settings, today)

    first = min(inputs.first_day() or today, today)
    bars = {
        week: bar_for_week(history, week.isoformat(), settings.workout.load_bar)
        for week in weeks_between(first, today)
    }

    def is_quality(activity: Activity) -> bool:
        load = loads[activity.id]
        return load is not None and load >= bars[week_start(activity.day_local)][0]

    week_inputs = [
        WeekInput(
            week,
            tuple(
                a.end_utc for a in activities if week_start(a.day_local) == week and is_quality(a)
            ),
            bar,
            source,
        )
        for week, (bar, source) in bars.items()
    ]
    weeks = week_rows(
        week_inputs,
        settings.week_target,
        now,
        inputs.pushes_utc,
        settings.metrics.week_close_grace_hours,
        tz,
    )

    by_day: dict[date, list[Activity]] = {}
    for activity in activities:
        by_day.setdefault(activity.day_local, []).append(activity)
    days = list(days_between(first, today))
    trimps = [sum(loads[a.id] or 0.0 for a in by_day.get(day, [])) for day in days]
    load_by_day = dict(zip(days, load_rows(trimps), strict=True))

    daily: dict[str, dict[str, object]] = {}
    for day in days:
        todays = by_day.get(day, [])
        scored = [loads[a.id] for a in todays if loads[a.id] is not None]
        quality = any(is_quality(a) for a in todays)
        asleep = inputs.sleep_s.get(day)
        sleep_hours = round_half_up(asleep / 3600, 2) if asleep is not None else None
        slept = sleep_win(sleep_hours, settings.sleep_target_hours)
        finished = books_rules.finished_on(inputs.book_dates, day)
        load = load_by_day[day]
        row: dict[str, object] = {
            "quality_workout": quality,
            "workout_count": len(todays),
            "workout_load": max(scored) if scored else None,
            "workout_ids": [a.id for a in todays],
            "sleep_hours": sleep_hours,
            "sleep_win": slept,
            "steps": inputs.steps.get(day),
            "books_ytd": books_rules.books_ytd(inputs.book_dates, day),
            "book_finished_today": finished,
            "wins": wins(slept, quality, finished),
            "load_trimp": load.trimp,
            "load_acute": load.acute,
            "load_chronic": load.chronic,
            "load_balance": load.balance,
        }
        row.update(wellness_json(inputs.wellness, day, settings))
        daily[day.isoformat()] = row
    return history, weeks, daily, checks


def _record_check(conn: sqlite3.Connection, check: Check) -> None:
    """Keep the latest re-check in metrics_state; log it once, when it is new."""
    row = conn.execute("SELECT value FROM metrics_state WHERE key = ?", (STATE_CHECK,)).fetchone()
    if row is not None and row["value"] == check.describe():
        return
    conn.execute(
        "INSERT INTO metrics_state (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (STATE_CHECK, check.describe()),
    )
    log.info("load bar re-check %s", check.describe())


def recompute(
    conn: sqlite3.Connection,
    settings: Settings,
    today_local: str | None = None,
    now: datetime | None = None,
) -> RecomputeResult:
    """Recompute and store every row. `today_local` and `now` default to the clock; a
    `today_local` given alone means the end of that home-timezone day."""
    today, moment = _resolve_clock(settings, today_local, now)
    conn.execute("BEGIN IMMEDIATE")
    try:
        inputs = read_inputs(conn, settings)
        history, weeks, daily, checks = compute_rows(inputs, settings, today, moment)
        added = _write_calibrations(conn, history[len(inputs.bar_history) :])
        if checks:
            _record_check(conn, checks[-1])
        days_written = sum(
            _merge_row(conn, "daily_metrics", "day_local", day, row) for day, row in daily.items()
        )
        weeks_written = sum(
            _merge_row(conn, "weekly_metrics", "week_start_local", w.week_start, w.as_json())
            for w in weeks
        )
        for key, value in ((STATE_TODAY, today.isoformat()), (STATE_NOW, to_utc_iso(moment))):
            conn.execute(
                "INSERT INTO metrics_state (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return RecomputeResult(
        today.isoformat(),
        to_utc_iso(moment),
        min(inputs.first_day() or today, today).isoformat(),
        days_written,
        weeks_written,
        added,
    )


def last_run(conn: sqlite3.Connection) -> tuple[str, str] | None:
    """(today_local, now_utc) of the last recompute, or None when it never ran."""
    rows = {
        row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM metrics_state")
    }
    if STATE_TODAY in rows and STATE_NOW in rows:
        return rows[STATE_TODAY], rows[STATE_NOW]
    return None
