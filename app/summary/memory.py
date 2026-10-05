"""The recent-lines memory: table `summary_lines`, one row per day."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from app.summary.gate import (
    RECENT_OPENING_LINES,
    RECENT_SIMILARITY_LINES,
    SOURCE_WINDOW_LINES,
    Recent,
    sources_in,
)
from app.timeutil import now_utc, to_utc_iso


@dataclass(frozen=True)
class StoredLine:
    day_local: str
    line: str
    web_line: str | None
    lens: str
    source: str
    gate_result: str
    attempts: list[dict]


def stored_line(conn: sqlite3.Connection, day_local: str) -> StoredLine | None:
    row = conn.execute("SELECT * FROM summary_lines WHERE day_local = ?", (day_local,)).fetchone()
    if row is None:
        return None
    return StoredLine(
        row["day_local"],
        row["line"],
        row["web_line"],
        row["lens"],
        row["source"],
        row["gate_result"],
        json.loads(row["attempts_json"]),
    )


def recent_before(conn: sqlite3.Connection, day_local: str) -> Recent:
    """What the gate remembers on `day_local`: lines strictly before it, newest first."""
    rows = conn.execute(
        "SELECT day_local, line FROM summary_lines WHERE day_local < ? "
        "ORDER BY day_local DESC LIMIT ?",
        (day_local, max(RECENT_OPENING_LINES, RECENT_SIMILARITY_LINES, SOURCE_WINDOW_LINES)),
    ).fetchall()
    lines = tuple(r["line"] for r in rows)
    sources: list[str] = []
    for line in lines[:SOURCE_WINDOW_LINES]:
        sources.extend(sources_in(line))
    return Recent(
        opening_lines=lines[:RECENT_OPENING_LINES],
        similarity_lines=lines[:RECENT_SIMILARITY_LINES],
        sources_named=tuple(dict.fromkeys(sources)),
    )


def remember(
    conn: sqlite3.Connection,
    day_local: str,
    line: str,
    web_line: str | None,
    lens: str,
    source: str,
    gate_result: str,
    attempts: list[dict],
) -> None:
    conn.execute(
        "INSERT INTO summary_lines "
        "(day_local, line, web_line, lens, source, gate_result, attempts_json, written_at_utc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (day_local) DO UPDATE SET line = excluded.line, web_line = excluded.web_line, "
        "lens = excluded.lens, source = excluded.source, gate_result = excluded.gate_result, "
        "attempts_json = excluded.attempts_json, written_at_utc = excluded.written_at_utc",
        (
            day_local,
            line,
            web_line,
            lens,
            source,
            gate_result,
            json.dumps(attempts),
            to_utc_iso(now_utc()),
        ),
    )
