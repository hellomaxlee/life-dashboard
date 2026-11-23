"""Load a fixtures/golden/*.json case into a database and compare the engine's rows with it.

A golden file is hand-written before any engine code and states its arithmetic in
`comment`. Shape:

  today_local, now_utc        the clock the engine is run under
  inputs                      what is stored before the first run
    activities[]              {id, type, start_local "YYYY-MM-DD HH:MM", minutes,
                               distance_mi?, hr: [[minutes, bpm], ...] | null,
                               hr_incomplete?}; or {"repeat_weekly": {id_prefix, from, to,
                               weekdays, time, type, minutes, distance_mi?, hr}}
                              hr segments expand to one sample per minute from the start,
                              bpm_avg = bpm, so the hand arithmetic is minutes x zone weight
    sleep[]                   {wake_day, start_local, end_local, asleep_h, in_bed_h?}
    steps                     {day: count}
    wellness[]                {metric, value, day} or {metric, value, from, to} (one per day)
    books[]                   {id, title, read_at, date_added}
    load_bar_history[]        rows already decided
    health_pushes_utc[]       parsed health pushes in raw_archive (week closure): a UTC ISO
                              string is a Workouts push; {"at", "workouts": 0} a metrics-only one
  expected                    {daily: {day: {key: value}}, weekly: {monday: {...}},
                               load_bar_history: [...]}; every key listed must match exactly
  stages[]                    optional; each {today_local, now_utc, add: <inputs shape>,
                               expected}, run in order on the same database

Times are home-timezone local; the loader converts them to the UTC the tables store.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import Settings
from app.metrics.engine import RecomputeResult, recompute
from app.timeutil import from_utc_iso, to_utc_iso

GOLDEN_DIR = Path(__file__).resolve().parent.parent.parent / "fixtures" / "golden"
HEALTH = "health"
MILE_M = 1609.344


def golden_files() -> list[Path]:
    return sorted(GOLDEN_DIR.glob("*.json"))


def load_golden(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _local(text: str, tz: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo(tz))


def _expand_activities(entries: list[dict]) -> list[dict]:
    out: list[dict] = []
    for entry in entries:
        if "repeat_weekly" not in entry:
            out.append(entry)
            continue
        spec = entry["repeat_weekly"]
        day = date.fromisoformat(spec["from"])
        last = date.fromisoformat(spec["to"])
        while day <= last:
            if day.weekday() in spec["weekdays"]:
                out.append(
                    {
                        "id": f"{spec['id_prefix']}{day.isoformat()}",
                        "type": spec["type"],
                        "start_local": f"{day.isoformat()} {spec['time']}",
                        "minutes": spec["minutes"],
                        "distance_mi": spec.get("distance_mi"),
                        "hr": spec.get("hr"),
                        "hr_incomplete": spec.get("hr_incomplete", False),
                    }
                )
            day += timedelta(days=1)
    return out


def insert_activity(conn: sqlite3.Connection, entry: dict, tz: str) -> None:
    start = _local(entry["start_local"], tz)
    minutes = int(entry["minutes"])
    end = start + timedelta(minutes=minutes)
    segments = entry.get("hr") or []
    samples: list[tuple[str, float]] = []
    offset = 0
    for seg_minutes, bpm in segments:
        for _ in range(int(seg_minutes)):
            samples.append((to_utc_iso(start + timedelta(minutes=offset)), float(bpm)))
            offset += 1
    distance = entry.get("distance_mi")
    span = (len(samples) - 1) * 60 if samples else 0
    incomplete = int(bool(entry.get("hr_incomplete")) or not samples)
    conn.execute(
        "INSERT INTO activities (id, type, start_utc, end_utc, duration_s, distance_m, "
        "hr_sample_count, hr_span_s, hr_incomplete, merged_from_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '[]')",
        (
            entry["id"],
            entry["type"],
            to_utc_iso(start),
            to_utc_iso(end),
            minutes * 60,
            float(distance) * MILE_M if distance is not None else None,
            len(samples),
            span,
            incomplete,
        ),
    )
    conn.execute(
        "INSERT INTO activity_sources (external_id, source_app, activity_id, start_utc, end_utc) "
        "VALUES (?, 'Apple Watch', ?, ?, ?)",
        (entry["id"], entry["id"], to_utc_iso(start), to_utc_iso(end)),
    )
    conn.executemany(
        "INSERT INTO workout_hr_samples "
        "(external_id, source_app, ts_utc, bpm_min, bpm_avg, bpm_max, source) "
        "VALUES (?, 'Apple Watch', ?, ?, ?, ?, 'Apple Watch')",
        [(entry["id"], ts, bpm - 5, bpm, bpm + 5) for ts, bpm in samples],
    )


def insert_sleep(conn: sqlite3.Connection, entry: dict, tz: str) -> None:
    start, end = _local(entry["start_local"], tz), _local(entry["end_local"], tz)
    asleep = int(round(float(entry["asleep_h"]) * 3600))
    in_bed = int(round(float(entry.get("in_bed_h", entry["asleep_h"])) * 3600))
    conn.execute(
        "INSERT INTO sleep_sessions (id, wake_day_local, start_utc, end_utc, in_bed_s, "
        "asleep_s, source) VALUES (?, ?, ?, ?, ?, ?, 'Apple Watch')",
        (
            f"sleep-{to_utc_iso(start)}",
            entry["wake_day"],
            to_utc_iso(start),
            to_utc_iso(end),
            in_bed,
            asleep,
        ),
    )


def _expand_wellness(entries: list[dict]) -> list[tuple[str, str, float]]:
    rows: list[tuple[str, str, float]] = []
    for entry in entries:
        if "day" in entry:
            rows.append((entry["day"], entry["metric"], float(entry["value"])))
            continue
        day, last = date.fromisoformat(entry["from"]), date.fromisoformat(entry["to"])
        while day <= last:
            rows.append((day.isoformat(), entry["metric"], float(entry["value"])))
            day += timedelta(days=1)
    return rows


def insert_push(conn: sqlite3.Connection, push: str | dict) -> None:
    """A parsed health push. A bare timestamp is a Workouts push (ingest_log workouts_seen 1);
    {"at": ts, "workouts": 0} is a Health Metrics push that carried no workout."""
    received_at_utc = push if isinstance(push, str) else push["at"]
    workouts = 1 if isinstance(push, str) else int(push.get("workouts", 0))
    from_utc_iso(received_at_utc)
    cursor = conn.execute(
        "INSERT INTO raw_archive (source, received_at_utc, sha256, path, byte_len, parsed_ok) "
        "VALUES (?, ?, ?, ?, 2, 1)",
        (HEALTH, received_at_utc, f"sha-{received_at_utc}", f"{HEALTH}/{received_at_utc}.json"),
    )
    conn.execute(
        "INSERT INTO ingest_log (raw_archive_id, workouts_seen) VALUES (?, ?)",
        (cursor.lastrowid, workouts),
    )


def apply_inputs(conn: sqlite3.Connection, inputs: dict, settings: Settings) -> None:
    tz = settings.home_tz
    for entry in _expand_activities(inputs.get("activities", [])):
        insert_activity(conn, entry, tz)
    for entry in inputs.get("sleep", []):
        insert_sleep(conn, entry, tz)
    for day, steps in inputs.get("steps", {}).items():
        conn.execute(
            "INSERT INTO steps_daily (day_local, steps, source) VALUES (?, ?, 'iPhone')",
            (day, int(steps)),
        )
    for day, metric, value in _expand_wellness(inputs.get("wellness", [])):
        conn.execute(
            "INSERT INTO wellness_daily (day_local, metric, value, units, source) "
            "VALUES (?, ?, ?, NULL, 'Apple Watch')",
            (day, metric, value),
        )
    for book in inputs.get("books", []):
        conn.execute(
            "INSERT INTO books (id, title, author, read_at, date_added, date_inferred) "
            "VALUES (?, ?, NULL, ?, ?, 0)",
            (book["id"], book["title"], book.get("read_at"), book.get("date_added")),
        )
    for entry in inputs.get("load_bar_history", []):
        conn.execute(
            "INSERT INTO load_bar_history (effective_from_week, value, source_ids_json, "
            "decided_at_utc) VALUES (?, ?, ?, ?)",
            (
                entry["effective_from_week"],
                float(entry["value"]),
                json.dumps(entry.get("source_ids", [])),
                entry["decided_at_utc"],
            ),
        )
    for received in inputs.get("health_pushes_utc", []):
        insert_push(conn, received)


def metrics_rows(conn: sqlite3.Connection, table: str, key_column: str) -> dict[str, dict]:
    return {
        row[key_column]: json.loads(row["metrics_json"])
        for row in conn.execute(f'SELECT "{key_column}", metrics_json FROM "{table}"')
    }


def history_rows(conn: sqlite3.Connection) -> list[dict]:
    return [
        {
            "effective_from_week": row["effective_from_week"],
            "value": row["value"],
            "source_ids": json.loads(row["source_ids_json"]),
            "decided_at_utc": row["decided_at_utc"],
        }
        for row in conn.execute("SELECT * FROM load_bar_history ORDER BY effective_from_week")
    ]


def mismatches(conn: sqlite3.Connection, expected: dict) -> list[str]:
    """Every expected key whose stored value differs, as 'table day key: want != got'."""
    lines: list[str] = []
    for table, key_column, label in (
        ("daily_metrics", "day_local", "daily"),
        ("weekly_metrics", "week_start_local", "weekly"),
    ):
        stored = metrics_rows(conn, table, key_column)
        for key, wanted in expected.get(label, {}).items():
            row = stored.get(key)
            if row is None:
                lines.append(f"{label} {key}: no row")
                continue
            for name, want in wanted.items():
                got = row.get(name, "<absent>")
                if got != want or type(got) is not type(want):
                    lines.append(f"{label} {key} {name}: want {want!r} != got {got!r}")
    if "load_bar_history" in expected:
        got_history = history_rows(conn)
        if got_history != expected["load_bar_history"]:
            lines.append(
                f"load_bar_history: want {expected['load_bar_history']!r} != got {got_history!r}"
            )
    return lines


def run_stage(
    conn: sqlite3.Connection, settings: Settings, today_local: str, now_utc: str
) -> RecomputeResult:
    return recompute(conn, settings, today_local, from_utc_iso(now_utc))


def run_golden(conn: sqlite3.Connection, settings: Settings, case: dict) -> list[str]:
    """Load, run every stage, return every mismatch (empty = the case passes)."""
    apply_inputs(conn, case.get("inputs", {}), settings)
    problems: list[str] = []
    stages = case.get("stages") or [
        {
            "today_local": case["today_local"],
            "now_utc": case["now_utc"],
            "expected": case["expected"],
        }
    ]
    for index, stage in enumerate(stages):
        if stage.get("add"):
            apply_inputs(conn, stage["add"], settings)
        run_stage(conn, settings, stage["today_local"], stage["now_utc"])
        problems.extend(f"stage {index}: {line}" for line in mismatches(conn, stage["expected"]))
    return problems
