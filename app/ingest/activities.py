"""How anything downstream reads activities: active rows only.

A workout Health no longer reports is kept with `withdrawn_at` set (issue #4). The metrics
engine, the view and every count must read through `active_activities_sql` or apply
`ACTIVE_CLAUSE` themselves; a withdrawn activity is provenance, not progress.
"""

from __future__ import annotations

import sqlite3

ACTIVE_CLAUSE = "withdrawn_at IS NULL"


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
