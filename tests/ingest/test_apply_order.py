"""The truth is "payloads applied in raw_archive.id order". A payload whose first parse
never finished is applied when it is re-posted, and every later payload of its source is
then applied again on top, so old data fills gaps and newer data still wins."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

from fastapi.testclient import TestClient

from app.config import StorageConfig
from app.db import open_db
from app.ingest import claude_usage, health
from app.main import create_app
from tests.conftest import count, fixture_bytes
from tests.payloads import T0, fail_next_stores, post, steps, steps_payload, tick_clock
from tools import backup, replay
from tools.replay import checksum, snapshot


def replay_checksum(db, settings, tmp_path) -> str:
    scratch = replay.replay(settings.storage.raw_dir, tmp_path / "scratch.db", settings, None, db)
    try:
        return checksum(snapshot(scratch))
    finally:
        scratch.close()


def flags(db) -> list[int]:
    return [r["parsed_ok"] for r in db.execute("SELECT parsed_ok FROM raw_archive ORDER BY id")]


def test_workouts_push_stranded_behind_a_metrics_push_is_recovered(
    client, db, settings, tmp_path, monkeypatch
):
    workouts, metrics = fixture_bytes("workouts_v2_run.json"), fixture_bytes("metrics_v2_days.json")
    tick_clock(monkeypatch)
    fail_next_stores(monkeypatch)
    assert post(client, workouts).status_code == 500
    assert post(client, metrics).json()["status"] == "ok"

    retry = post(client, workouts)

    assert retry.status_code == 200
    assert retry.json()["status"] == "ok"
    assert retry.json()["reapplied"] == 1
    assert count(db, "activities") == 1
    assert flags(db) == [1, 1]
    assert post(client, workouts).json()["status"] == "duplicate"
    assert replay_checksum(db, settings, tmp_path) == checksum(snapshot(db))


def test_older_payload_recovered_later_never_beats_a_newer_one(
    client, db, settings, tmp_path, monkeypatch
):
    push_a = steps_payload({"2026-09-28": 1000, "2026-09-29": 2000})
    push_b = steps_payload({"2026-09-29": 2500, "2026-09-30": 3000})
    tick_clock(monkeypatch)
    fail_next_stores(monkeypatch)
    assert post(client, push_a).status_code == 500
    assert post(client, push_b).json()["status"] == "ok"

    retry = post(client, push_a)

    assert retry.json()["status"] == "ok"
    assert steps(db) == {"2026-09-28": 1000, "2026-09-29": 2500, "2026-09-30": 3000}
    assert replay_checksum(db, settings, tmp_path) == checksum(snapshot(db))


def test_two_payloads_recovered_in_reverse_order(client, db, settings, tmp_path, monkeypatch):
    push_a = steps_payload({"2026-09-27": 700, "2026-09-28": 1000})
    push_b = steps_payload({"2026-09-28": 1500, "2026-09-29": 2000})
    push_c = steps_payload({"2026-09-29": 2500, "2026-09-30": 3000})
    tick_clock(monkeypatch)
    fail_next_stores(monkeypatch, failures=2)
    assert post(client, push_a).status_code == 500
    assert post(client, push_b).status_code == 500
    assert post(client, push_c).json()["status"] == "ok"

    second = post(client, push_b)
    first = post(client, push_a)

    assert (second.json()["status"], second.json()["reapplied"]) == ("ok", 1)
    assert (first.json()["status"], first.json()["reapplied"]) == ("ok", 2)
    assert steps(db) == {
        "2026-09-27": 700,
        "2026-09-28": 1500,
        "2026-09-29": 2500,
        "2026-09-30": 3000,
    }
    assert flags(db) == [1, 1, 1]
    assert replay_checksum(db, settings, tmp_path) == checksum(snapshot(db))


def test_recovery_with_a_later_raw_file_missing_applies_nothing(client, db, settings, monkeypatch):
    push_a = steps_payload({"2026-09-28": 1000, "2026-09-29": 2000})
    push_b = steps_payload({"2026-09-29": 2500})
    tick_clock(monkeypatch)
    fail_next_stores(monkeypatch)
    post(client, push_a)
    later = post(client, push_b).json()
    (settings.storage.raw_dir / "health" / later["file"]).unlink()

    retry = post(client, push_a)

    assert retry.status_code == 500
    assert "file missing" in retry.json()["error"]
    assert later["file"] in retry.json()["error"]
    assert steps(db) == {"2026-09-29": 2500}
    row = db.execute("SELECT parsed_ok, error FROM raw_archive WHERE id = 1").fetchone()
    assert row["parsed_ok"] == 0 and "file missing" in row["error"]


def usage_bytes(used_pct: float, second: int) -> bytes:
    record = {
        "captured_at_utc": f"2026-10-01T14:00:{second:02d}Z",
        "rate_limits": {"seven_day": {"used_percentage": used_pct, "resets_at": 1759680000}},
    }
    return json.dumps(record).encode()


def test_unparsed_usage_file_read_after_a_newer_one_is_applied_in_order(db, settings, tmp_path):
    usage = replace(settings.claude_usage, path=tmp_path / "claude_usage.json")
    jobs_settings = replace(settings, claude_usage=usage)
    old, new = usage_bytes(10.0, 1), usage_bytes(20.0, 2)
    raw_dir = jobs_settings.storage.raw_dir
    health.archive_raw(db, raw_dir, old, source=claude_usage.SOURCE, received_at=T0)
    jobs_settings.claude_usage.path.write_bytes(new)
    assert claude_usage.read_usage_file(db, jobs_settings).status == "ok"

    jobs_settings.claude_usage.path.write_bytes(old)
    result = claude_usage.read_usage_file(db, jobs_settings)

    assert result.status == "ok"
    assert flags(db) == [1, 1]
    metrics = json.loads(db.execute("SELECT metrics_json FROM daily_metrics").fetchone()[0])
    assert metrics["claude_week_used_pct"] == 20.0


def test_repost_of_an_unparsed_payload_restores_its_deleted_raw_file(client, db, settings):
    body = fixture_bytes("workouts_v2_run.json")
    archived = health.archive_raw(db, settings.storage.raw_dir, body)
    archived.path.unlink()

    assert post(client, body).json()["status"] == "ok"

    files = list((settings.storage.raw_dir / "health").glob("*.json"))
    assert [f.read_bytes() for f in files] == [body]


def test_restored_copy_never_writes_to_the_original_raw_dir(client, db, settings, tmp_path):
    body = fixture_bytes("workouts_v2_run.json")
    post(client, fixture_bytes("metrics_v2_days.json"))
    health.archive_raw(db, settings.storage.raw_dir, body)
    made = backup.create_backup(settings, tmp_path / "b1")
    restored_dir = tmp_path / "moved"
    backup.restore(made.path, restored_dir)
    original_raw = settings.storage.raw_dir
    original_names = sorted(p.name for p in (original_raw / "health").iterdir())
    for path in (original_raw / "health").iterdir():
        path.unlink()
    unparsed = db.execute("SELECT path FROM raw_archive WHERE parsed_ok = 0").fetchone()["path"]
    name = unparsed.rsplit("/", 1)[-1]
    (restored_dir / "raw" / "health" / name).unlink()

    moved = replace(settings, storage=StorageConfig(restored_dir / "life.db", restored_dir / "raw"))
    with TestClient(create_app(moved)) as moved_client:
        assert post(moved_client, body).json()["status"] == "ok"

    assert list((original_raw / "health").iterdir()) == []
    assert (restored_dir / "raw" / "health" / name).read_bytes() == body
    assert name in original_names


def test_same_body_racing_itself_is_a_duplicate_not_a_crash(client, db, settings, monkeypatch):
    body = steps_payload({"2026-09-29": 1})
    real_write, nested = health._write_durably, []

    def write_then_get_overtaken(target, data):
        real_write(target, data)
        if not nested:
            nested.append("started")
            other = open_db(settings.storage.db_path)
            try:
                overtaking = health.archive_raw(other, settings.storage.raw_dir, body)
                health.ingest_archived(other, body, overtaking.raw_archive_id, settings)
                nested[0] = "ok"
            finally:
                other.close()

    monkeypatch.setattr(health, "_write_durably", write_then_get_overtaken)

    first = post(client, body)

    assert nested == ["ok"]
    assert (first.status_code, first.json()["status"]) == (200, "duplicate")
    assert count(db, "raw_archive") == 1
    assert len(list((settings.storage.raw_dir / "health").glob("*.json"))) == 1


def test_push_while_the_db_is_locked_keeps_the_file_and_says_so(client, db, settings, monkeypatch):
    body = steps_payload({"2026-09-29": 2})
    tick_clock(monkeypatch)
    real_open = client.app.state.open_conn

    def impatient_open() -> sqlite3.Connection:
        conn = real_open()
        conn.execute("PRAGMA busy_timeout=50")
        return conn

    client.app.state.open_conn = impatient_open
    blocker = sqlite3.connect(settings.storage.db_path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        locked = post(client, body)
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()

    assert locked.status_code == 503
    assert locked.json()["status"] == "busy"
    kept = settings.storage.raw_dir / "health" / locked.json()["file"]
    assert kept.read_bytes() == body
    assert count(db, "raw_archive") == 0

    retry = post(client, body)

    assert retry.json()["status"] == "ok"
    assert retry.json()["file"] == kept.name
    assert len(list((settings.storage.raw_dir / "health").glob("*.json"))) == 1
    assert steps(db) == {"2026-09-29": 2}
