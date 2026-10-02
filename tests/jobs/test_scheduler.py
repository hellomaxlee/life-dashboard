from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_MAX_INSTANCES
from fastapi.testclient import TestClient

from app.db import open_db
from app.jobs import scheduler as jobs
from app.jobs.scheduler import BACKUP_JOB, GOODREADS_JOB, USAGE_JOB, JobStats, UsageWatcher
from app.main import create_app
from tests.conftest import count, post_fixture
from tools import backup
from tools.replay import checksum, snapshot


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class CountingOpen:
    def __init__(self, settings) -> None:
        self.settings = settings
        self.conns: list[sqlite3.Connection] = []

    def __call__(self) -> sqlite3.Connection:
        conn = open_db(self.settings.storage.db_path)
        self.conns.append(conn)
        return conn


def write_usage(settings, used_pct: float, second: int) -> None:
    record = {
        "captured_at_utc": f"2026-10-01T14:00:{second:02d}Z",
        "rate_limits": {"seven_day": {"used_percentage": used_pct, "resets_at": 1759680000}},
    }
    settings.claude_usage.path.write_text(json.dumps(record))


def archived(settings) -> int:
    return len(list((settings.storage.raw_dir / "claude_usage").glob("*.json")))


def test_usage_job_reads_on_change_and_not_otherwise(db, jobs_settings):
    clock, opened = Clock(), CountingOpen(jobs_settings)
    watcher = UsageWatcher(jobs_settings, opened, clock)

    assert watcher.tick() == "missing"
    assert opened.conns == []

    write_usage(jobs_settings, 41.2, 1)
    assert watcher.tick() == "ok"
    assert len(opened.conns) == 1
    assert archived(jobs_settings) == 1

    for _ in range(3):
        clock.now += 10_000
        assert watcher.tick() == "unchanged"
    assert len(opened.conns) == 1
    assert archived(jobs_settings) == 1

    write_usage(jobs_settings, 55.0, 2)
    clock.now += 10_000
    assert watcher.tick() == "ok"
    assert len(opened.conns) == 2
    assert archived(jobs_settings) == 2
    metrics = json.loads(db.execute("SELECT metrics_json FROM daily_metrics").fetchone()[0])
    assert metrics["claude_week_used_pct"] == 55.0

    for conn in opened.conns:
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")
    assert opened.conns[0] is not opened.conns[1]


def test_usage_job_waits_out_the_minimum_read_interval(db, jobs_settings):
    clock, opened = Clock(), CountingOpen(jobs_settings)
    watcher = UsageWatcher(jobs_settings, opened, clock)
    gap = jobs_settings.scheduler.usage_min_read_seconds

    write_usage(jobs_settings, 41.2, 1)
    assert watcher.tick() == "ok"
    for second in range(2, 12):
        write_usage(jobs_settings, 41.2 + second, second)
        clock.now += (gap - 1) / 10
        assert watcher.tick() == "throttled"
    assert len(opened.conns) == 1
    assert archived(jobs_settings) == 1

    clock.now += 1
    assert watcher.tick() == "ok"
    assert archived(jobs_settings) == 2
    assert count(db, "raw_archive") == 2
    assert watcher.tick() == "unchanged"


def test_guarded_job_logs_and_counts_an_exception_without_raising(caplog):
    stats: dict[str, JobStats] = {}
    outcomes = iter([RuntimeError("disk full"), None])

    def flaky() -> None:
        outcome = next(outcomes)
        if outcome is not None:
            raise outcome

    run = jobs.guarded("flaky", flaky, stats)
    run()
    assert stats["flaky"] == JobStats(runs=1, failures=1, last_error="RuntimeError: disk full")
    assert "job flaky failed" in caplog.text
    run()
    assert stats["flaky"] == JobStats(runs=2, failures=1, last_error=None)


def test_a_failing_run_does_not_stop_later_runs_of_the_real_scheduler(jobs_settings, monkeypatch):
    finished = threading.Event()
    calls: list[int] = []

    def nightly(settings):
        calls.append(len(calls))
        if len(calls) == 1:
            raise OSError("backup volume not mounted")

    monkeypatch.setattr(backup, "nightly", nightly)
    scheduler = jobs.build_scheduler(jobs_settings, lambda: open_db(jobs_settings.storage.db_path))
    scheduler.add_listener(lambda event: finished.set(), EVENT_JOB_EXECUTED)
    scheduler.start()
    try:
        for expected in (1, 2):
            finished.clear()
            scheduler.get_job(BACKUP_JOB).modify(next_run_time=datetime.now(UTC))
            scheduler.wakeup()
            assert finished.wait(timeout=5)
            assert len(calls) == expected
        stats = scheduler.job_stats[BACKUP_JOB]
        assert (stats.runs, stats.failures) == (2, 1)
        assert scheduler.running
    finally:
        scheduler.shutdown(wait=True)


def test_a_job_never_overlaps_itself(jobs_settings, monkeypatch):
    started, release, skipped = threading.Event(), threading.Event(), threading.Event()
    calls: list[int] = []

    def slow_nightly(settings):
        calls.append(len(calls))
        started.set()
        release.wait(timeout=5)

    monkeypatch.setattr(backup, "nightly", slow_nightly)
    scheduler = jobs.build_scheduler(jobs_settings, lambda: open_db(jobs_settings.storage.db_path))
    scheduler.add_listener(lambda event: skipped.set(), EVENT_JOB_MAX_INSTANCES)
    scheduler.start()
    try:
        scheduler.get_job(BACKUP_JOB).modify(next_run_time=datetime.now(UTC))
        scheduler.wakeup()
        assert started.wait(timeout=5)
        scheduler.get_job(BACKUP_JOB).modify(next_run_time=datetime.now(UTC))
        scheduler.wakeup()
        assert skipped.wait(timeout=5)
        assert len(calls) == 1
    finally:
        release.set()
        scheduler.shutdown(wait=True)


def test_registered_jobs_and_their_guards(jobs_settings):
    scheduler = jobs.build_scheduler(jobs_settings, lambda: open_db(jobs_settings.storage.db_path))
    scheduler.start(paused=True)
    try:
        by_id = {job.id: job for job in scheduler.get_jobs()}
        assert set(by_id) == {USAGE_JOB, BACKUP_JOB}
        assert GOODREADS_JOB not in by_id
        assert all(job.max_instances == 1 and job.coalesce for job in by_id.values())
        assert by_id[USAGE_JOB].trigger.interval == timedelta(
            seconds=jobs_settings.scheduler.usage_poll_seconds
        )
        cron = by_id[BACKUP_JOB].trigger
        assert str(cron.timezone) == "America/New_York"
        fire = cron.get_next_fire_time(None, datetime(2026, 10, 2, 12, 0, tzinfo=UTC))
        assert fire == datetime(2026, 10, 3, 3, 15, tzinfo=ZoneInfo("America/New_York"))
    finally:
        scheduler.shutdown(wait=False)


def test_backup_job_writes_a_restorable_backup(client, db, jobs_settings, tmp_path):
    post_fixture(client, "workouts_v2_overlap.json")
    post_fixture(client, "metrics_v2_days.json")
    scheduler = jobs.build_scheduler(jobs_settings, lambda: open_db(jobs_settings.storage.db_path))

    scheduler.get_job(BACKUP_JOB).func()

    assert scheduler.job_stats[BACKUP_JOB] == JobStats(runs=1)
    [made] = backup.list_backups(jobs_settings.backup.dir)
    restored = backup.restore(made, tmp_path / "restored")
    assert backup.data_checksum(restored) == checksum(snapshot(db))
    assert backup.verify(made, jobs_settings) == []


def test_overdue_backup_is_caught_up_soon_after_start(client, jobs_settings):
    now = datetime(2026, 10, 2, 16, 0, tzinfo=UTC)
    assert jobs.backup_overdue(jobs_settings, now)
    late = jobs.build_scheduler(jobs_settings, lambda: None, now)
    late.start(paused=True)
    assert late.get_job(BACKUP_JOB).next_run_time == now + jobs.BACKUP_CATCHUP_DELAY
    late.shutdown(wait=False)

    backup.nightly(jobs_settings, now - timedelta(hours=9))
    assert not jobs.backup_overdue(jobs_settings, now)
    assert jobs.backup_overdue(jobs_settings, now + timedelta(days=2))
    fresh = jobs.build_scheduler(jobs_settings, lambda: None, now)
    fresh.start(paused=True)
    assert fresh.get_job(BACKUP_JOB).next_run_time.astimezone(UTC) > now + timedelta(hours=1)
    fresh.shutdown(wait=False)


def test_scheduler_is_off_unless_settings_enable_it(client, jobs_settings):
    assert jobs_settings.scheduler.enabled is False
    assert client.app.state.scheduler is None

    enabled = replace(jobs_settings, scheduler=replace(jobs_settings.scheduler, enabled=True))
    app = create_app(enabled)
    with TestClient(app):
        running = app.state.scheduler
        assert running.running
        assert {job.id for job in running.get_jobs()} == {USAGE_JOB, BACKUP_JOB}
    assert not running.running
    assert app.state.scheduler is None


def test_config_file_ships_with_the_scheduler_on(monkeypatch):
    from app.config import load_settings

    monkeypatch.delenv("LIFE_SCHEDULER_ENABLED")
    shipped = load_settings()
    assert shipped.scheduler.enabled is True
    assert shipped.scheduler.usage_min_read_seconds >= shipped.scheduler.usage_poll_seconds
    assert jobs.parse_hh_mm(shipped.backup.time) == (3, 15)
    assert jobs.parse_hh_mm(shipped.pull.goodreads) == (6, 30)
