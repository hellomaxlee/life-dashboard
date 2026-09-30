"""One canonical activity: a workout seen from two HealthKit source apps is one record.

Rule (notes.txt § Goal model, Dedupe): starts within `start_window_min` minutes and durations
within `duration_tolerance_pct` percent, from a source app not already attached to the candidate.
Setting DEDUPE_DISABLED=1 in the environment bypasses the rule; it is the mutant the
source-replay skill runs to prove the dedupe test is a real gate.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import timedelta

from app.config import DedupeConfig
from app.timeutil import from_utc_iso, to_utc_iso


def disabled() -> bool:
    return os.environ.get("DEDUPE_DISABLED", "") == "1"


def durations_match(a_s: int, b_s: int, tolerance_pct: float) -> bool:
    longest = max(a_s, b_s)
    if longest <= 0:
        return a_s == b_s
    return abs(a_s - b_s) / longest * 100.0 <= tolerance_pct


def find_matching_activity(
    conn: sqlite3.Connection,
    start_utc: str,
    duration_s: int,
    source_app: str,
    cfg: DedupeConfig,
) -> str | None:
    """Return the canonical activity id an incoming workout should merge into, or None."""
    if disabled():
        return None
    start = from_utc_iso(start_utc)
    window = timedelta(minutes=cfg.start_window_min)
    rows = conn.execute(
        "SELECT id, duration_s FROM activities WHERE start_utc BETWEEN ? AND ? ORDER BY start_utc",
        (to_utc_iso(start - window), to_utc_iso(start + window)),
    ).fetchall()
    for row in rows:
        if not durations_match(int(row["duration_s"]), duration_s, cfg.duration_tolerance_pct):
            continue
        already = conn.execute(
            "SELECT 1 FROM activity_sources WHERE activity_id = ? AND source_app = ?",
            (row["id"], source_app),
        ).fetchone()
        if already is None:
            return str(row["id"])
    return None
