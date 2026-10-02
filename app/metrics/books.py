"""Books this year (notes.txt § Goal model, "Reading").

A book's effective date is `read_at`; when that is empty and `books.fallback_to_date_added`
is on, `date_added`. Dates arrive as YYYY-MM-DD, as an ISO datetime (an offset is converted
to the home timezone) or as an RFC 2822 string (the Goodreads RSS form); anything else is
no date, and a book without one is never counted.
"""

from __future__ import annotations

from datetime import date, datetime
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

from app.config import Settings


def parse_book_date(text: str | None, tz: str) -> date | None:
    if not isinstance(text, str) or not text.strip():
        return None
    value = text.strip()
    if len(value) == 10:
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(ZoneInfo(tz))
    return parsed.date()


def effective_date(read_at: str | None, date_added: str | None, settings: Settings) -> date | None:
    finished = parse_book_date(read_at, settings.home_tz)
    if finished is not None:
        return finished
    if settings.books.fallback_to_date_added:
        return parse_book_date(date_added, settings.home_tz)
    return None


def books_ytd(dates: list[date], day: date) -> int:
    return sum(1 for d in dates if d.year == day.year and d <= day)


def finished_on(dates: list[date], day: date) -> bool:
    return any(d == day for d in dates)
