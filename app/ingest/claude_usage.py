"""Claude usage reader: the status-line hook's file is a source like any other (issue #2).

The file is archived verbatim first, then the seven-day window lands in `daily_metrics` on
the home-timezone day the reading was captured. The window is rolling, not Monday to Sunday.
Setting CLAUDE_USAGE_MUTANT_FIVE_HOUR=1 reads the five-hour window instead; it is the mutant
that proves the golden case is a real gate.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from app.config import Settings
from app.ingest.health import archive_raw, mark_parsed
from app.timeutil import from_utc_iso, local_day, to_utc_iso

SOURCE = "claude_usage"
USED_PCT = "claude_week_used_pct"
RESETS_AT = "claude_week_resets_at"
CAPTURED_AT = "claude_week_captured_at"


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
    return float(value)


def parse_usage(body: bytes) -> UsageReading | None:
    """Read the hook's file. None when it carries no seven-day reading; ValueError when broken."""
    record = json.loads(body)
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
    resets_at_utc = to_utc_iso(datetime.fromtimestamp(resets, UTC)) if resets is not None else None
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


def ingest_archived(
    conn: sqlite3.Connection, body: bytes, raw_archive_id: int, settings: Settings
) -> UsageReading | None:
    """Parse and store an already-archived usage file inside one transaction."""
    try:
        reading = parse_usage(body)
    except ValueError as exc:
        mark_parsed(conn, raw_archive_id, False, f"malformed claude usage: {exc}")
        raise
    conn.execute("BEGIN IMMEDIATE")
    try:
        if reading is not None:
            store_reading(conn, reading, settings.home_tz)
        mark_parsed(conn, raw_archive_id, True, None)
        conn.execute("COMMIT")
    except Exception as exc:
        conn.execute("ROLLBACK")
        mark_parsed(conn, raw_archive_id, False, f"{type(exc).__name__}: {exc}")
        raise
    return reading


def read_usage_file(conn: sqlite3.Connection, settings: Settings) -> ReadResult:
    """Archive the hook's file and store its reading. Unchanged bytes are a no-op."""
    path = settings.claude_usage.path
    if not path.is_file():
        return ReadResult("missing")
    body = path.read_bytes()
    archived = archive_raw(conn, settings.storage.raw_dir, body, source=SOURCE)
    if archived.duplicate:
        status = "superseded" if archived.superseded else "duplicate"
        return ReadResult(status, archived.raw_archive_id)
    try:
        reading = ingest_archived(conn, body, archived.raw_archive_id, settings)
    except ValueError:
        return ReadResult("malformed", archived.raw_archive_id)
    return ReadResult("ok" if reading else "no_data", archived.raw_archive_id, reading)
