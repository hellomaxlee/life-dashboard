"""Weekly count, week closure and the weeks-hit streak (notes.txt § Goal model, "Weekly
target", "Weeks-hit streak", "Pull schedule").

A week closes after the first health push following its Monday 00:00 local, or, with no
push by then, `week_close_grace_hours` after it. Until it closes the week is in progress:
`week_hit` is null and the streak neither extends nor breaks. A closed week is scored from
whatever is stored at recompute time, so a Sunday workout that lands with Monday's push
re-opens and re-scores the week.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.metrics.calendar import local_midnight_utc


@dataclass(frozen=True)
class WeekInput:
    week_start: date
    quality_ends_utc: tuple[str, ...]
    load_bar: float
    load_bar_source: str


@dataclass(frozen=True)
class WeekRow:
    week_start: str
    quality_workouts: int
    week_hit: bool | None
    weeks_hit_streak: int
    week_complete_at: str | None
    load_bar: float
    load_bar_source: str

    def as_json(self) -> dict[str, object]:
        return {
            "quality_workouts": self.quality_workouts,
            "week_hit": self.week_hit,
            "weeks_hit_streak": self.weeks_hit_streak,
            "week_complete_at": self.week_complete_at,
            "load_bar": self.load_bar,
            "load_bar_source": self.load_bar_source,
        }


def week_closed(
    week_start: date, now: datetime, pushes_utc: list[datetime], grace_hours: float, tz: str
) -> bool:
    end = local_midnight_utc(week_start + timedelta(days=7), tz)
    if now < end:
        return False
    if any(push >= end for push in pushes_utc):
        return True
    return now >= end + timedelta(hours=grace_hours)


def week_rows(
    weeks: list[WeekInput],
    target: int,
    now: datetime,
    pushes_utc: list[datetime],
    grace_hours: float,
    tz: str,
) -> list[WeekRow]:
    """Rows in chronological order; `weeks` must be chronological and contiguous."""
    rows: list[WeekRow] = []
    streak = 0
    for week in weeks:
        ends = sorted(week.quality_ends_utc)
        count = len(ends)
        complete_at = ends[target - 1] if count >= target else None
        if week_closed(week.week_start, now, pushes_utc, grace_hours, tz):
            hit = count >= target
            streak = streak + 1 if hit else 0
        else:
            hit = None
        rows.append(
            WeekRow(
                week.week_start.isoformat(),
                count,
                hit,
                streak,
                complete_at,
                week.load_bar,
                week.load_bar_source,
            )
        )
    return rows
