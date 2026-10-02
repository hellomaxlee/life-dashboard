"""A payload is authoritative for what it carries: a corrected night or a relabelled
wellness value replaces the earlier one. Canonical activities do not flip between copies."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.db import MIGRATIONS_DIR, connect, migrate, schema_version
from tests.conftest import count
from tests.payloads import post, sleep_payload, wellness_payload, workout, workouts_payload
from tools import replay
from tools.replay import checksum, snapshot


def sleep_rows(db) -> list[tuple[str, int]]:
    rows = db.execute("SELECT end_utc, asleep_s FROM sleep_sessions ORDER BY end_utc").fetchall()
    return [(r["end_utc"], r["asleep_s"]) for r in rows]


def test_corrected_night_replaces_the_first_report(client, db, settings, tmp_path):
    post(client, sleep_payload("2026-09-30 07:30:00 -0400", 8.0))
    post(client, sleep_payload("2026-09-30 07:00:00 -0400", 6.9))

    assert sleep_rows(db) == [("2026-09-30T11:00:00Z", 24840)]
    scratch = replay.replay(settings.storage.raw_dir, tmp_path / "s.db", settings, None, db)
    try:
        assert checksum(snapshot(scratch)) == checksum(snapshot(db))
    finally:
        scratch.close()


def test_a_nap_and_a_night_in_one_payload_both_stay(client, db):
    night = {
        "date": "2026-09-30",
        "asleep": 7.0,
        "sleepStart": "2026-09-29 23:00:00 -0400",
        "sleepEnd": "2026-09-30 06:00:00 -0400",
        "source": "Watch",
    }
    nap = {**night, "asleep": 1.0, "sleepStart": "2026-09-30 14:00:00 -0400"}
    nap["sleepEnd"] = "2026-09-30 15:00:00 -0400"
    other = {**night, "asleep": 6.5, "source": "Ring"}
    metric = {"name": "sleep_analysis", "units": "hr", "data": [night, nap, other]}
    post(client, json.dumps({"data": {"metrics": [metric]}}).encode())
    assert count(db, "sleep_sessions") == 3

    watch_only = {"name": "sleep_analysis", "units": "hr", "data": [{**night, "asleep": 6.0}]}
    post(client, json.dumps({"data": {"metrics": [watch_only]}}).encode())

    rows = db.execute("SELECT source, asleep_s FROM sleep_sessions ORDER BY source").fetchall()
    assert [(r["source"], r["asleep_s"]) for r in rows] == [("Ring", 23400), ("Watch", 21600)]


def test_wellness_value_with_a_changed_source_label_replaces_the_old_one(client, db):
    post(client, wellness_payload(58, "Max's Apple Watch"))
    post(client, wellness_payload(52, "Max's Apple Watch|Max's iPhone"))

    rows = db.execute("SELECT metric, value, source FROM wellness_daily").fetchall()
    assert [tuple(r) for r in rows] == [
        ("resting_heart_rate", 52.0, "Max's Apple Watch|Max's iPhone")
    ]


def schema(conn) -> list[str]:
    rows = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name").fetchall()
    return [f"{r['type']} {r['name']} {r['sql']}" for r in rows]


def test_migration_002_keeps_the_latest_ingested_value_and_applies_once(tmp_path: Path):
    only_first = tmp_path / "migrations"
    only_first.mkdir()
    shutil.copy(MIGRATIONS_DIR / "001_initial.sql", only_first)
    conn = connect(tmp_path / "old.db")
    assert migrate(conn, only_first) == [1]
    wellness = (
        "INSERT OR REPLACE INTO wellness_daily (day_local, metric, value, units, source) "
        "VALUES (?, ?, ?, 'bpm', ?)"
    )
    conn.execute(wellness, ("2026-09-30", "resting_heart_rate", 58, "Watch"))
    conn.execute(wellness, ("2026-09-30", "resting_heart_rate", 52, "Watch|iPhone"))
    conn.execute(wellness, ("2026-09-30", "resting_heart_rate", 57, "Watch"))
    conn.execute(wellness, ("2026-09-29", "resting_heart_rate", 60, "Watch"))
    sleep = (
        "INSERT INTO sleep_sessions (id, wake_day_local, start_utc, end_utc, asleep_s, source) "
        "VALUES (?, '2026-09-30', ?, ?, ?, 'aggregate')"
    )
    conn.execute(sleep, ("first", "2026-09-30T03:00:00Z", "2026-09-30T11:30:00Z", 28800))
    conn.execute(sleep, ("corrected", "2026-09-30T03:00:00Z", "2026-09-30T11:00:00Z", 24840))
    conn.execute(sleep, ("nap", "2026-09-30T18:00:00Z", "2026-09-30T19:00:00Z", 3600))

    assert migrate(conn) == [2]

    rows = conn.execute(
        "SELECT day_local, value, source FROM wellness_daily ORDER BY day_local"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("2026-09-29", 60.0, "Watch"),
        ("2026-09-30", 57.0, "Watch"),
    ]
    kept = conn.execute("SELECT id FROM sleep_sessions ORDER BY id").fetchall()
    assert [r["id"] for r in kept] == ["corrected", "nap"]
    after = schema(conn)
    assert migrate(conn) == []
    assert schema(conn) == after
    assert schema_version(conn) == 2
    conn.close()


def activities(db) -> list[tuple]:
    rows = db.execute("SELECT * FROM activities ORDER BY start_utc").fetchall()
    return [
        (r["id"], r["type"], r["duration_s"], len(json.loads(r["merged_from_json"]))) for r in rows
    ]


def test_canonical_record_does_not_flip_when_a_later_push_carries_one_copy(client, db):
    watch = workout("W-1", "07:00", 40, "Apple Watch", name="Running")
    phone = workout("P-1", "07:01", 39, "Nike Run Club", hr=False, name="Run (NRC)")
    post(client, workouts_payload(watch, phone))
    assert activities(db) == [("W-1", "Running", 2400, 2)]

    post(client, workouts_payload(watch, note="later push, Watch copy only"))
    assert activities(db) == [("W-1", "Running", 2400, 2)]

    post(client, workouts_payload(phone, note="phone copy only"))
    assert activities(db) == [("W-1", "Running", 2400, 2)]


def test_copy_with_heart_rate_is_canonical_even_when_it_arrives_second(client, db):
    phone = workout("P-1", "07:00", 39, "Nike Run Club", hr=False, name="Run (NRC)")
    watch = workout("W-1", "07:01", 40, "Apple Watch", name="Running")
    post(client, workouts_payload(phone))
    post(client, workouts_payload(watch, note="second"))

    assert activities(db) == [("P-1", "Running", 2400, 2)]


def test_two_overlapping_copies_with_unknown_source_merge(client, db):
    post(client, workouts_payload(workout("W-1", "07:00", 40), workout("P-1", "07:01", 39)))

    assert [(a[0], a[3]) for a in activities(db)] == [("W-1", 2)]
