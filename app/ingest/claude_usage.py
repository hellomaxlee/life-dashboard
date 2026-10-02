"""Claude usage reader: the status-line hook's file is a source like any other (issue #2).

The file is archived verbatim first, then the seven-day window lands in `daily_metrics` on
the home-timezone day the reading was captured. The window is rolling, not Monday to Sunday.
Setting CLAUDE_USAGE_MUTANT_FIVE_HOUR=1 reads the five-hour window instead; it is the mutant
that proves the golden case is a real gate.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.ingest.health import archive_raw, run_ingest
from app.timeutil import from_utc_iso, local_day, to_utc_iso

SOURCE = "claude_usage"
USED_PCT = "claude_week_used_pct"
RESETS_AT = "claude_week_resets_at"
CAPTURED_AT = "claude_week_captured_at"
MAX_BYTES = 64 * 1024
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class UsageReading:
    captured_at_utc: str
    used_pct: float
    resets_at_utc: str | None


@dataclass(frozen=True)
class ReadResult:
    status: str
    raw_archive_id: int | None = None
    reading: UsageReading | None = None


def window_name() -> str:
    mutant = os.environ.get("CLAUDE_USAGE_MUTANT_FIVE_HOUR", "") == "1"
    return "five_hour" if mutant else "seven_day"


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(value):
        raise ValueError(f"non-finite number {value!r}")
    return float(value)


def _reject_constant(name: str) -> float:
    raise ValueError(f"{name} is not a number this reader accepts")


def parse_usage(body: bytes) -> UsageReading | None:
    """Read the hook's file. None when it carries no seven-day reading; ValueError when broken."""
    try:
        record = json.loads(body, parse_constant=_reject_constant)
    except RecursionError as exc:
        raise ValueError(f"nested too deeply: {exc}") from exc
    if not isinstance(record, dict):
        raise ValueError("claude usage file is not a JSON object")
    captured = record.get("captured_at_utc")
    if not isinstance(captured, str):
        raise ValueError("claude usage file has no captured_at_utc")
    captured_at_utc = to_utc_iso(from_utc_iso(captured))
    rate_limits = record.get("rate_limits")
    window = rate_limits.get(window_name()) if isinstance(rate_limits, dict) else None
    used_pct = _number(window.get("used_percentage")) if isinstance(window, dict) else None
    if used_pct is None:
        return None
    resets = _number(window.get("resets_at"))
    try:
        resets_at_utc = (
            to_utc_iso(datetime.fromtimestamp(resets, UTC)) if resets is not None else None
        )
    except (OverflowError, OSError) as exc:
        raise ValueError(f"resets_at {resets!r} is not a timestamp: {exc}") from exc
    return UsageReading(captured_at_utc, used_pct, resets_at_utc)


def store_reading(conn: sqlite3.Connection, reading: UsageReading, home_tz: str) -> bool:
    """Merge the reading into its day's metrics row. The latest capture of a day wins."""
    day = local_day(from_utc_iso(reading.captured_at_utc), home_tz)
    row = conn.execute(
        "SELECT metrics_json FROM daily_metrics WHERE day_local = ?", (day,)
    ).fetchone()
    metrics: dict[str, object] = json.loads(row["metrics_json"]) if row else {}
    if str(metrics.get(CAPTURED_AT, "")) > reading.captured_at_utc:
        return False
    metrics[USED_PCT] = reading.used_pct
    metrics[RESETS_AT] = reading.resets_at_utc
    metrics[CAPTURED_AT] = reading.captured_at_utc
    conn.execute(
        "INSERT INTO daily_metrics (day_local, metrics_json) VALUES (?, ?) "
        "ON CONFLICT (day_local) DO UPDATE SET metrics_json = excluded.metrics_json",
        (day, json.dumps(metrics, sort_keys=True)),
    )
    return True


def apply_reading(
    conn: sqlite3.Connection, body: bytes, raw_archive_id: int, settings: Settings, first: bool
) -> UsageReading | None:
    """Parse and store one usage file inside the caller's transaction."""
    try:
        reading = parse_usage(body)
    except ValueError as exc:
        raise ValueError(f"malformed claude usage: {exc}") from exc
    if reading is not None:
        store_reading(conn, reading, settings.home_tz)
    return reading


def ingest_archived(
    conn: sqlite3.Connection,
    body: bytes,
    raw_archive_id: int,
    settings: Settings,
    raw_dir: Path | None = None,
) -> UsageReading | None:
    """Parse and store an already-archived usage file, keeping raw_archive.id order."""
    reading, _ = run_ingest(conn, body, raw_archive_id, settings, apply_reading, raw_dir)
    assert reading is None or isinstance(reading, UsageReading)
    return reading


def read_usage_file(conn: sqlite3.Connection, settings: Settings) -> ReadResult:
    """Archive the hook's file and store its reading. Unchanged bytes are a no-op.

    A file larger than MAX_BYTES is not the hook's output (a real one is about 250 bytes);
    it is reported as malformed and deliberately not archived.
    """
    path = settings.claude_usage.path
    if not path.is_file():
        return ReadResult("missing")
    size = path.stat().st_size
    if size > MAX_BYTES:
        log.warning(
            "claude usage file is %d bytes, larger than the %d byte cap; not archived",
            size,
            MAX_BYTES,
        )
        return ReadResult("malformed")
    body = path.read_bytes()
    archived = archive_raw(conn, settings.storage.raw_dir, body, source=SOURCE)
    if archived.duplicate:
        return ReadResult("duplicate", archived.raw_archive_id)
    try:
        reading = ingest_archived(conn, body, archived.raw_archive_id, settings)
    except ValueError:
        return ReadResult("malformed", archived.raw_archive_id)
    return ReadResult("ok" if reading else "no_data", archived.raw_archive_id, reading)
