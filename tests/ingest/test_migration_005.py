"""Migration 005 (issue #4): withdrawn_at columns, per-copy provenance and HR samples, one
night per person across labels. Applies once; `recluster_all` (tools.migrate) normalises."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.config import load_settings
from app.db import CODE_SCHEMA_VERSION, MIGRATIONS_DIR, connect, migrate
from app.ingest.store import recluster_all


def schema(conn) -> list[str]:
    rows = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name").fetchall()
    return [f"{r['type']} {r['name']} {r['sql']}" for r in rows]


def entry(id_: str, source: str, start: str, end: str, hr: int, type_: str) -> dict:
    return {
        "external_id": id_,
        "source_app": source,
        "type": type_,
        "start_utc": start,
        "end_utc": end,
        "duration_s": 2400,
        "distance_m": 6437.0,
        "energy_kcal": None,
        "avg_hr": None,
        "max_hr": None,
        "hr_sample_count": hr,
    }


def old_db(tmp_path: Path):
    before = tmp_path / "migrations"
    before.mkdir()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if int(path.name[:3]) < 5:
            shutil.copy(path, before)
    conn = connect(tmp_path / "old.db")
    assert migrate(conn, before) == [1, 2, 3, 4]
    w = entry("W-1", "Apple Watch", "2026-09-22T11:01:00Z", "2026-09-22T11:41:00Z", 3, "Running")
    p = entry("P-1", "Nike Run Club", "2026-09-22T11:00:00Z", "2026-09-22T11:39:00Z", 0, "Run")
    # Stored under the pre-#4 rule: first-seen id, arrival order.
    conn.execute(
        "INSERT INTO activities (id, type, start_utc, end_utc, duration_s, merged_from_json) "
        "VALUES ('P-1', 'Run', '2026-09-22T11:00:00Z', '2026-09-22T11:39:00Z', 2340, ?)",
        (json.dumps([p, w]),),
    )
    for e in (w, p):
        conn.execute(
            "INSERT INTO activity_sources (activity_id, source_app, external_id, start_utc, "
            "end_utc) VALUES ('P-1', ?, ?, ?, ?)",
            (e["source_app"], e["external_id"], e["start_utc"], e["end_utc"]),
        )
    for minute in (1, 3, 5):
        conn.execute(
            "INSERT INTO workout_hr_samples (activity_id, ts_utc, bpm_avg, source) "
            "VALUES ('P-1', ?, 150, 'Apple Watch')",
            (f"2026-09-22T11:{minute:02d}:00Z",),
        )
    conn.execute(
        "INSERT INTO workout_hr_samples (activity_id, ts_utc, bpm_avg, source) "
        "VALUES ('P-1', '2026-09-22T11:07:00Z', 151, 'Polar H10')"
    )
    sleep = (
        "INSERT INTO sleep_sessions (id, wake_day_local, start_utc, end_utc, asleep_s, source) "
        "VALUES (?, '2026-09-30', ?, ?, ?, ?)"
    )
    conn.execute(sleep, ("a", "2026-09-30T03:00:00Z", "2026-09-30T10:30:00Z", 25920, "Watch"))
    conn.execute(sleep, ("b", "2026-09-30T03:10:00Z", "2026-09-30T10:40:00Z", 24840, "Oura"))
    conn.execute(sleep, ("nap", "2026-09-30T10:20:00Z", "2026-09-30T11:20:00Z", 2880, "Ring"))
    return conn


def test_migration_005_keeps_every_copy_and_sample_and_applies_once(tmp_path):
    conn = old_db(tmp_path)

    assert migrate(conn) == list(range(5, CODE_SCHEMA_VERSION + 1))
    after = schema(conn)
    assert migrate(conn) == []
    assert schema(conn) == after
    assert CODE_SCHEMA_VERSION >= 5

    samples = conn.execute(
        "SELECT external_id, source_app, source, COUNT(*) AS n FROM workout_hr_samples "
        "GROUP BY 1, 2, 3 ORDER BY 3"
    ).fetchall()
    assert [tuple(r) for r in samples] == [
        ("W-1", "Apple Watch", "Apple Watch", 3),
        ("P-1", "Nike Run Club", "Polar H10", 1),
    ]
    prov = conn.execute(
        "SELECT external_id, json_extract(provenance_json, '$.type') FROM activity_sources "
        "ORDER BY 1"
    ).fetchall()
    assert [tuple(r) for r in prov] == [("P-1", "Run"), ("W-1", "Running")]
    nights = conn.execute("SELECT id FROM sleep_sessions ORDER BY id").fetchall()
    assert [r["id"] for r in nights] == ["b", "nap"]
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(activities)")]
    assert "withdrawn_at" in cols

    settings = load_settings()
    assert recluster_all(conn, settings) == 1
    row = conn.execute("SELECT * FROM activities").fetchone()
    assert (row["id"], row["type"], row["hr_sample_count"], row["withdrawn_at"]) == (
        "W-1",
        "Running",
        3,
        None,
    )
    assert [e["external_id"] for e in json.loads(row["merged_from_json"])] == ["W-1", "P-1"]
    assert recluster_all(conn, settings) == 0
    conn.close()
