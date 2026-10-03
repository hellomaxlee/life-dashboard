"""How anything downstream reads activities: active rows only, one HR series each.

A workout Health no longer reports is kept with `withdrawn_at` set (issue #4). The metrics
engine, the view and every count must read through `active_activities_sql` (or apply
`ACTIVE_CLAUSE` themselves) and `active_hr_samples`; a withdrawn activity is provenance, not
progress.

HR samples are stored per copy (`external_id`, `source_app`), because a cluster can split or
re-key. An activity's HR series is the series of the one sample source with the most distinct
timestamps (tie: the lowest source name), one sample per timestamp, the same rule that sets
`activities.hr_sample_count`. Two copies of one workout therefore never add their samples
together.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

ACTIVE_CLAUSE = "withdrawn_at IS NULL"


@dataclass(frozen=True)
class HrSampleRow:
    ts_utc: str
    bpm_min: float | None
    bpm_avg: float | None
    bpm_max: float | None
    source: str


def active_activities_sql(columns: str = "*", where: str = "", order: str = "start_utc") -> str:
    """SELECT over `activities` restricted to rows Health still reports."""
    extra = f" AND ({where})" if where else ""
    return f"SELECT {columns} FROM activities WHERE {ACTIVE_CLAUSE}{extra} ORDER BY {order}"


def active_activities(
    conn: sqlite3.Connection, start_utc: str | None = None, end_utc: str | None = None
) -> list[sqlite3.Row]:
    """Active activities that start in [start_utc, end_utc), both optional, by start."""
    clauses, params = [], []
    if start_utc is not None:
        clauses.append("start_utc >= ?")
        params.append(start_utc)
    if end_utc is not None:
        clauses.append("start_utc < ?")
        params.append(end_utc)
    return conn.execute(active_activities_sql(where=" AND ".join(clauses)), params).fetchall()


def active_hr_samples(conn: sqlite3.Connection) -> dict[str, list[HrSampleRow]]:
    """Each active activity's HR series, by activity id, in timestamp order."""
    rows = conn.execute(
        "SELECT c.activity_id, s.source, s.ts_utc, MIN(s.bpm_min) AS bpm_min, "
        "AVG(s.bpm_avg) AS bpm_avg, MAX(s.bpm_max) AS bpm_max "
        "FROM workout_hr_samples s "
        "JOIN activity_sources c ON c.external_id = s.external_id AND c.source_app = s.source_app "
        "JOIN activities a ON a.id = c.activity_id "
        "WHERE a.withdrawn_at IS NULL AND c.withdrawn_at IS NULL "
        "GROUP BY c.activity_id, s.source, s.ts_utc ORDER BY c.activity_id, s.source, s.ts_utc"
    ).fetchall()
    by_source: dict[str, dict[str, list[HrSampleRow]]] = {}
    for r in rows:
        series = by_source.setdefault(r["activity_id"], {}).setdefault(r["source"], [])
        row = HrSampleRow(r["ts_utc"], r["bpm_min"], r["bpm_avg"], r["bpm_max"], r["source"])
        series.append(row)
    return {
        activity_id: min(sources.items(), key=lambda item: (-len(item[1]), item[0]))[1]
        for activity_id, sources in by_source.items()
    }
