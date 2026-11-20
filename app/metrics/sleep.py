"""Last night's sleep for a wake day (notes.txt § Goal model, "Sleep"; ruling Bartek 2026-10-02).

A night arrives as one or more fragments: the parser splits a night at a gap of more than an
hour awake. The wake day's sleep is the sum of `asleep_s` over its night fragments, the
sessions that start between NIGHT_FROM local the evening before and NIGHT_UNTIL local on
the wake day. A session starting outside that window is a nap and counts for nothing. When
two sources report the same night, each source's night is summed on its own and the larger
is taken, so one night is never counted twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.timeutil import from_utc_iso

NIGHT_FROM = time(18, 0)
NIGHT_UNTIL = time(12, 0)


@dataclass(frozen=True)
class SleepFragment:
    wake_day: date
    start_utc: str
    asleep_s: int
    source: str
    end_utc: str | None = None


def in_night_window(fragment: SleepFragment, tz: str) -> bool:
    zone = ZoneInfo(tz)
    start = from_utc_iso(fragment.start_utc).astimezone(zone)
    opens = datetime.combine(fragment.wake_day - timedelta(days=1), NIGHT_FROM, zone)
    closes = datetime.combine(fragment.wake_day, NIGHT_UNTIL, zone)
    return opens <= start < closes


def night_seconds(fragments: list[SleepFragment], tz: str) -> dict[date, int]:
    """Seconds asleep per wake day, nights only; a day with only naps is absent."""
    per_source: dict[tuple[date, str], int] = {}
    for fragment in fragments:
        if in_night_window(fragment, tz):
            key = (fragment.wake_day, fragment.source)
            per_source[key] = per_source.get(key, 0) + fragment.asleep_s
    nights: dict[date, int] = {}
    for (day, _), seconds in per_source.items():
        nights[day] = max(nights.get(day, 0), seconds)
    return nights


def night_spans(fragments: list[SleepFragment], tz: str) -> dict[date, tuple[datetime, datetime]]:
    """First start and last end of each wake day's night fragments, over every source."""
    spans: dict[date, tuple[datetime, datetime]] = {}
    for fragment in fragments:
        if fragment.end_utc is None or not in_night_window(fragment, tz):
            continue
        start, end = from_utc_iso(fragment.start_utc), from_utc_iso(fragment.end_utc)
        known = spans.get(fragment.wake_day)
        spans[fragment.wake_day] = (
            (min(known[0], start), max(known[1], end)) if known else (start, end)
        )
    return spans
