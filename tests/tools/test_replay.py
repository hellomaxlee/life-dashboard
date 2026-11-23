from __future__ import annotations

import json

from tests.conftest import post_fixture
from tools import replay
from tools.replay import DATA_TABLES, checksum, diff, snapshot

FIXTURES = (
    "workouts_v2_overlap.json",
    "workouts_v2_recovery_only.json",
    "workouts_v2_no_hr.json",
    "metrics_v2_days.json",
    "metrics_v2_sleep_dst.json",
    "batch_part1.json",
    "batch_part2.json",
    "malformed.json",
)


def test_replay_matches_live_and_is_stable(client, db, settings, tmp_path):
    for name in FIXTURES:
        post_fixture(client, name)
    live = snapshot(db)
    assert all(
        len(live[t]) > 0
        for t in DATA_TABLES
        if t not in ("daily_metrics", "weekly_metrics", "books", "hr_minutes")
    )

    first = replay.replay(
        settings.storage.raw_dir, tmp_path / "scratch1.db", settings, since="2000-01-01"
    )
    second = replay.replay(settings.storage.raw_dir, tmp_path / "scratch2.db", settings)
    try:
        assert checksum(snapshot(first)) == checksum(live)
        assert checksum(snapshot(second)) == checksum(live)
        assert first.execute("SELECT COUNT(*) FROM raw_archive").fetchone()[0] == len(FIXTURES)
        bad = first.execute("SELECT parsed_ok FROM raw_archive WHERE parsed_ok = 0").fetchall()
        assert len(bad) == 1
        report = replay.report(first)
        assert report.endswith(checksum(live))
    finally:
        first.close()
        second.close()


def test_since_filters_by_filename_date(settings, client, tmp_path):
    post_fixture(client, "workouts_v2_run.json")
    conn = replay.replay(settings.storage.raw_dir, tmp_path / "s.db", settings, since="2999-01-01")
    try:
        assert conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0] == 0
    finally:
        conn.close()


def test_snapshot_diff_cli(client, db, settings, tmp_path, monkeypatch, capsys):
    post_fixture(client, "workouts_v2_run.json")
    snap = tmp_path / "before.json"
    assert replay.main(["--snapshot", str(snap)]) == 0
    assert replay.main(["--diff", str(snap)]) == 0
    post_fixture(client, "metrics_v2_days.json")
    assert replay.main(["--diff", str(snap)]) == 1
    out = capsys.readouterr().out
    assert "sleep_sessions: +" in out
    saved = json.loads(snap.read_text())
    assert diff(saved, saved) == []


def test_snapshot_of_a_db_missing_a_data_table_dumps_it_as_empty():
    """A backup of a db that is behind reads every data table; one the schema does not have
    yet (hr_minutes before migration 010) is empty, not `incomplete input`."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for table in DATA_TABLES:
        if table != "hr_minutes":
            conn.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY, metrics_json TEXT)')

    snap = snapshot(conn)

    assert snap["hr_minutes"] == []
    assert set(snap) == set(DATA_TABLES)
