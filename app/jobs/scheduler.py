"""Scheduled jobs. Health ingest is a push and needs none.

Every job opens its own SQLite connection per run, never overlaps itself, and logs an
exception instead of raising it, so one bad run cannot stop the next.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import Settings
from app.ingest.claude_usage import read_usage_file
from app.jobs.rotation import ROTATION_JOB, ClipAdapter, DeviceRotation, device_adapter
from app.timeutil import now_utc
from tools import backup

USAGE_JOB = "claude_usage_watch"
BACKUP_JOB = "nightly_backup"
BACKUP_MISFIRE_GRACE_S = 18 * 3600
BACKUP_OVERDUE = timedelta(hours=26)
BACKUP_CATCHUP_DELAY = timedelta(minutes=2)

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


def run_backup(settings: Settings) -> None:
    result = backup.nightly(settings)
    if result.raw_missing:
        log.error(
            "backup %s is missing %d raw file(s): %s",
            result.path.name,
            len(result.raw_missing),
            ", ".join(result.raw_missing),
        )


def build_scheduler(
    settings: Settings,
    open_conn: OpenConn,
    now: datetime | None = None,
    device: ClipAdapter | None = None,
) -> BackgroundScheduler:
    """A configured, not yet started scheduler. `scheduler.job_stats` counts runs and failures.

    `device` replaces the Pixoo adapter (tests); it is used only while a device is configured.
    """
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
    catch_up = {}
    if backup_overdue(settings, moment):
        catch_up = {"next_run_time": moment + BACKUP_CATCHUP_DELAY}
    scheduler.add_job(
        guarded(BACKUP_JOB, lambda: run_backup(settings), stats),
        CronTrigger(hour=hour, minute=minute, timezone=tz),
        id=BACKUP_JOB,
        misfire_grace_time=BACKUP_MISFIRE_GRACE_S,
        **catch_up,
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
