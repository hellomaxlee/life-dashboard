"""Table `manual_workouts`: a day credited with a quality workout by hand (notes.txt
§ Architecture assumptions, Manual workout override).

The override is credit by assertion: no heart rate, no zones, no load figure. The engine
marks the day `manual_workout` and gives it the dot when no scored activity already earned
it; a real workout that later lands on the same day merges with the override and never adds
a second dot. The table is authored data: `tools.replay --verify` copies it into the scratch
db before recomputing and never compares it, `--rebuild-live` leaves it alone, and a backup
carries it like every table.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime

from app.config import Settings
from app.timeutil import local_day, now_utc, to_utc_iso

TABLE = "manual_workouts"
NOTE_MAX = 200
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class Override:
    day_local: str
    note: str
    created_at_utc: str


def parse_day(text: str, settings: Settings, now: datetime | None = None) -> str:
    """A canonical YYYY-MM-DD no later than today (home timezone); ValueError otherwise."""
    cleaned = text.strip()
    if not _DAY.match(cleaned):
        raise ValueError(f"not a YYYY-MM-DD date: {cleaned!r}")
    day = date.fromisoformat(cleaned).isoformat()
    today = local_day(now or now_utc(), settings.home_tz)
    if day > today:
        raise ValueError(f"{day} is after today ({today}); an override records a day that happened")
    return day


def clean_note(text: str) -> str:
    return " ".join(text.split())[:NOTE_MAX]


def add_override(
    conn: sqlite3.Connection, day_local: str, note: str, now: datetime | None = None
) -> Override:
    """Record the day; a second add for the same day replaces the note and the time."""
    row = Override(day_local, clean_note(note), to_utc_iso(now or now_utc()))
    conn.execute(
        f"INSERT INTO {TABLE} (day_local, note, created_at_utc) VALUES (?, ?, ?) "
        "ON CONFLICT (day_local) DO UPDATE SET note = excluded.note, "
        "created_at_utc = excluded.created_at_utc",
        (row.day_local, row.note, row.created_at_utc),
    )
    return row


def remove_override(conn: sqlite3.Connection, day_local: str) -> bool:
    cursor = conn.execute(f"DELETE FROM {TABLE} WHERE day_local = ?", (day_local,))
    return cursor.rowcount > 0


def list_overrides(conn: sqlite3.Connection) -> list[Override]:
    """Newest day first."""
    return [
        Override(row["day_local"], row["note"], row["created_at_utc"])
        for row in conn.execute(
            f"SELECT day_local, note, created_at_utc FROM {TABLE} ORDER BY day_local DESC"
        )
    ]


def read_overrides(conn: sqlite3.Connection) -> dict[date, Override]:
    """The engine's input: every override keyed by its home-timezone day."""
    return {date.fromisoformat(row.day_local): row for row in list_overrides(conn)}
