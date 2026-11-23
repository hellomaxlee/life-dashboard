"""Home-timezone days and Monday-to-Sunday weeks (notes.txt § Goal model, "Home timezone")."""

from __future__ import annotations

import calendar as _calendar
from collections.abc import Iterator
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.timeutil import from_utc_iso, to_utc_iso


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def next_week_start(day: date) -> date:
    return week_start(day) + timedelta(days=7)


def days_between(first: date, last: date) -> Iterator[date]:
    day = first
    while day <= last:
        yield day
        day += timedelta(days=1)


def weeks_between(first: date, last: date) -> Iterator[date]:
    week = week_start(first)
    while week <= last:
        yield week
        week += timedelta(days=7)


def add_months(day: date, months: int) -> date:
    """Calendar months, the day of month clamped to the target month's length."""
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(day.day, _calendar.monthrange(year, month)[1]))


def local_date(utc_iso: str, tz: str) -> date:
    return from_utc_iso(utc_iso).astimezone(ZoneInfo(tz)).date()


def local_midnight_utc(day: date, tz: str) -> datetime:
    return datetime.combine(day, time.min, ZoneInfo(tz))


def local_midnight_iso(day: date, tz: str) -> str:
    return to_utc_iso(local_midnight_utc(day, tz))


def end_of_day_utc(day: date, tz: str) -> datetime:
    return local_midnight_utc(day + timedelta(days=1), tz) - timedelta(seconds=1)
