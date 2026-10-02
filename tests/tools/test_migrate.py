"""Migrations run at service start and from tools.migrate, never as a side effect of a
request, a job, or a tool that only reads the live db."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import CODE_SCHEMA_VERSION, MIGRATIONS_DIR, connect, migrate
from app.main import create_app
from tests.payloads import post, steps, steps_payload, workout, workouts_payload
from tools import backup, replay, sync
from tools import migrate as migrate_tool


def version(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    finally:
        conn.close()


@pytest.fixture
def behind(settings, tmp_path) -> Path:
    """A live db one migration behind the code, as after `git pull` and before upgrading."""
    only_first = tmp_path / "migrations"
    only_first.mkdir()
    shutil.copy(MIGRATIONS_DIR / "001_initial.sql", only_first)
    conn = connect(settings.storage.db_path)
    assert migrate(conn, only_first) == [1]
    conn.execute(
        "INSERT INTO wellness_daily (day_local, metric, value, units, source) "
        "VALUES ('2026-09-30', 'resting_heart_rate', 58, 'bpm', 'Watch')"
    )
    conn.close()
    return settings.storage.db_path


@pytest.mark.parametrize(
    "command",
    [
        lambda tmp: replay.main(["--snapshot", str(tmp / "snap.json")]),
        lambda tmp: replay.main(["--verify", "--scratch", str(tmp / "scratch.db")]),
        lambda tmp: replay.main(["--since", "2000-01-01", "--scratch", str(tmp / "scratch.db")]),
        lambda tmp: sync.main(["--source", "claude_usage"]),
    ],
    ids=["replay-snapshot", "replay-verify", "replay-since", "sync"],
)
def test_tools_that_read_live_refuse_a_db_that_is_behind(behind, tmp_path, capsys, command):
    code = command(tmp_path)
    err = capsys.readouterr().err

    assert version(behind) == 1
    assert code == 2
    assert "tools.migrate" in err


def test_backup_of_a_db_that_is_behind_works_and_migrates_nothing(behind, tmp_path, capsys):
    assert backup.main(["--out", str(tmp_path / "pre-upgrade")]) == 0
    assert version(behind) == 1
    assert version(tmp_path / "pre-upgrade" / backup.DB_NAME) == 1

    assert backup.main(["--verify", str(tmp_path / "pre-upgrade")]) == 2
    assert "tools.migrate" in capsys.readouterr().err
    assert version(behind) == 1
    assert version(tmp_path / "pre-upgrade" / backup.DB_NAME) == 1


def test_tools_migrate_upgrades_and_is_safe_to_repeat(behind, capsys):
    assert migrate_tool.main([]) == 0
    assert version(behind) == CODE_SCHEMA_VERSION
    assert "applied 2" in capsys.readouterr().out
    assert migrate_tool.main([]) == 0
    assert "nothing to apply" in capsys.readouterr().out


def test_service_start_migrates(behind, settings):
    with TestClient(create_app(settings)) as client:
        assert version(behind) == CODE_SCHEMA_VERSION
        assert post(client, steps_payload({"2026-09-29": 5})).json()["status"] == "ok"


def test_running_service_refuses_a_db_whose_schema_is_not_its_own(client, db, settings):
    assert post(client, steps_payload({"2026-09-28": 1})).json()["status"] == "ok"
    db.execute(
        "INSERT INTO schema_version (version, applied_at_utc, name) "
        "VALUES (99, '2026-10-02T00:00:00Z', '099_from_newer_code')"
    )
    body = steps_payload({"2026-09-29": 2})

    push = post(client, body)
    page = client.get("/")

    assert push.status_code == 503
    assert push.json()["status"] == "schema_mismatch"
    assert "tools.migrate" in push.json()["error"]
    kept = settings.storage.raw_dir / "health" / push.json()["file"]
    assert kept.read_bytes() == body
    assert steps(db) == {"2026-09-28": 1}
    assert page.status_code == 503


def activity_rows(db_path: Path) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, type, duration_s, merged_from_json FROM activities ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [(r[0], r[1], r[2], [e["external_id"] for e in json.loads(r[3])]) for r in rows]


def test_tools_migrate_restores_activities_stored_under_the_old_canonical_rule(
    client, db, settings, tmp_path, capsys
):
    watch = workout("W-1", "07:01", 40, "Apple Watch")
    phone = workout("P-1", "07:00", 39, "Nike Run Club", hr=False, name="Run (NRC)")
    post(client, workouts_payload(phone, watch))
    good = activity_rows(settings.storage.db_path)
    assert good == [("W-1", "Running", 2400, ["W-1", "P-1"])]

    entries = json.loads(db.execute("SELECT merged_from_json FROM activities").fetchone()[0])
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute(
        "UPDATE activities SET id = 'P-1', type = 'Run (NRC)', duration_s = 2340, "
        "merged_from_json = ?",
        (json.dumps(list(reversed(entries)), sort_keys=True),),
    )
    db.execute("UPDATE activity_sources SET activity_id = 'P-1'")
    db.execute("UPDATE workout_hr_samples SET activity_id = 'P-1'")
    db.execute("PRAGMA foreign_keys=ON")
    assert replay.main(["--verify", "--scratch", str(tmp_path / "s1.db")]) == 1
    capsys.readouterr()

    assert migrate_tool.main([]) == 0
    assert "1 activity" in capsys.readouterr().out

    assert activity_rows(settings.storage.db_path) == good
    assert replay.main(["--verify", "--scratch", str(tmp_path / "s2.db")]) == 0


def test_migration_file_that_lands_after_start_is_not_applied_by_this_process(
    tmp_path, monkeypatch
):
    import app.db as db_module

    landed = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, landed)
    (landed / "099_arrived_by_git_pull.sql").write_text("CREATE TABLE arrived (x INTEGER);\n")
    monkeypatch.setattr(db_module, "MIGRATIONS_DIR", landed)

    conn = db_module.open_db(tmp_path / "running.db")
    try:
        assert db_module.stored_schema_version(conn) == db_module.CODE_SCHEMA_VERSION < 99
        assert "arrived" not in db_module.table_names(conn)
    finally:
        conn.close()
