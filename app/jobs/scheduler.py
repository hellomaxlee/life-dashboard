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

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import Settings
from app.ingest import goodreads
from app.ingest.claude_usage import read_usage_file
from app.jobs.rotation import ROTATION_JOB, ClipAdapter, DeviceRotation, device_adapter
from app.metrics.job import RECOMPUTE_JOB, ROLLOVER_JOB, run_recompute
from app.summary.run import write_summary
from app.timeutil import from_utc_iso, local_day, now_utc
from tools import backup

USAGE_JOB = "claude_usage_watch"
BACKUP_JOB = "nightly_backup"
BACKUP_CHECK_JOB = "backup_overdue_check"
GOODREADS_JOB = "goodreads_poll"
SUMMARY_JOB = "daily_summary"
CORE_JOBS = frozenset(
    {USAGE_JOB, BACKUP_JOB, BACKUP_CHECK_JOB, SUMMARY_JOB, RECOMPUTE_JOB, ROLLOVER_JOB}
)
SUMMARY_MISFIRE_GRACE_S = 12 * 3600
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


def guarded(name: str, fn: Callable[[], object], stats: dict[str, JobStats]) -> Callable[[], None]:
    """Wrap a job so an exception is logged and counted, never raised into the scheduler."""
    record = stats.setdefault(name, JobStats())

    def run() -> None:
        record.runs += 1
        try:
            fn()
            record.last_error = None
        except Exception as exc:
            record.failures += 1
            record.last_error = f"{type(exc).__name__}: {exc}"
            log.exception("job %s failed", name)

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
    scheduler.add_job(
        guarded(SUMMARY_JOB, lambda: run_daily_summary(settings, open_conn), stats),
        CronTrigger(hour=summary_hour, minute=summary_minute, timezone=tz),
        id=SUMMARY_JOB,
        misfire_grace_time=SUMMARY_MISFIRE_GRACE_S,
    )
    scheduler.add_job(
        guarded(RECOMPUTE_JOB, lambda: run_recompute(settings, open_conn), stats),
        IntervalTrigger(minutes=settings.metrics.recompute_minutes, timezone=tz),
        id=RECOMPUTE_JOB,
        next_run_time=moment + RECOMPUTE_FIRST_DELAY,
    )
    roll_hour, roll_minute = parse_hh_mm(settings.metrics.rollover_time)
    scheduler.add_job(
        guarded(ROLLOVER_JOB, lambda: run_recompute(settings, open_conn), stats),
        CronTrigger(hour=roll_hour, minute=roll_minute, timezone=tz),
        id=ROLLOVER_JOB,
        misfire_grace_time=int(timedelta(hours=23).total_seconds()),
    )

    if settings.device.pixoo_host:
        try:
            adapter = device or device_adapter(settings)
        except ValueError:
            log.exception("%s not registered: bad [device] config", ROTATION_JOB)
        else:
            rotation = DeviceRotation(settings, open_conn, adapter)
            scheduler.add_job(
                guarded(ROTATION_JOB, rotation.tick, stats),
                IntervalTrigger(seconds=settings.device.screen_seconds, timezone=tz),
                id=ROTATION_JOB,
                next_run_time=moment,
                misfire_grace_time=settings.device.screen_seconds,
            )

    return scheduler


def start_scheduler(settings: Settings, open_conn: OpenConn) -> BackgroundScheduler:
    scheduler = build_scheduler(settings, open_conn)
    scheduler.start()
    return scheduler
