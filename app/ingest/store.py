"""Write parsed Health Auto Export records to SQLite. Every write is an upsert."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from statistics import fmean

from app.config import Settings
from app.ingest import dedupe
from app.ingest.parse import DailyValue, ParsedPayload, SleepSession, Workout
from app.timeutil import from_utc_iso


@dataclass
class IngestStats:
    workouts_seen: int = 0
    workouts_merged: int = 0
    metrics_rows: int = 0
    unknown_metrics: list[str] | None = None
    reapplied: int = 0


def _provenance(workout: Workout) -> dict[str, object]:
    return {
        "source_app": workout.source_app,
        "external_id": workout.external_id,
        "type": workout.type,
        "start_utc": workout.start_utc,
        "end_utc": workout.end_utc,
        "duration_s": workout.duration_s,
        "distance_m": workout.distance_m,
        "energy_kcal": workout.energy_kcal,
        "avg_hr": workout.avg_hr,
        "max_hr": workout.max_hr,
        "hr_sample_count": len(workout.hr_samples),
    }


def _canonical_id(
    conn: sqlite3.Connection, workout: Workout, settings: Settings
) -> tuple[str, bool]:
    known = conn.execute(
        "SELECT activity_id FROM activity_sources WHERE external_id = ? AND source_app = ?",
        (workout.external_id, workout.source_app),
    ).fetchone()
    if known is not None:
        return str(known["activity_id"]), False
    match = dedupe.find_matching_activity(
        conn, workout.start_utc, workout.duration_s, workout.source_app, settings.ingest.dedupe
    )
    if match is not None:
        return match, True
    return workout.external_id, False


def _merged_entries(conn: sqlite3.Connection, activity_id: str, workout: Workout) -> list[dict]:
    row = conn.execute(
        "SELECT merged_from_json FROM activities WHERE id = ?", (activity_id,)
    ).fetchone()
    entries: list[dict] = json.loads(row["merged_from_json"]) if row else []
    fresh = _provenance(workout)
    for index, entry in enumerate(entries):
        same_app = entry["source_app"] == fresh["source_app"]
        if same_app and entry["external_id"] == fresh["external_id"]:
            entries[index] = fresh
            return entries
    entries.append(fresh)
    return entries


def canonical_entry(entries: list[dict]) -> dict:
    """The copy that describes the activity: the first seen that carries HR samples, else
    the first seen. A copy keeps its place when a later push re-sends it, so the choice
    does not move with which copies a push happens to carry."""
    with_hr = [e for e in entries if e.get("hr_sample_count")]
    return (with_hr or entries)[0]


def _hr_fields(
    conn: sqlite3.Connection, activity_id: str, entries: list[dict]
) -> tuple[float | None, float | None, int, int]:
    counts = conn.execute(
        "SELECT source, COUNT(*) AS n, MIN(ts_utc) AS first_ts, MAX(ts_utc) AS last_ts "
        "FROM workout_hr_samples WHERE activity_id = ? GROUP BY source ORDER BY n DESC, source",
        (activity_id,),
    ).fetchall()
    if not counts:
        avg = next((e["avg_hr"] for e in entries if e.get("avg_hr") is not None), None)
        mx = next((e["max_hr"] for e in entries if e.get("max_hr") is not None), None)
        return avg, mx, 0, 0
    winner = counts[0]
    samples = conn.execute(
        "SELECT bpm_avg, bpm_max FROM workout_hr_samples WHERE activity_id = ? AND source = ?",
        (activity_id, winner["source"]),
    ).fetchall()
    avgs = [s["bpm_avg"] for s in samples if s["bpm_avg"] is not None]
    maxes = [s["bpm_max"] for s in samples if s["bpm_max"] is not None]
    avg_hr = fmean(avgs) if avgs else None
    max_hr = max(maxes) if maxes else None
    span = int((from_utc_iso(winner["last_ts"]) - from_utc_iso(winner["first_ts"])).total_seconds())
    return avg_hr, max_hr, int(winner["n"]), span


def _max_or_none(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return max(present) if present else None


def upsert_workout(
    conn: sqlite3.Connection, workout: Workout, raw_archive_id: int | None, settings: Settings
) -> bool:
    """Store one workout under its canonical activity. Returns True when it merged into another."""
    activity_id, merged = _canonical_id(conn, workout, settings)
    entries = _merged_entries(conn, activity_id, workout)
    canonical = canonical_entry(entries)
    conn.execute(
        "INSERT INTO activities (id, type, start_utc, end_utc, duration_s, merged_from_json) "
        "VALUES (?, ?, ?, ?, ?, '[]') ON CONFLICT (id) DO NOTHING",
        (
            activity_id,
            canonical["type"],
            canonical["start_utc"],
            canonical["end_utc"],
            canonical["duration_s"],
        ),
    )
    conn.execute(
        "INSERT INTO activity_sources "
        "(activity_id, source_app, external_id, start_utc, end_utc, raw_archive_id) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (external_id, source_app) DO UPDATE SET "
        "activity_id = excluded.activity_id, start_utc = excluded.start_utc, "
        "end_utc = excluded.end_utc",
        (
            activity_id,
            workout.source_app,
            workout.external_id,
            workout.start_utc,
            workout.end_utc,
            raw_archive_id,
        ),
    )
    conn.executemany(
        "INSERT OR REPLACE INTO workout_hr_samples "
        "(activity_id, ts_utc, bpm_min, bpm_avg, bpm_max, source) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (activity_id, s.ts_utc, s.bpm_min, s.bpm_avg, s.bpm_max, s.source)
            for s in workout.hr_samples
        ],
    )
    avg_hr, max_hr, count, span = _hr_fields(conn, activity_id, entries)
    duration_s = int(canonical["duration_s"])
    incomplete = int(count == 0 or span < settings.ingest.hr_incomplete_ratio * duration_s)
    conn.execute(
        "UPDATE activities SET type = ?, start_utc = ?, end_utc = ?, duration_s = ?, "
        "distance_m = ?, energy_kcal = ?, avg_hr = ?, max_hr = ?, hr_sample_count = ?, "
        "hr_span_s = ?, hr_incomplete = ?, merged_from_json = ? WHERE id = ?",
        (
            canonical["type"],
            canonical["start_utc"],
            canonical["end_utc"],
            duration_s,
            _max_or_none([e.get("distance_m") for e in entries]),
            _max_or_none([e.get("energy_kcal") for e in entries]),
            avg_hr,
            max_hr,
            count,
            span,
            incomplete,
            json.dumps(entries, sort_keys=True),
            activity_id,
        ),
    )
    return merged


def sleep_id(session: SleepSession) -> str:
    key = f"{session.start_utc}|{session.end_utc}|{session.source}".encode()
    return "sleep_" + hashlib.sha256(key).hexdigest()[:16]


def replace_sleep(conn: sqlite3.Connection, sessions: list[SleepSession]) -> None:
    """A payload is authoritative for each (wake day, source) it carries: that day's sessions
    from that source are replaced by the payload's, so a corrected night leaves one row."""
    for wake_day, source in sorted({(s.wake_day_local, s.source) for s in sessions}):
        conn.execute(
            "DELETE FROM sleep_sessions WHERE wake_day_local = ? AND source = ?",
            (wake_day, source),
        )
    for session in sessions:
        upsert_sleep(conn, session)


def upsert_sleep(conn: sqlite3.Connection, session: SleepSession) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO sleep_sessions (id, wake_day_local, start_utc, end_utc, "
        "in_bed_s, asleep_s, core_s, deep_s, rem_s, awake_s, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            sleep_id(session),
            session.wake_day_local,
            session.start_utc,
            session.end_utc,
            session.in_bed_s,
            session.asleep_s,
            session.core_s,
            session.deep_s,
            session.rem_s,
            session.awake_s,
            session.source,
        ),
    )


def upsert_steps(conn: sqlite3.Connection, value: DailyValue) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO steps_daily (day_local, steps, source) VALUES (?, ?, ?)",
        (value.day_local, int(round(value.value)), value.source),
    )


def upsert_wellness(conn: sqlite3.Connection, value: DailyValue) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO wellness_daily (day_local, metric, value, units, source) "
        "VALUES (?, ?, ?, ?, ?)",
        (value.day_local, value.metric, value.value, value.units, value.source),
    )


def store_payload(
    conn: sqlite3.Connection,
    parsed: ParsedPayload,
    raw_archive_id: int | None,
    settings: Settings,
    record_log: bool = True,
) -> IngestStats:
    stats = IngestStats(unknown_metrics=list(parsed.unknown_metrics))
    for workout in sorted(parsed.workouts, key=lambda w: (w.start_utc, w.external_id)):
        stats.workouts_seen += 1
        if upsert_workout(conn, workout, raw_archive_id, settings):
            stats.workouts_merged += 1
    replace_sleep(conn, parsed.sleep)
    for value in parsed.steps:
        upsert_steps(conn, value)
    for value in parsed.wellness:
        upsert_wellness(conn, value)
    stats.metrics_rows = len(parsed.sleep) + len(parsed.steps) + len(parsed.wellness)
    if raw_archive_id is not None and record_log:
        conn.execute(
            "INSERT INTO ingest_log "
            "(raw_archive_id, workouts_seen, workouts_merged, metrics_rows, unknown_metrics) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                raw_archive_id,
                stats.workouts_seen,
                stats.workouts_merged,
                stats.metrics_rows,
                json.dumps(stats.unknown_metrics),
            ),
        )
    return stats
