from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_MAX_INSTANCES
from fastapi.testclient import TestClient

from app.db import open_db
from app.jobs import scheduler as jobs
from app.jobs.scheduler import BACKUP_JOB, USAGE_JOB, JobStats, UsageWatcher
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
        return SimpleNamespace(raw_missing=[])

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
        return SimpleNamespace(raw_missing=[])

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
        assert set(by_id) == jobs.CORE_JOBS
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
    check = late.get_job(jobs.BACKUP_CHECK_JOB)
    assert check.next_run_time == now + jobs.BACKUP_CATCHUP_DELAY
    late.shutdown(wait=False)

    backup.nightly(jobs_settings, now - timedelta(hours=9))
    assert not jobs.backup_overdue(jobs_settings, now)
    assert jobs.backup_overdue(jobs_settings, now + timedelta(days=2))
    fresh = jobs.build_scheduler(jobs_settings, lambda: None, now)
    fresh.start(paused=True)
    assert fresh.get_job(BACKUP_JOB).next_run_time.astimezone(UTC) > now + timedelta(hours=1)
    fresh.shutdown(wait=False)


def test_summary_is_caught_up_after_a_start_past_its_time(db, jobs_settings, monkeypatch):
    opener = lambda: open_db(jobs_settings.storage.db_path)  # noqa: E731
    tz = ZoneInfo(jobs_settings.home_tz)
    early = datetime(2026, 10, 2, 6, 0, tzinfo=tz)
    within_grace = datetime(2026, 10, 2, 9, 0, tzinfo=tz)
    beyond_grace = datetime(2026, 10, 2, 21, 30, tzinfo=tz)

    def first_run(now: datetime) -> datetime | None:
        """The catch-up run build_scheduler asked for, or None when it left it to the cron."""
        scheduler = jobs.build_scheduler(jobs_settings, opener, now)
        scheduler.start(paused=True)
        try:
            due = scheduler.get_job(jobs.SUMMARY_JOB).next_run_time
            return None if (due.hour, due.minute) == (6, 50) else due
        finally:
            scheduler.shutdown(wait=False)

    assert not jobs.summary_overdue(jobs_settings, opener, early)
    assert first_run(early) is None
    for late in (within_grace, beyond_grace):
        assert jobs.summary_overdue(jobs_settings, opener, late)
        assert first_run(late) == late + jobs.SUMMARY_CATCHUP_DELAY

    monkeypatch.setattr(jobs, "now_utc", lambda: within_grace.astimezone(UTC))
    assert jobs.run_daily_summary(jobs_settings, opener) == "fallback"
    assert db.execute("SELECT COUNT(*) FROM summary_lines").fetchone()[0] == 1
    for late in (within_grace, beyond_grace):
        assert not jobs.summary_overdue(jobs_settings, opener, late)
        assert first_run(late) is None
    assert jobs.run_daily_summary(jobs_settings, opener) == "stored"
    assert jobs.summary_overdue(jobs_settings, opener, within_grace + timedelta(days=1))


def test_summary_catch_up_respects_the_monthly_cap(db, jobs_settings, monkeypatch):
    from app.summary import run as summary_run

    class NeverCalled:
        class messages:  # noqa: N801
            @staticmethod
            def create(**kwargs):
                raise AssertionError("the model was called over the cap")

    capped = replace(
        jobs_settings,
        anthropic_api_key="test-key-never-sent",
        summary=replace(jobs_settings.summary, monthly_cap_usd=0.0),
    )
    monkeypatch.setattr(summary_run, "make_client", lambda settings: NeverCalled())
    now = datetime(2026, 10, 2, 9, 0, tzinfo=ZoneInfo(capped.home_tz))
    monkeypatch.setattr(jobs, "now_utc", lambda: now.astimezone(UTC))
    scheduler = jobs.build_scheduler(capped, lambda: open_db(capped.storage.db_path), now)
    assert scheduler.get_job(jobs.SUMMARY_JOB).next_run_time == now + jobs.SUMMARY_CATCHUP_DELAY

    scheduler.get_job(jobs.SUMMARY_JOB).func()

    assert scheduler.job_stats[jobs.SUMMARY_JOB] == JobStats(runs=1)
    line = db.execute("SELECT source, attempts_json FROM summary_lines").fetchone()
    assert line["source"] == "fallback"
    assert json.loads(line["attempts_json"])[0]["result"].startswith("cap:")
    assert db.execute("SELECT COUNT(*) FROM model_spend").fetchone()[0] == 0


def test_month_feature_job_is_registered_daily_at_twenty_past_midnight(jobs_settings):
    opener = lambda: open_db(jobs_settings.storage.db_path)  # noqa: E731
    scheduler = jobs.build_scheduler(jobs_settings, opener)
    scheduler.start(paused=True)
    try:
        job = scheduler.get_job(jobs.MONTH_JOB)
        assert jobs.MONTH_JOB in jobs.CORE_JOBS
        assert job.max_instances == 1 and job.coalesce
        assert job.misfire_grace_time == jobs.MONTH_MISFIRE_GRACE_S == 20 * 3600
        assert str(job.trigger.timezone) == "America/New_York"
        fire = job.trigger.get_next_fire_time(None, datetime(2026, 10, 31, 12, 0, tzinfo=UTC))
        assert fire == datetime(2026, 11, 1, 0, 20, tzinfo=ZoneInfo("America/New_York"))
    finally:
        scheduler.shutdown(wait=False)


def test_month_feature_catch_up_runs_once_after_a_start_without_one(db, jobs_settings, monkeypatch):
    from app.month import generate
    from tests.month.conftest import FakeClient, feature_text, reply

    opener = lambda: open_db(jobs_settings.storage.db_path)  # noqa: E731
    tz = ZoneInfo(jobs_settings.home_tz)
    now = datetime(2026, 10, 4, 15, 0, tzinfo=tz)
    fake = FakeClient(reply(feature_text("2026-10")))
    monkeypatch.setattr(generate, "make_client", lambda settings: fake)
    monkeypatch.setattr(jobs, "now_utc", lambda: now.astimezone(UTC))

    def first_run(moment: datetime) -> datetime | None:
        """The catch-up run build_scheduler asked for, or None when it left it to the cron."""
        scheduler = jobs.build_scheduler(jobs_settings, opener, moment)
        scheduler.start(paused=True)
        try:
            due = scheduler.get_job(jobs.MONTH_JOB).next_run_time
            return None if (due.hour, due.minute) == jobs.MONTH_TIME else due
        finally:
            scheduler.shutdown(wait=False)

    assert jobs.month_feature_missing(jobs_settings, opener, now)
    assert first_run(now) == now + jobs.MONTH_CATCHUP_DELAY

    scheduler = jobs.build_scheduler(jobs_settings, opener, now)
    scheduler.get_job(jobs.MONTH_JOB).func()
    scheduler.get_job(jobs.MONTH_JOB).func()

    assert scheduler.job_stats[jobs.MONTH_JOB] == JobStats(runs=2)
    assert len(fake.requests) == 1
    assert db.execute("SELECT month_local FROM month_features").fetchone()[0] == "2026-10"
    assert not jobs.month_feature_missing(jobs_settings, opener, now)
    assert first_run(now) is None
    assert jobs.run_month_feature(jobs_settings, opener) == "stored"
    assert jobs.month_feature_missing(jobs_settings, opener, now + timedelta(days=28))


def test_month_feature_job_never_calls_over_the_cap_or_twice_a_day(db, jobs_settings, monkeypatch):
    from app.month import generate
    from tests.month.conftest import FakeClient, reply

    now = datetime(2026, 10, 4, 15, 0, tzinfo=ZoneInfo(jobs_settings.home_tz))
    monkeypatch.setattr(jobs, "now_utc", lambda: now.astimezone(UTC))
    opener = lambda: open_db(jobs_settings.storage.db_path)  # noqa: E731
    capped = replace(jobs_settings, summary=replace(jobs_settings.summary, monthly_cap_usd=0.0))
    fake = FakeClient(reply("not an object", output_tokens=40), reply("nor this"), reply("x"))
    monkeypatch.setattr(generate, "make_client", lambda settings: fake)

    assert jobs.run_month_feature(capped, opener) == "failed"
    assert fake.requests == []
    assert db.execute("SELECT COUNT(*) FROM month_feature_attempts").fetchone()[0] == 0

    assert jobs.run_month_feature(jobs_settings, opener) == "failed"
    assert len(fake.requests) == 2
    assert jobs.run_month_feature(jobs_settings, opener) == "skipped"
    assert len(fake.requests) == 2
    assert db.execute("SELECT COUNT(*) FROM model_spend").fetchone()[0] == 2


def test_month_feature_catch_up_runs_when_the_db_cannot_say(jobs_settings, caplog):
    def broken():
        raise sqlite3.OperationalError("locked")

    now = datetime(2026, 10, 4, 15, 0, tzinfo=ZoneInfo(jobs_settings.home_tz))
    assert jobs.month_feature_missing(jobs_settings, broken, now)
    assert "cannot read this month's feature" in caplog.text


def test_summary_catch_up_runs_when_the_db_cannot_say(jobs_settings, caplog):
    def broken():
        raise sqlite3.OperationalError("locked")

    late = datetime(2026, 10, 2, 9, 0, tzinfo=ZoneInfo(jobs_settings.home_tz))
    assert jobs.summary_overdue(jobs_settings, broken, late)
    assert "cannot read today's line" in caplog.text


def test_scheduler_is_off_unless_settings_enable_it(client, jobs_settings):
    assert jobs_settings.scheduler.enabled is False
    assert client.app.state.scheduler is None

    enabled = replace(jobs_settings, scheduler=replace(jobs_settings.scheduler, enabled=True))
    app = create_app(enabled)
    with TestClient(app):
        running = app.state.scheduler
        assert running.running
        assert {job.id for job in running.get_jobs()} == jobs.CORE_JOBS
    assert not running.running
    assert app.state.scheduler is None


def test_config_file_ships_with_the_scheduler_on(monkeypatch):
    from app.config import load_settings

    monkeypatch.delenv("LIFE_SCHEDULER_ENABLED")
    shipped = load_settings()
    assert shipped.scheduler.enabled is True
    assert shipped.scheduler.usage_min_read_seconds >= shipped.scheduler.usage_poll_seconds
    assert jobs.parse_hh_mm(shipped.backup.time) == (3, 15)


def test_a_bad_goodreads_time_does_not_stop_the_scheduler(jobs_settings, caplog):
    typo = replace(jobs_settings, pull=replace(jobs_settings.pull, goodreads="6h30"))
    scheduler = jobs.build_scheduler(typo, lambda: open_db(typo.storage.db_path))
    assert {job.id for job in scheduler.get_jobs()} == jobs.CORE_JOBS
    assert jobs.GOODREADS_JOB not in caplog.text

    with_url = replace(typo, goodreads_rss_url="https://www.goodreads.com/review/list_rss/1")
    scheduler = jobs.build_scheduler(with_url, lambda: open_db(with_url.storage.db_path))
    assert {job.id for job in scheduler.get_jobs()} == jobs.CORE_JOBS
    assert f"{jobs.GOODREADS_JOB} not registered" in caplog.text


def test_backup_job_logs_raw_files_missing_from_the_archive(client, db, jobs_settings, caplog):
    post_fixture(client, "workouts_v2_overlap.json")
    post_fixture(client, "metrics_v2_days.json")
    gone = sorted((jobs_settings.storage.raw_dir / "health").iterdir())[0]
    gone.unlink()
    scheduler = jobs.build_scheduler(jobs_settings, lambda: open_db(jobs_settings.storage.db_path))

    scheduler.get_job(BACKUP_JOB).func()

    assert len(backup.list_backups(jobs_settings.backup.dir)) == 1
    assert scheduler.job_stats[BACKUP_JOB] == JobStats(runs=1)
    assert f"is missing 1 raw file(s): health/{gone.name}" in caplog.text


def test_malformed_usage_read_does_not_use_up_the_throttle_window(db, jobs_settings):
    clock, opened = Clock(), CountingOpen(jobs_settings)
    watcher = UsageWatcher(jobs_settings, opened, clock)

    jobs_settings.claude_usage.path.write_text("{not json")
    assert watcher.tick() == "malformed"
    clock.now += 30
    assert watcher.tick() == "unchanged"
    write_usage(jobs_settings, 41.2, 1)
    clock.now += 30
    assert watcher.tick() == "ok"
    assert count(db, "daily_metrics") == 1


def test_failed_usage_read_is_retried_on_the_next_tick(db, jobs_settings):
    clock = Clock()
    attempts: list[int] = []

    def flaky_open() -> sqlite3.Connection:
        attempts.append(1)
        if len(attempts) == 1:
            raise sqlite3.OperationalError("database is locked")
        return open_db(jobs_settings.storage.db_path)

    watcher = UsageWatcher(jobs_settings, flaky_open, clock)
    write_usage(jobs_settings, 41.2, 1)
    with pytest.raises(sqlite3.OperationalError):
        watcher.tick()
    clock.now += 30
    assert watcher.tick() == "ok"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", True), ("true", True), ("YES", True), ("0", False), ("false", False), ("No", False)],
)
def test_scheduler_env_switch_accepts_words(monkeypatch, value, expected):
    from app.config import load_settings

    monkeypatch.setenv("LIFE_SCHEDULER_ENABLED", value)
    assert load_settings().scheduler.enabled is expected


def test_scheduler_env_switch_rejects_anything_else(monkeypatch):
    from app.config import load_settings

    monkeypatch.setenv("LIFE_SCHEDULER_ENABLED", "maybe")
    with pytest.raises(ValueError, match="LIFE_SCHEDULER_ENABLED"):
        load_settings()


def city_on(jobs_settings):
    return replace(jobs_settings, city=replace(jobs_settings.city, enabled=True))


def test_city_jobs_are_registered_only_when_the_panel_is_enabled(jobs_settings):
    open_conn = lambda: open_db(jobs_settings.storage.db_path)  # noqa: E731
    assert jobs_settings.city.enabled is False
    off = jobs.build_scheduler(jobs_settings, open_conn)
    assert {job.id for job in off.get_jobs()} == jobs.CORE_JOBS
    assert not jobs.CITY_JOBS & jobs.CORE_JOBS

    start = datetime(2026, 10, 5, 3, 0, tzinfo=UTC)
    on = jobs.build_scheduler(city_on(jobs_settings), open_conn, now=start)
    on.start(paused=True)
    try:
        by_id = {job.id: job for job in on.get_jobs()}
        assert set(by_id) == jobs.CORE_JOBS | {"city_transit", "city_weather"}
        transit, weather = by_id[jobs.CITY_TRANSIT_JOB], by_id[jobs.CITY_WEATHER_JOB]
        assert transit.trigger.interval == timedelta(minutes=5)
        assert weather.trigger.interval == timedelta(minutes=30)
        assert (transit.misfire_grace_time, weather.misfire_grace_time) == (300, 1800)
        assert transit.next_run_time == start + timedelta(seconds=20)
        assert weather.next_run_time == start + timedelta(seconds=30)
        assert all(job.max_instances == 1 and job.coalesce for job in (transit, weather))
    finally:
        on.shutdown(wait=False)


def test_city_jobs_store_a_snapshot_and_log_a_dead_network_once(
    db, jobs_settings, monkeypatch, caplog
):
    import httpx

    from app.city import fetch as city_fetch
    from app.city import store as city_store
    from tests.city.test_fetch import Feeds

    feeds = Feeds()
    monkeypatch.setattr(city_fetch, "new_client", feeds.client)
    monkeypatch.setattr(city_fetch, "now_utc", lambda: datetime(2026, 10, 5, 3, 0, tzinfo=UTC))
    settings = city_on(jobs_settings)
    scheduler = jobs.build_scheduler(settings, lambda: open_db(settings.storage.db_path))
    transit = scheduler.get_job(jobs.CITY_TRANSIT_JOB).func
    weather = scheduler.get_job(jobs.CITY_WEATHER_JOB).func

    transit()
    weather()
    good = city_store.load_status(db, "2026-10-04")
    assert len(good.lines) == 7 and good.weather.temp_f == 56.6
    assert scheduler.job_stats[jobs.CITY_TRANSIT_JOB] == JobStats(runs=1)

    feeds.broken["api-endpoint.mta.info"] = httpx.ConnectError("network is down")
    feeds.broken["api.open-meteo.com"] = httpx.Response(200, json={"error": True})
    caplog.clear()
    for _ in range(3):
        transit()
        weather()
    stats = scheduler.job_stats
    assert (stats[jobs.CITY_TRANSIT_JOB].runs, stats[jobs.CITY_TRANSIT_JOB].failures) == (4, 3)
    assert (stats[jobs.CITY_WEATHER_JOB].runs, stats[jobs.CITY_WEATHER_JOB].failures) == (4, 3)
    assert [record.getMessage() for record in caplog.records] == [
        "job city_transit failed: FetchError: api-endpoint.mta.info: ConnectError: "
        "network is down (repeats are not logged)",
        "job city_weather failed: ParseError: the forecast reply is not an Open-Meteo forecast "
        "(no hourly block) (repeats are not logged)",
    ]
    assert all(record.exc_info is None for record in caplog.records), "no traceback"
    assert city_store.load_status(db, "2026-10-04") == good, "the last good snapshot stays"

    feeds.broken.clear()
    transit()
    assert stats[jobs.CITY_TRANSIT_JOB].last_error is None


def test_a_city_job_bug_is_logged_with_its_traceback_and_never_raised(
    jobs_settings, monkeypatch, caplog
):
    from app.city import fetch as city_fetch

    def broken(conn, settings):
        raise KeyError("a bug, not a network failure")

    monkeypatch.setattr(city_fetch, "poll_transit", broken)
    settings = city_on(jobs_settings)
    scheduler = jobs.build_scheduler(settings, lambda: open_db(settings.storage.db_path))
    scheduler.get_job(jobs.CITY_TRANSIT_JOB).func()
    assert scheduler.job_stats[jobs.CITY_TRANSIT_JOB].failures == 1
    assert "job city_transit failed" in caplog.text and "Traceback" in caplog.text


def test_a_health_push_pulls_the_recompute_job_to_now(jobs_settings, monkeypatch):
    from app.metrics.job import RECOMPUTE_JOB

    scheduler = jobs.build_scheduler(jobs_settings, lambda: open_db(jobs_settings.storage.db_path))
    scheduler.start(paused=True)
    try:
        app = create_app(jobs_settings)
        with TestClient(app) as client:
            app.state.scheduler = scheduler
            before = scheduler.get_job(RECOMPUTE_JOB).next_run_time
            assert before.astimezone(UTC) > datetime.now(UTC) + timedelta(seconds=30)
            assert post_fixture(client, "batch_part1.json").status_code == 200
            after = scheduler.get_job(RECOMPUTE_JOB).next_run_time
            assert after.astimezone(UTC) <= datetime.now(UTC)
            scheduler.get_job(RECOMPUTE_JOB).modify(next_run_time=before)
            assert post_fixture(client, "batch_part1.json").json()["status"] == "duplicate"
            assert scheduler.get_job(RECOMPUTE_JOB).next_run_time == before
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)


def test_guarded_job_logs_the_traceback_once_and_one_line_on_repeats(caplog):
    stats: dict[str, JobStats] = {}

    def broken() -> None:
        raise RuntimeError("schema version 9 and this code expects 8")

    run = jobs.guarded("broken", broken, stats)
    run()
    assert caplog.text.count("Traceback") == 1
    caplog.clear()
    run()
    run()
    assert "Traceback" not in caplog.text
    assert caplog.text.count("job broken failed again: RuntimeError: schema version 9") == 2
    assert stats["broken"].failures == 3
