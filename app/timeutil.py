from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

UTC_ISO = "%Y-%m-%dT%H:%M:%SZ"
_HAE_FORMATS = ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S")


def parse_hae_datetime(value: str, tz: str) -> datetime:
    """Parse a Health Auto Export date such as '2024-02-06 07:00:00 -0800' to an aware datetime.

    A bare date ('2024-02-06') or an offset-less timestamp is read in the home timezone.
    """
    text = value.strip()
    if len(text) == 10:
        return datetime.combine(date.fromisoformat(text), datetime.min.time(), ZoneInfo(tz))
    for fmt in _HAE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(tz))
        return parsed
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(tz))
    return parsed


def to_utc_iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(UTC_ISO)


def from_utc_iso(text: str) -> datetime:
    return datetime.strptime(text, UTC_ISO).replace(tzinfo=UTC)


def local_day(moment: datetime, tz: str) -> str:
    return moment.astimezone(ZoneInfo(tz)).date().isoformat()


def now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def clock_12h(moment: datetime, compact: bool = False) -> str:
    """The wall clock as Max reads it: "1:30PM", never 24-hour. `compact` gives the one-letter
    suffix the city's hour labels use ("1:30P"), for a line that must fit beside a weekday."""
    suffix = "AM" if moment.hour < 12 else "PM"
    hour = moment.hour % 12 or 12
    return f"{hour}:{moment.minute:02d}{suffix[0] if compact else suffix}"


def utc_iso_to_local_display(text: str, tz: str) -> str:
    local = from_utc_iso(text).astimezone(ZoneInfo(tz))
    return f"{local:%Y-%m-%d} {clock_12h(local)} {local:%Z}"
