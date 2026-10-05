"""Table `month_features`: the stored feature per home-timezone month, and
`month_feature_attempts`: the runs that called the model and stored nothing.

A row is written only after `spec.parse_feature` accepted the object, and is parsed again on
every load, so a row the current spec no longer accepts is never drawn.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime

from app.month.spec import MonthFeature, SpecError, parse_feature
from app.timeutil import now_utc, to_utc_iso

log = logging.getLogger(__name__)
_unparseable_logged: set[tuple[str, str]] = set()


def load_feature(conn: sqlite3.Connection, month: str) -> MonthFeature | None:
    """The stored feature for `month` (YYYY-MM), or None when there is none or the stored
    row no longer parses (logged once per row per process)."""
    row = conn.execute(
        "SELECT feature_json, created_at_utc FROM month_features WHERE month_local = ?", (month,)
    ).fetchone()
    if row is None:
        return None
    try:
        return parse_feature(json.loads(row["feature_json"]), month)
    except (SpecError, TypeError, ValueError) as exc:
        seen = (month, str(row["created_at_utc"]))
        if seen not in _unparseable_logged:
            _unparseable_logged.add(seen)
            log.error("month feature %s: the stored row no longer parses: %s", month, exc)
        return None


def save_feature(
    conn: sqlite3.Connection,
    month: str,
    raw: dict,
    source: str,
    model: str | None,
    raw_reply: str | None,
    now: datetime | None = None,
) -> MonthFeature:
    """Validate `raw` and store it as the feature for `month`, replacing any earlier one.
    Raises SpecError, with nothing written, when it does not parse."""
    feature = parse_feature(raw, month)
    conn.execute(
        "INSERT INTO month_features (month_local, feature_json, source, model, raw_reply, "
        "created_at_utc) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (month_local) DO UPDATE SET feature_json = excluded.feature_json, "
        "source = excluded.source, model = excluded.model, raw_reply = excluded.raw_reply, "
        "created_at_utc = excluded.created_at_utc",
        (
            month,
            json.dumps(raw, sort_keys=True),
            source,
            model,
            raw_reply,
            to_utc_iso(now or now_utc()),
        ),
    )
    return feature


def feature_source(conn: sqlite3.Connection, month: str) -> tuple[str, str | None] | None:
    """(source, model) of the stored row, or None."""
    row = conn.execute(
        "SELECT source, model FROM month_features WHERE month_local = ?", (month,)
    ).fetchone()
    return None if row is None else (row["source"], row["model"])


def previous_themes(
    conn: sqlite3.Connection, before_month: str, limit: int = 12
) -> list[tuple[str, str, str]]:
    """(month, title, theme) of the newest `limit` features before `before_month`, oldest
    first. Rows that no longer parse are left out."""
    rows = conn.execute(
        "SELECT month_local FROM month_features WHERE month_local < ? "
        "ORDER BY month_local DESC LIMIT ?",
        (before_month, limit),
    ).fetchall()
    found = []
    for row in reversed(rows):
        feature = load_feature(conn, row["month_local"])
        if feature is not None:
            found.append((feature.month, feature.title, feature.theme))
    return found


def record_attempt(
    conn: sqlite3.Connection,
    month: str,
    day_local: str,
    calls: int,
    reason: str,
    now: datetime | None = None,
) -> None:
    conn.execute(
        "INSERT INTO month_feature_attempts (month_local, day_local, calls, reason, "
        "created_at_utc) VALUES (?, ?, ?, ?, ?)",
        (month, day_local, calls, reason, to_utc_iso(now or now_utc())),
    )


def attempts(conn: sqlite3.Connection, month: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT day_local, calls, reason, created_at_utc FROM month_feature_attempts "
        "WHERE month_local = ? ORDER BY id",
        (month,),
    ).fetchall()
