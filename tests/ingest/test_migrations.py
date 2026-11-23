from __future__ import annotations

from pathlib import Path

from app.db import CODE_SCHEMA_VERSION, connect, migrate, schema_version, table_names

EXPECTED_TABLES = {
    "raw_archive",
    "activities",
    "activity_sources",
    "workout_hr_samples",
    "sleep_sessions",
    "steps_daily",
    "wellness_daily",
    "daily_metrics",
    "weekly_metrics",
    "books",
    "ingest_log",
    "schema_version",
    "summary_lines",
    "model_spend",
    "load_bar_history",
    "metrics_state",
    "month_features",
    "month_feature_attempts",
    "city_snapshots",
    "manual_workouts",
    "judged_workouts",
    "hr_minutes",
}


def _schema(conn) -> list[str]:
    rows = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name").fetchall()
    return [f"{r['type']} {r['name']} {r['sql']}" for r in rows]


def test_apply_twice_is_noop(tmp_path: Path):
    conn = connect(tmp_path / "m.db")
    first = migrate(conn)
    assert first == list(range(1, CODE_SCHEMA_VERSION + 1))
    after_first = _schema(conn)
    assert migrate(conn) == []
    assert _schema(conn) == after_first
    assert schema_version(conn) == CODE_SCHEMA_VERSION
    assert set(table_names(conn)) == EXPECTED_TABLES
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()
