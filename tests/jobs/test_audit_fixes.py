"""Audit gates for config validation, the usage reader and hook, backup catch-up,
bounded shutdown, and the SQLite pragmas the service relies on."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_MISSED
from fastapi.testclient import TestClient

from app.config import DEFAULT_CONFIG_PATH, load_settings
from app.db import connect, open_db
from app.ingest import claude_usage
from app.jobs import scheduler as jobs
from app.jobs.scheduler import BACKUP_JOB, UsageWatcher
from app.main import create_app
from tests.conftest import count
from tests.payloads import post, steps_payload
from tools import backup, claude_usage_hook

BAD_CONFIG = [
    (r"^usage_poll_seconds = 30", "usage_poll_seconds = -5", "scheduler.usage_poll_seconds"),
    (r"^usage_poll_seconds = 30", "usage_poll_seconds = 0", "scheduler.usage_poll_seconds"),
    (r"^usage_min_read_seconds = 900", "usage_min_read_seconds = -1", "usage_min_read_seconds"),
    (r"^enabled = true", 'enabled = "false"', "scheduler.enabled"),
    (r'^dir = "data/backups"', 'dir = ""', "backup.dir"),
    (r"^keep = 14", "keep = 0", "backup.keep"),
    (r"^keep = 14", "keep = -3", "backup.keep"),
    (r"^keep = 14", "keep = 2.9", "backup.keep"),
    (r'^time = "03:15"', 'time = "3h15"', "backup.time"),
    (r"^hr_incomplete_ratio = 0.25", "hr_incomplete_ratio = -1", "ingest.hr_incomplete_ratio"),
    (r"^sleep_gap_min = 60", "sleep_gap_min = -1", "ingest.sleep_gap_min"),
    (r"^start_window_min = 5", "start_window_min = -5", "ingest.dedupe.start_window_min"),
    (r'^home_tz = "America/New_York"', 'home_tz = "Mars/Olympus"', "home_tz"),
]


@pytest.mark.parametrize(("pattern", "replacement", "key"), BAD_CONFIG)
def test_bad_config_value_stops_boot_and_names_the_key(
    tmp_path, monkeypatch, pattern, replacement, key
):
    text, n = re.subn(pattern, replacement, DEFAULT_CONFIG_PATH.read_text(), count=1, flags=re.M)
    assert n == 1
    config = tmp_path / "config.toml"
    config.write_text(text)
    monkeypatch.delenv("LIFE_BACKUP_DIR", raising=False)
    monkeypatch.setenv("LIFE_SCHEDULER_ENABLED", "0")

    with pytest.raises(ValueError, match=re.escape(key)):
        load_settings(config)


def test_shipped_config_passes_its_own_checks(monkeypatch):
    monkeypatch.delenv("LIFE_BACKUP_DIR", raising=False)
    shipped = load_settings(DEFAULT_CONFIG_PATH)
    assert shipped.backup.keep == 14 and shipped.scheduler.usage_poll_seconds == 30


def test_scheduler_refuses_a_poll_interval_that_would_spin(jobs_settings):
    bad = replace(jobs_settings.scheduler, usage_poll_seconds=-5)
    spinning = replace(jobs_settings, scheduler=bad)
    with pytest.raises(ValueError, match="usage_poll_seconds"):
        jobs.build_scheduler(spinning, lambda: open_db(spinning.storage.db_path))


def write_usage(settings, text: str) -> None:
    settings.claude_usage.path.write_text(text)


def usage_row(db) -> sqlite3.Row:
    return db.execute("SELECT parsed_ok, error FROM raw_archive ORDER BY id DESC").fetchone()


@pytest.mark.parametrize(
    "text",
    [
        "[" * 50_000,
        '{"captured_at_utc": "2026-10-01T14:00:01Z", "rate_limits": {"seven_day": '
        '{"used_percentage": 41, "resets_at": 1e30}}}',
        '{"captured_at_utc": "2026-10-01T14:00:01Z", "rate_limits": {"seven_day": '
        '{"used_percentage": NaN, "resets_at": 1759680000}}}',
        '{"captured_at_utc": "2026-10-01T14:00:01Z", "rate_limits": {"seven_day": '
        '{"used_percentage": 1e999, "resets_at": 1759680000}}}',
    ],
    ids=["deeply-nested", "resets-at-overflow", "nan", "infinity"],
)
def test_poisoned_usage_file_is_malformed_with_the_error_recorded(db, jobs_settings, text):
    watcher = UsageWatcher(jobs_settings, lambda: open_db(jobs_settings.storage.db_path))
    write_usage(jobs_settings, text)

    assert watcher.tick() == "malformed"
    assert watcher.tick() == "unchanged"
    row = usage_row(db)
    assert row["parsed_ok"] == 0 and row["error"]
    assert count(db, "daily_metrics") == 0


def test_oversized_usage_file_is_rejected_without_being_archived(db, jobs_settings, caplog):
    write_usage(jobs_settings, '{"pad": "' + "x" * 200_000 + '"}')

    result = claude_usage.read_usage_file(db, jobs_settings)

    assert result.status == "malformed"
    assert count(db, "raw_archive") == 0
    assert not (jobs_settings.storage.raw_dir / "claude_usage").exists()
    assert "larger than" in caplog.text


FEED = {"rate_limits": {"seven_day": {"used_percentage": 41, "resets_at": 1791200000}}}


def run_hook(out: Path, stdin: bytes, *extra: str) -> subprocess.CompletedProcess:
    hook = Path(claude_usage_hook.__file__)
    return subprocess.run(
        [sys.executable, str(hook), "--out", str(out), *extra], input=stdin, capture_output=True
    )


def test_hook_refuses_nan_and_keeps_the_last_reading(tmp_path):
    out = tmp_path / "u.json"
    assert run_hook(out, json.dumps(FEED).encode()).returncode == 0
    good = out.read_bytes()

    run = run_hook(out, b'{"rate_limits": {"seven_day": {"used_percentage": NaN}}}')

    assert (run.returncode, run.stdout, run.stderr) == (0, b"", b"")
    assert out.read_bytes() == good


def test_hook_is_silent_and_exits_zero_whatever_it_is_given(tmp_path):
    out = tmp_path / "u.json"
    cases = [(b"", ()), (os.urandom(2000), ()), (b"[" * 100_000, ()), (b"{}", ("--bogus",))]
    for stdin, extra in cases:
        run = run_hook(out, stdin, *extra)
        assert (run.returncode, run.stdout, run.stderr) == (0, b"", b"")
    assert not out.exists()


def test_hook_leaves_no_tmp_file_when_the_rename_fails(tmp_path):
    target = tmp_path / "u.json"
    target.mkdir()

    run = run_hook(target, json.dumps(FEED).encode())

    assert run.returncode == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["u.json"]


def test_hook_replaces_the_file_in_one_step(tmp_path, monkeypatch):
    target = tmp_path / "u.json"
    old = {"captured_at_utc": "2026-10-01T14:00:01Z", **FEED}
    target.write_text(json.dumps(old))
    seen: list[tuple[dict, dict]] = []
    real_replace = os.replace

    def watching_replace(src, dst):
        seen.append((json.loads(Path(dst).read_text()), json.loads(Path(src).read_text())))
        real_replace(src, dst)

    monkeypatch.setattr(claude_usage_hook.os, "replace", watching_replace)
    new = {"captured_at_utc": "2026-10-01T14:00:02Z", **FEED}

    claude_usage_hook.write_atomic(target, new)

    assert seen == [(old, new)]
    assert json.loads(target.read_text()) == new
    assert sorted(p.name for p in tmp_path.iterdir()) == ["u.json"]


def test_overdue_check_is_an_hourly_job_that_backs_up_only_when_overdue(client, db, jobs_settings):
    scheduler = jobs.build_scheduler(jobs_settings, lambda: open_db(jobs_settings.storage.db_path))
    check = scheduler.get_job("backup_overdue_check")
    assert check.trigger.interval == timedelta(hours=1)

    now = datetime(2026, 10, 2, 16, 0, tzinfo=UTC)
    assert jobs.backup_if_overdue(jobs_settings, now) is True
    assert len(backup.list_backups(jobs_settings.backup.dir)) == 1
    assert jobs.backup_if_overdue(jobs_settings, now + timedelta(hours=25)) is False
    assert jobs.backup_if_overdue(jobs_settings, now + timedelta(hours=27)) is True
    assert len(backup.list_backups(jobs_settings.backup.dir)) == 2


def test_backup_missed_while_asleep_runs_on_wake(jobs_settings, monkeypatch):
    outcome = threading.Event()
    events: list[int] = []
    monkeypatch.setattr(backup, "nightly", lambda settings: SimpleNamespace(raw_missing=[]))
    scheduler = jobs.build_scheduler(jobs_settings, lambda: open_db(jobs_settings.storage.db_path))

    def note(event) -> None:
        if event.job_id == BACKUP_JOB:
            events.append(event.code)
            outcome.set()

    scheduler.add_listener(note, EVENT_JOB_EXECUTED | EVENT_JOB_MISSED)
    scheduler.start()
    try:
        five_hours_ago = datetime.now(UTC) - timedelta(hours=5)
        scheduler.get_job(BACKUP_JOB).modify(next_run_time=five_hours_ago)
        scheduler.wakeup()
        assert outcome.wait(timeout=5)
        assert events == [EVENT_JOB_EXECUTED]
    finally:
        scheduler.shutdown(wait=True)


def enabled(settings):
    return replace(settings, scheduler=replace(settings.scheduler, enabled=True))


def test_in_flight_job_finishes_before_the_lifespan_exits(jobs_settings, monkeypatch):
    started, finished = threading.Event(), threading.Event()

    def slow_nightly(settings):
        started.set()
        time.sleep(0.3)
        finished.set()
        return SimpleNamespace(raw_missing=[])

    monkeypatch.setattr(backup, "nightly", slow_nightly)
    app = create_app(enabled(jobs_settings))
    with TestClient(app):
        app.state.scheduler.get_job(BACKUP_JOB).modify(next_run_time=datetime.now(UTC))
        app.state.scheduler.wakeup()
        assert started.wait(timeout=5)
    assert finished.is_set()


def test_hung_job_does_not_block_shutdown_forever(jobs_settings, monkeypatch, caplog):
    started, release = threading.Event(), threading.Event()

    def hung_nightly(settings):
        started.set()
        release.wait(timeout=30)
        return SimpleNamespace(raw_missing=[])

    monkeypatch.setattr(backup, "nightly", hung_nightly)
    monkeypatch.setattr(jobs, "SHUTDOWN_WAIT_S", 0.3, raising=False)
    app = create_app(enabled(jobs_settings))
    done = threading.Event()

    def run_lifespan() -> None:
        with TestClient(app):
            app.state.scheduler.get_job(BACKUP_JOB).modify(next_run_time=datetime.now(UTC))
            app.state.scheduler.wakeup()
            started.wait(timeout=5)
        done.set()

    thread = threading.Thread(target=run_lifespan, daemon=True)
    thread.start()
    try:
        assert done.wait(timeout=5), "lifespan exit is still waiting on the hung job"
        assert "still running" in caplog.text
    finally:
        release.set()
        thread.join(timeout=5)


def test_connection_pragmas_the_service_relies_on(tmp_path):
    conn = connect(tmp_path / "p.db")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_busy_timeout_comes_from_the_pragma_not_a_library_default(tmp_path, monkeypatch):
    import app.db as db_module

    executed: list[str] = []
    real_connect = sqlite3.connect

    class Recording(sqlite3.Connection):
        def execute(self, sql, *args):
            executed.append(sql)
            return super().execute(sql, *args)

    monkeypatch.setattr(
        db_module.sqlite3, "connect", lambda *a, **k: real_connect(*a, factory=Recording, **k)
    )
    conn = connect(tmp_path / "p.db")
    conn.close()
    assert "PRAGMA busy_timeout=5000" in executed
    assert executed.index("PRAGMA busy_timeout=5000") == 0


def test_a_write_under_a_brief_lock_waits_and_succeeds(client, db, settings):
    blocker = sqlite3.connect(
        settings.storage.db_path, isolation_level=None, check_same_thread=False
    )
    blocker.execute("BEGIN IMMEDIATE")
    threading.Timer(0.3, lambda: blocker.execute("ROLLBACK")).start()

    resp = post(client, steps_payload({"2026-09-29": 5}))

    assert (resp.status_code, resp.json()["status"]) == (200, "ok")
    blocker.close()
