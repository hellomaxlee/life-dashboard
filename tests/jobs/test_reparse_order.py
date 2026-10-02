"""Re-parsing an unparsed duplicate must never put older data over a later push, and an
ingest must hold the write lock from its first read."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.ingest import claude_usage, dedupe, health
from tests.conftest import fixture_bytes
from tools import replay
from tools.replay import checksum, snapshot

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def post(client, body: bytes):
    return client.post("/ingest/health", content=body, headers={"content-type": "application/json"})


def steps(db) -> list[int]:
    return [r["steps"] for r in db.execute("SELECT steps FROM steps_daily WHERE steps > 8000")]


def tick_clock(monkeypatch) -> None:
    moments = (T0 + timedelta(minutes=n) for n in range(100))
    monkeypatch.setattr(health, "now_utc", lambda: next(moments))


def test_stale_failed_push_does_not_overwrite_a_later_one(
    client, db, settings, tmp_path, monkeypatch
):
    push_a = fixture_bytes("metrics_v2_days.json")
    push_b = push_a.replace(b"8421", b"13421")
    assert push_a != push_b
    tick_clock(monkeypatch)
    real_store, calls = health.store_payload, []

    def fails_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_store(*args, **kwargs)

    monkeypatch.setattr(health, "store_payload", fails_once)

    assert post(client, push_a).status_code == 500
    assert post(client, push_b).json()["status"] == "ok"
    again = post(client, push_a)

    assert again.status_code == 200
    assert again.json()["status"] == "superseded"
    assert 13421 in steps(db) and 8421 not in steps(db)
    flags = db.execute("SELECT parsed_ok FROM raw_archive ORDER BY id").fetchall()
    assert [r["parsed_ok"] for r in flags] == [0, 1]
    scratch = replay.replay(settings.storage.raw_dir, tmp_path / "scratch.db", settings)
    try:
        assert checksum(snapshot(scratch)) == checksum(snapshot(db))
    finally:
        scratch.close()


def test_failed_push_with_nothing_after_it_is_still_reparsed(client, db, monkeypatch):
    push_a = fixture_bytes("metrics_v2_days.json")
    tick_clock(monkeypatch)
    real_store, calls = health.store_payload, []

    def fails_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_store(*args, **kwargs)

    monkeypatch.setattr(health, "store_payload", fails_once)
    assert post(client, push_a).status_code == 500
    assert post(client, push_a).json()["status"] == "ok"
    assert 8421 in steps(db)


def usage_bytes(used_pct: float, second: int) -> bytes:
    record = {
        "captured_at_utc": f"2026-10-01T14:00:{second:02d}Z",
        "rate_limits": {"seven_day": {"used_percentage": used_pct, "resets_at": 1759680000}},
    }
    return json.dumps(record).encode()


def test_stale_unparsed_usage_file_is_superseded_by_a_later_reading(db, jobs_settings):
    old, new = usage_bytes(10.0, 1), usage_bytes(20.0, 2)
    health.archive_raw(
        db, jobs_settings.storage.raw_dir, old, source=claude_usage.SOURCE, received_at=T0
    )
    jobs_settings.claude_usage.path.write_bytes(new)
    assert claude_usage.read_usage_file(db, jobs_settings).status == "ok"

    jobs_settings.claude_usage.path.write_bytes(old)
    result = claude_usage.read_usage_file(db, jobs_settings)

    assert result.status == "superseded"
    metrics = json.loads(db.execute("SELECT metrics_json FROM daily_metrics").fetchone()[0])
    assert metrics["claude_week_used_pct"] == 20.0


class OtherWriter:
    """A second connection that tries to commit a write at a chosen moment, without waiting."""

    def __init__(self, db_path) -> None:
        self.db_path = db_path
        self.outcomes: list[str] = []

    def write(self) -> None:
        conn = sqlite3.connect(self.db_path, timeout=0, isolation_level=None)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO steps_daily (day_local, steps, source) "
                "VALUES ('2000-01-01', 1, 'other-writer')"
            )
            self.outcomes.append("committed")
        except sqlite3.OperationalError:
            self.outcomes.append("blocked")
        finally:
            conn.close()


def test_health_ingest_holds_the_write_lock_from_its_first_read(client, settings, monkeypatch):
    other = OtherWriter(settings.storage.db_path)
    real_find = dedupe.find_matching_activity

    def find_after_other_writer(*args, **kwargs):
        if not other.outcomes:
            other.write()
        return real_find(*args, **kwargs)

    monkeypatch.setattr(dedupe, "find_matching_activity", find_after_other_writer)

    resp = post(client, fixture_bytes("workouts_v2_run.json"))

    assert resp.json().get("error") is None
    assert resp.json()["status"] == "ok"
    assert other.outcomes == ["blocked"]


def test_usage_ingest_holds_the_write_lock_from_its_first_read(db, jobs_settings, monkeypatch):
    other = OtherWriter(jobs_settings.storage.db_path)

    def dumps_after_other_writer(*args, **kwargs):
        if not other.outcomes:
            other.write()
        return json.dumps(*args, **kwargs)

    monkeypatch.setattr(
        claude_usage, "json", SimpleNamespace(loads=json.loads, dumps=dumps_after_other_writer)
    )
    jobs_settings.claude_usage.path.write_bytes(usage_bytes(41.2, 1))

    result = claude_usage.read_usage_file(db, jobs_settings)

    assert result.status == "ok"
    assert other.outcomes == ["blocked"]


def test_raw_write_is_flushed_and_fsynced_before_the_row_exists(db, settings, monkeypatch):
    body = fixture_bytes("workouts_v2_run.json")
    synced: list[tuple[int, int]] = []
    real_fsync = os.fsync

    def recording_fsync(fd: int) -> None:
        rows = db.execute("SELECT COUNT(*) FROM raw_archive").fetchone()[0]
        synced.append((os.fstat(fd).st_size, rows))
        real_fsync(fd)

    monkeypatch.setattr(health.os, "fsync", recording_fsync)
    archived = health.archive_raw(db, settings.storage.raw_dir, body)

    assert synced == [(len(body), 0)]
    assert archived.path.read_bytes() == body
