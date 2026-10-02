"""The scheduled recompute. Opens its own live connection per run and closes it."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable

from app.config import Settings
from app.metrics.engine import RecomputeResult, recompute

RECOMPUTE_JOB = "metrics_recompute"
ROLLOVER_JOB = "metrics_rollover"

log = logging.getLogger(__name__)


def run_recompute(
    settings: Settings, open_conn: Callable[[], sqlite3.Connection]
) -> RecomputeResult:
    conn = open_conn()
    try:
        result = recompute(conn, settings)
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
    return result
