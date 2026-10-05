"""Scheduled jobs. Health ingest is a push and needs none.

Every job opens its own SQLite connection per run, never overlaps itself, and logs an
exception instead of raising it, so one bad run cannot stop the next.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.city import fetch as city_fetch
from app.city.parse import ParseError as CityParseError
from app.config import Settings
from app.ingest import goodreads
from app.ingest.claude_usage import read_usage_file
from app.jobs.rotation import (
    ROTATION_FALLBACK_S,
    ROTATION_JOB,
    ROTATION_MISFIRE_GRACE_S,
    SEND_ERRORS,
    ClipAdapter,
    DeviceRotation,
    device_adapter,
)
from app.metrics.job import RECOMPUTE_JOB, ROLLOVER_JOB, run_recompute
from app.month import store as month_store
from app.month.generate import ensure_month_feature
from app.summary import memory
from app.summary.run import write_summary
from app.timeutil import from_utc_iso, local_day, now_utc
from tools import backup

USAGE_JOB = "claude_usage_watch"
BACKUP_JOB = "nightly_backup"
BACKUP_CHECK_JOB = "backup_overdue_check"
GOODREADS_JOB = "goodreads_poll"
SUMMARY_JOB = "daily_summary"
MONTH_JOB = "month_feature"
CORE_JOBS = frozenset(
    {USAGE_JOB, BACKUP_JOB, BACKUP_CHECK_JOB, SUMMARY_JOB, RECOMPUTE_JOB, ROLLOVER_JOB, MONTH_JOB}
)
CITY_TRANSIT_JOB = "city_transit"
CITY_WEATHER_JOB = "city_weather"
CITY_JOBS = frozenset({CITY_TRANSIT_JOB, CITY_WEATHER_JOB})
CITY_ERRORS = (city_fetch.FetchError, CityParseError)
CITY_TRANSIT_FIRST_DELAY = timedelta(seconds=20)
CITY_WEATHER_FIRST_DELAY = timedelta(seconds=30)
MONTH_TIME = (0, 20)
MONTH_MISFIRE_GRACE_S = 20 * 3600
MONTH_CATCHUP_DELAY = timedelta(minutes=5)
SUMMARY_MISFIRE_GRACE_S = 12 * 3600
SUMMARY_CATCHUP_DELAY = timedelta(minutes=4)
RECOMPUTE_FIRST_DELAY = timedelta(seconds=60)
BACKUP_MISFIRE_GRACE_S = 18 * 3600
BACKUP_OVERDUE = timedelta(hours=26)
BACKUP_CATCHUP_DELAY = timedelta(minutes=2)
BACKUP_CHECK_EVERY = timedelta(hours=1)
GOODREADS_MISFIRE_GRACE_S = 18 * 3600
GOODREADS_OVERDUE = timedelta(hours=24)
GOODREADS_CATCHUP_DELAY = timedelta(minutes=3)
SHUTDOWN_WAIT_S = 10.0

log = logging.getLogger(__name__)
OpenConn = Callable[[], sqlite3.Connection]


@dataclass
class JobStats:
    runs: int = 0
    failures: int = 0
    last_error: str | None = None


def guarded(
    name: str,
    fn: Callable[[], object],
    stats: dict[str, JobStats],
    quiet: tuple[type[Exception], ...] = (),
) -> Callable[[], None]:
    """Wrap a job so an exception is logged and counted, never raised into the scheduler.
    A `quiet` exception is one line with no traceback, and only when it differs from the
    last failure, so a device that is switched off does not fill the log."""
    record = stats.setdefault(name, JobStats())

    def run() -> None:
        record.runs += 1
        try:
            fn()
            record.last_error = None
        except Exception as exc:
            record.failures += 1
            error = f"{type(exc).__name__}: {exc}"
            if not isinstance(exc, quiet):
                log.exception("job %s failed", name)
            elif error != record.last_error:
                log.error("job %s failed: %s (repeats are not logged)", name, error)
            record.last_error = error

    return run


class UsageWatcher:
    """Reads the Claude usage file only when its mtime or size changed, at most once per
    `usage_min_read_seconds`. An unchanged or throttled tick never opens the db. A read
    that raised or found the file malformed does not start the wait, so the next change is
    read on the next tick."""

    def __init__(
        self, settings: Settings, open_conn: OpenConn, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._settings = settings
        self._open_conn = open_conn
        self._clock = clock
        self._seen: tuple[int, int] | None = None
        self._last_read: float | None = None

    def tick(self) -> str:
        try:
            stat = self._settings.claude_usage.path.stat()
        except FileNotFoundError:
            return "missing"
        signature = (stat.st_mtime_ns, stat.st_size)
        if signature == self._seen:
            return "unchanged"
        now = self._clock()
        min_gap = self._settings.scheduler.usage_min_read_seconds
        if self._last_read is not None and now - self._last_read < min_gap:
            return "throttled"
        conn = self._open_conn()
        try:
            result = read_usage_file(conn, self._settings)
        finally:
            conn.close()
        self._seen = signature
        if result.status != "malformed":
            self._last_read = now
        return result.status


def parse_hh_mm(text: str) -> tuple[int, int]:
    hour, minute = text.strip().split(":")
    if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
        raise ValueError(f"not a HH:MM time: {text!r}")
    return int(hour), int(minute)


def backup_overdue(settings: Settings, now: datetime) -> bool:
    """True when the newest nightly backup is missing or older than a day and a bit."""
    backups = backup.list_backups(settings.backup.dir)
    return not backups or now - backup.backup_created_at(backups[-1]) > BACKUP_OVERDUE


def run_backup(settings: Settings, now: datetime | None = None) -> None:
    result = backup.nightly(settings) if now is None else backup.nightly(settings, now)
    if result.raw_missing:
        log.error(
            "backup %s is missing %d raw file(s): %s",
            result.path.name,
            len(result.raw_missing),
            ", ".join(result.raw_missing),
        )


def backup_if_overdue(settings: Settings, now: datetime) -> bool:
    """The hourly check: take a backup when none is newer than BACKUP_OVERDUE.

    It covers a first boot, a failed 03:15 run, and a Mac that slept through 03:15 for
    longer than the misfire grace."""
    if not backup_overdue(settings, now):
        return False
    run_backup(settings, now)
    return True


def last_goodreads_poll(conn: sqlite3.Connection) -> datetime | None:
    """When the newest parsed Goodreads feed was received, or None before the first one.

    An unchanged shelf polls to the same bytes and adds no row, so this is the last poll
    that brought a new feed, not the last poll."""
    row = conn.execute(
        "SELECT MAX(received_at_utc) FROM raw_archive WHERE source = ? AND parsed_ok = 1",
        (goodreads.SOURCE,),
    ).fetchone()
    return None if row[0] is None else from_utc_iso(row[0])


def goodreads_overdue(open_conn: OpenConn, now: datetime) -> bool:
    """True when no parsed feed arrived within GOODREADS_OVERDUE, or the db cannot say."""
    try:
        conn = open_conn()
    except Exception:
        log.exception("%s: cannot read the last poll time; polling soon", GOODREADS_JOB)
        return True
    try:
        last = last_goodreads_poll(conn)
    finally:
        conn.close()
    return last is None or now - last > GOODREADS_OVERDUE


def run_goodreads_poll(settings: Settings, open_conn: OpenConn) -> goodreads.PollResult:
    conn = open_conn()
    try:
        result = goodreads.poll_goodreads(conn, settings)
    finally:
        conn.close()
    log.info("%s: %s (%d books)", GOODREADS_JOB, result.status, result.books)
    return result


def run_daily_summary(settings: Settings, open_conn: OpenConn) -> str:
    """Write today's summary (home-timezone day). Stored line → no model call."""
    day = local_day(now_utc(), settings.home_tz)
    conn = open_conn()
    try:
        result = write_summary(conn, settings, day)
    finally:
        conn.close()
    log.info("summary %s [%s] %s", day, result.source, result.line)
    return result.source


def summary_overdue(settings: Settings, open_conn: OpenConn, now: datetime) -> bool:
    """True when today's summary time has passed and today has no stored line, or the db
    cannot say. A start after 06:50 would otherwise show no line until tomorrow."""
    tz = ZoneInfo(settings.home_tz)
    hour, minute = parse_hh_mm(settings.summary.time)
    local = now.astimezone(tz)
    if (local.hour, local.minute) < (hour, minute):
        return False
    try:
        conn = open_conn()
        try:
            return memory.stored_line(conn, local_day(now, settings.home_tz)) is None
        finally:
            conn.close()
    except Exception:
        log.exception("%s: cannot read today's line; writing it soon", SUMMARY_JOB)
        return True


def run_month_feature(settings: Settings, open_conn: OpenConn) -> str:
    """Author this home-timezone month's feature if it has none. A stored feature, the
    attempt guard and the monthly cap each mean no model call."""
    moment = now_utc()
    month = local_day(moment, settings.home_tz)[:7]
    conn = open_conn()
    try:
        result = ensure_month_feature(conn, settings, month, now=moment)
    finally:
        conn.close()
    log.info("%s %s [%s] %s", MONTH_JOB, month, result.status, result.reason)
    return result.status


def month_feature_missing(settings: Settings, open_conn: OpenConn, now: datetime) -> bool:
    """True when this month has no stored feature, or the db cannot say. A start in the
    middle of a month would otherwise wait for 00:20."""
    try:
        conn = open_conn()
        try:
            return month_store.load_feature(conn, local_day(now, settings.home_tz)[:7]) is None
        finally:
            conn.close()
    except Exception:
        log.exception("%s: cannot read this month's feature; trying soon", MONTH_JOB)
        return True


def run_city_transit(settings: Settings, open_conn: OpenConn) -> int:
    """Fetch the MTA alert feeds and replace the stored transit snapshot. A failed fetch
    raises a CITY_ERRORS error and leaves the last snapshot in place."""
    conn = open_conn()
    try:
        statuses = city_fetch.poll_transit(conn, settings)
    finally:
        conn.close()
    return len(statuses)


def run_city_weather(settings: Settings, open_conn: OpenConn) -> None:
    """Fetch the forecast and the weather alerts and replace the stored weather snapshot."""
    conn = open_conn()
    try:
        city_fetch.poll_weather(conn, settings)
    finally:
        conn.close()


def stop_scheduler(scheduler: BackgroundScheduler) -> bool:
    """Shut the scheduler down, letting running jobs finish for at most SHUTDOWN_WAIT_S.

    Returns False when a job was still running at the deadline; shutdown then proceeds
    without it rather than waiting forever."""
    waiter = threading.Thread(
        target=lambda: scheduler.shutdown(wait=True), name="scheduler-shutdown", daemon=True
    )
    waiter.start()
    waiter.join(SHUTDOWN_WAIT_S)
    if waiter.is_alive():
        log.error(
            "scheduler shutdown: a job is still running after %.1f s; not waiting for it",
            SHUTDOWN_WAIT_S,
        )
        return False
    return True


def build_scheduler(
    settings: Settings,
    open_conn: OpenConn,
    now: datetime | None = None,
    device: ClipAdapter | None = None,
) -> BackgroundScheduler:
    """A configured, not yet started scheduler. `scheduler.job_stats` counts runs and failures.

    `device` replaces the Pixoo adapter (tests); it is used only while a device is configured.
    """
    if settings.scheduler.usage_poll_seconds < 1:
        raise ValueError(
            f"scheduler.usage_poll_seconds must be at least 1, not "
            f"{settings.scheduler.usage_poll_seconds}"
        )
    tz = ZoneInfo(settings.home_tz)
    scheduler = BackgroundScheduler(
        timezone=tz, job_defaults={"coalesce": True, "max_instances": 1}
    )
    stats: dict[str, JobStats] = {}
    scheduler.job_stats = stats

    watcher = UsageWatcher(settings, open_conn)
    scheduler.add_job(
        guarded(USAGE_JOB, watcher.tick, stats),
        IntervalTrigger(seconds=settings.scheduler.usage_poll_seconds, timezone=tz),
        id=USAGE_JOB,
        misfire_grace_time=settings.scheduler.usage_poll_seconds,
    )

    hour, minute = parse_hh_mm(settings.backup.time)
    moment = now or now_utc()
    scheduler.add_job(
        guarded(BACKUP_JOB, lambda: run_backup(settings), stats),
        CronTrigger(hour=hour, minute=minute, timezone=tz),
        id=BACKUP_JOB,
        misfire_grace_time=BACKUP_MISFIRE_GRACE_S,
    )
    scheduler.add_job(
        guarded(BACKUP_CHECK_JOB, lambda: backup_if_overdue(settings, now_utc()), stats),
        IntervalTrigger(seconds=int(BACKUP_CHECK_EVERY.total_seconds()), timezone=tz),
        id=BACKUP_CHECK_JOB,
        misfire_grace_time=int(BACKUP_CHECK_EVERY.total_seconds()),
        next_run_time=moment + BACKUP_CATCHUP_DELAY,
    )

    if settings.goodreads_rss_url:
        try:
            poll_hour, poll_minute = parse_hh_mm(settings.pull.goodreads)
        except ValueError:
            log.exception("%s not registered: pull.goodreads is not HH:MM", GOODREADS_JOB)
        else:
            first_run = {}
            if goodreads_overdue(open_conn, moment):
                first_run = {"next_run_time": moment + GOODREADS_CATCHUP_DELAY}
            scheduler.add_job(
                guarded(GOODREADS_JOB, lambda: run_goodreads_poll(settings, open_conn), stats),
                CronTrigger(hour=poll_hour, minute=poll_minute, timezone=tz),
                id=GOODREADS_JOB,
                misfire_grace_time=GOODREADS_MISFIRE_GRACE_S,
                **first_run,
            )
    summary_hour, summary_minute = parse_hh_mm(settings.summary.time)
    first_summary = {}
    if summary_overdue(settings, open_conn, moment):
        first_summary = {"next_run_time": moment + SUMMARY_CATCHUP_DELAY}
    scheduler.add_job(
        guarded(SUMMARY_JOB, lambda: run_daily_summary(settings, open_conn), stats),
        CronTrigger(hour=summary_hour, minute=summary_minute, timezone=tz),
        id=SUMMARY_JOB,
        misfire_grace_time=SUMMARY_MISFIRE_GRACE_S,
        **first_summary,
    )
    first_month = {}
    if month_feature_missing(settings, open_conn, moment):
        first_month = {"next_run_time": moment + MONTH_CATCHUP_DELAY}
    scheduler.add_job(
        guarded(MONTH_JOB, lambda: run_month_feature(settings, open_conn), stats),
        CronTrigger(hour=MONTH_TIME[0], minute=MONTH_TIME[1], timezone=tz),
        id=MONTH_JOB,
        misfire_grace_time=MONTH_MISFIRE_GRACE_S,
        **first_month,
    )
    scheduler.add_job(
        guarded(RECOMPUTE_JOB, lambda: run_recompute(settings, open_conn), stats),
        IntervalTrigger(minutes=settings.metrics.recompute_minutes, timezone=tz),
        id=RECOMPUTE_JOB,
        misfire_grace_time=settings.metrics.recompute_minutes * 60,
        next_run_time=moment + RECOMPUTE_FIRST_DELAY,
    )
    roll_hour, roll_minute = parse_hh_mm(settings.metrics.rollover_time)
    scheduler.add_job(
        guarded(ROLLOVER_JOB, lambda: run_recompute(settings, open_conn), stats),
        CronTrigger(hour=roll_hour, minute=roll_minute, timezone=tz),
        id=ROLLOVER_JOB,
        misfire_grace_time=int(timedelta(hours=23).total_seconds()),
    )

    if settings.city.enabled:
        city_jobs = (
            (
                CITY_TRANSIT_JOB,
                run_city_transit,
                settings.city.transit_poll_minutes,
                CITY_TRANSIT_FIRST_DELAY,
            ),
            (
                CITY_WEATHER_JOB,
                run_city_weather,
                settings.city.weather_poll_minutes,
                CITY_WEATHER_FIRST_DELAY,
            ),
        )
        for job_id, run, minutes, first_delay in city_jobs:
            scheduler.add_job(
                guarded(job_id, lambda run=run: run(settings, open_conn), stats, quiet=CITY_ERRORS),
                IntervalTrigger(minutes=minutes, timezone=tz),
                id=job_id,
                misfire_grace_time=minutes * 60,
                next_run_time=moment + first_delay,
            )

    if settings.device.pixoo_host:
        try:
            adapter = device or device_adapter(settings)
        except ValueError:
            log.exception("%s not registered: bad [device] config", ROTATION_JOB)
        else:
            rotation = DeviceRotation(settings, open_conn, adapter)
            scheduler.rotation = rotation
            tick = guarded(ROTATION_JOB, rotation.tick, stats, quiet=SEND_ERRORS)

            def run_rotation() -> None:
                """One slot, then move the next run to when its hold is up, so no run
                overlaps a slow send. The interval is only the fallback cadence, longer
                than any send can take."""
                try:
                    tick()
                finally:
                    try:
                        scheduler.modify_job(ROTATION_JOB, next_run_time=rotation.due())
                    except JobLookupError:
                        pass

            scheduler.add_job(
                run_rotation,
                IntervalTrigger(seconds=ROTATION_FALLBACK_S, timezone=tz),
                id=ROTATION_JOB,
                next_run_time=moment,
                misfire_grace_time=ROTATION_MISFIRE_GRACE_S,
            )

    return scheduler


def start_scheduler(settings: Settings, open_conn: OpenConn) -> BackgroundScheduler:
    scheduler = build_scheduler(settings, open_conn)
    scheduler.start()
    return scheduler
