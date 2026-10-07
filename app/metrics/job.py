"""The scheduled recompute. Opens its own live connection per run and closes it."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.config import Settings
from app.metrics import judge
from app.metrics.engine import RecomputeResult, recompute, scored_days
from app.summary.run import make_client
from app.timeutil import now_utc

RECOMPUTE_JOB = "metrics_recompute"
ROLLOVER_JOB = "metrics_rollover"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobResult:
    metrics: RecomputeResult
    judge: judge.JudgeRun

    def __getattr__(self, name: str) -> object:
        return getattr(self.metrics, name)


def judge_before(
    conn: sqlite3.Connection, settings: Settings, client: object | None
) -> judge.JudgeRun:
    """Judge the unjudged days of the two-week window (notes.txt § Goal model, Judged
    workout). Days scored at the bar (from the raw series, not the stale metrics row) or
    credited by hand are skipped, so the engine's precedence holds before a call is ever
    made. No client or no `[judge] model`, no calls."""
    if client is None or not settings.judge_model:
        return judge.JudgeRun()
    skip = scored_days(conn, settings) | {
        row["day_local"] for row in conn.execute("SELECT day_local FROM manual_workouts")
    }
    return judge.judge_window(conn, settings, client, skip)


def run_recompute(
    settings: Settings,
    open_conn: Callable[[], sqlite3.Connection],
    client: object | None = None,
    today_local: str | None = None,
) -> JobResult:
    """The scheduler's tick and the CLI's --recompute: judge, then recompute. The judge
    runs only when `[judge] model` is set and a key is present, for both callers."""
    conn = open_conn()
    try:
        judged = judge_before(
            conn, settings, client if client is not None else make_client(settings)
        )
        if judged.judged:
            log.info("judged %d day(s) from heart rate", judged.judged)
        result = recompute(conn, settings, today_local)
    finally:
        conn.close()
    if result.days_written or result.weeks_written or result.calibrations_added:
        log.info(
            "metrics recomputed for %s: %d day row(s), %d week row(s), %d calibration(s)",
            result.today_local,
            result.days_written,
            result.weeks_written,
            result.calibrations_added,
        )
    return JobResult(result, judged)


def recompute_soon(scheduler: Any, now: datetime | None = None) -> bool:
    """Pull the recompute job's next run to now, so a Health push that landed a workout or
    a day's heart rate is judged and scored at once rather than at the next tick. No
    scheduler (tests, a disabled config) means nothing to move."""
    if scheduler is None:
        return False
    job = scheduler.get_job(RECOMPUTE_JOB)
    if job is None:
        return False
    job.modify(next_run_time=now or now_utc())
    return True
