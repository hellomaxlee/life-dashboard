"""DayView: everything the screens show, as plain typed values.

The renderer never queries or computes a metric. It is handed a DayView, and every field
that is None is drawn as a stated fallback ("NO DATA"), never as a blank and never as a
guess. Two builders make one: `load_fixture` here (a hand-written file under fixtures/days/)
and `view_from_db` in view_db.py (what is stored for a date). Both go through
`view_from_metrics`, so a fixture's `daily_metrics` / `weekly_metrics` objects have the same
shape as the table rows. This module imports no database or ingest code.

`month_feature` is the stored month feature (app/month/spec.py) for the requested day's
month, or None; the Month screen then draws its calendar. view_db reads it from the
database. A fixture names a file under fixtures/month with a `month_feature` key; that
sample is re-dated to the fixture's own month (its days cut to the month's length), so one
hand-made sample serves every fixture day. Only fixtures do that; a stored feature is
shown for its own month and no other.

`city` is the city status (app/city/model.py: weather and transit lines) for the requested
day, or None; the City screen then says "CITY NO DATA". view_db reads it from the database. A
fixture names a file under fixtures/city_view with a `city` key (see `fixture_city`); that
sample is dated to the fixture's own day and its fetch times are counted back from the
fixture's "now", so one sample serves any fixture day. `city_stale_minutes` is how old a
fetch may be before its page says "AS OF".

Keys read. Phase 2 (metrics) and Phase 3 (summary) are expected to write the ones marked
with their phase; until they do, the screens show the fallback.

daily_metrics.metrics_json, row keyed by the home-timezone day:
  quality_workout          bool   a quality workout landed that day (Phase 2)
  sleep_hours              float  hours asleep in the session that woke that day (Phase 2).
                                  When present and valid it takes precedence; only when it is
                                  absent does view_db fall back, as an interim, to the longest
                                  `sleep_sessions.asleep_s` for the wake day
  steps                    int    (Phase 2, optional); absent: `steps_daily.steps`
  books_ytd                int    books read in the row's calendar year, as of that day (Phase 2)
  summary_device_line      str    the one-sentence device summary, at most 110 chars (Phase 3)
  claude_week_used_pct     float  seven-day window, 0 to 100 (written today by ingest)
  claude_week_resets_at    str    UTC ISO or null (written today by ingest)
  claude_week_captured_at  str    UTC ISO (written today by ingest)

weekly_metrics.metrics_json, row keyed by the Monday that starts the week:
  quality_workouts         int    quality workouts so far that week (Phase 2)
  weeks_hit_streak         int    consecutive completed weeks at target, as of that week (Phase 2)

Ranges. A value of the wrong type or outside its range is a missing value, drawn as NO DATA,
never clamped into a plausible-looking number:
  claude_week_used_pct   0 to 100 inclusive, no tolerance: the feed documents 0 to 100, so
                         100.5 is a broken reading, not a full one
  sleep_hours            0 to 24 inclusive
  counts (steps, books_ytd, quality_workouts, weeks_hit_streak)   whole numbers, 0 or more
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.city.model import KINDS, CityStatus, HourWeather, LineStatus, Weather
from app.config import Settings
from app.month.spec import MonthFeature, SpecError, days_in, parse_feature
from app.timeutil import from_utc_iso, to_utc_iso

QUALITY_WORKOUT = "quality_workout"
BOOK_FINISHED = "book_finished_today"
WORKOUT_COUNT = "workout_count"
WORKOUT_LOAD = "workout_load"
WEEK_LOAD_BAR = "load_bar"
SLEEP_HOURS = "sleep_hours"
STEPS = "steps"
BOOKS_YTD = "books_ytd"
SUMMARY_LINE = "summary_device_line"
CLAUDE_USED_PCT = "claude_week_used_pct"
CLAUDE_RESETS_AT = "claude_week_resets_at"
CLAUDE_CAPTURED_AT = "claude_week_captured_at"
WEEK_QUALITY_WORKOUTS = "quality_workouts"
WEEKS_HIT_STREAK = "weeks_hit_streak"

MAX_SLEEP_HOURS = 24.0
CITY_STALE_MINUTES = 45


@dataclass(frozen=True)
class ClaudeUsage:
    used_pct: float | None = None
    resets_at_utc: str | None = None
    captured_at_utc: str | None = None


@dataclass(frozen=True)
class DayView:
    day_local: str
    home_tz: str = "America/New_York"
    week_target: int = 3
    week_dots: int | None = None
    streak_weeks: int | None = None
    today_dot: bool | None = None
    workout_count: int | None = None
    workout_load: float | None = None
    load_bar: float | None = None
    day_shown: str | None = None
    book_finished: bool = False
    sleep_hours: float | None = None
    sleep_target_hours: float = 7.0
    sleep_max_hours: float = 9.0
    steps: int | None = None
    as_of_utc: str | None = None
    books_ytd: int | None = None
    books_target: int = 12
    summary_line: str | None = None
    claude: ClaudeUsage = field(default_factory=ClaudeUsage)
    stale_hours: int = 24
    month_feature: MonthFeature | None = None
    city: CityStatus | None = None
    city_stale_minutes: int = CITY_STALE_MINUTES


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def valid_percent(value: float | None) -> bool:
    return value is not None and math.isfinite(value) and 0.0 <= value <= 100.0


def valid_sleep_hours(value: float | None) -> bool:
    return value is not None and math.isfinite(value) and 0.0 <= value <= MAX_SLEEP_HOURS


def has_health_data(view: DayView) -> bool:
    """A push has covered this day: it has sleep, steps or a workout. A row with only
    `workout_count` 0 says nothing; the engine writes that before any push arrives."""
    return (
        valid_sleep_hours(view.sleep_hours)
        or valid_count(view.steps)
        or view.today_dot is True
        or bool(view.workout_count)
    )


def valid_count(value: int | None) -> bool:
    return value is not None and not isinstance(value, bool) and value >= 0


def _whole(value: Any) -> int | None:
    number = _number(value)
    if number is None or number < 0 or number != int(number):
        return None
    return int(number)


def _sleep(value: Any) -> float | None:
    number = _number(value)
    return number if valid_sleep_hours(number) else None


def _utc_iso(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return to_utc_iso(from_utc_iso(value))
    except ValueError:
        return None


def claude_from_metrics(daily: dict[str, Any]) -> ClaudeUsage:
    """The seven-day keys and nothing else; a row without a valid percent is no reading."""
    used = _number(daily.get(CLAUDE_USED_PCT))
    if not valid_percent(used):
        return ClaudeUsage()
    return ClaudeUsage(
        used, _utc_iso(daily.get(CLAUDE_RESETS_AT)), _utc_iso(daily.get(CLAUDE_CAPTURED_AT))
    )


def city_stale_minutes(settings: Settings) -> int:
    """`[city] stale_minutes`, or CITY_STALE_MINUTES when the settings carry none."""
    value = getattr(getattr(settings, "city", None), "stale_minutes", None)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return CITY_STALE_MINUTES
    return value


def week_start(day_local: str) -> str:
    day = date.fromisoformat(day_local)
    return (day - timedelta(days=day.weekday())).isoformat()


def view_from_metrics(
    day_local: str,
    daily: dict[str, Any],
    weekly: dict[str, Any],
    settings: Settings,
    *,
    as_of_utc: str | None = None,
    stored_sleep_hours: float | None = None,
    stored_steps: int | None = None,
    claude: ClaudeUsage | None = None,
) -> DayView:
    """Build the view. A `daily` value wins over its `stored_*` interim fallback.

    `day_local` is stored in canonical YYYY-MM-DD form; ValueError if it is not a date.
    """
    day_local = date.fromisoformat(day_local).isoformat()
    quality = daily.get(QUALITY_WORKOUT)
    summary = daily.get(SUMMARY_LINE)
    sleep = _sleep(daily.get(SLEEP_HOURS))
    steps = _whole(daily.get(STEPS))
    return DayView(
        day_local=day_local,
        home_tz=settings.home_tz,
        week_target=settings.week_target,
        week_dots=_whole(weekly.get(WEEK_QUALITY_WORKOUTS)),
        streak_weeks=_whole(weekly.get(WEEKS_HIT_STREAK)),
        today_dot=quality if isinstance(quality, bool) else None,
        workout_count=_whole(daily.get(WORKOUT_COUNT)),
        workout_load=_number(daily.get(WORKOUT_LOAD)),
        load_bar=_number(weekly.get(WEEK_LOAD_BAR)),
        book_finished=daily.get(BOOK_FINISHED) is True,
        sleep_hours=sleep if SLEEP_HOURS in daily else _sleep(stored_sleep_hours),
        sleep_target_hours=settings.sleep_target_hours,
        sleep_max_hours=settings.sleep_max_hours,
        steps=steps if steps is not None else _whole(stored_steps),
        as_of_utc=_utc_iso(as_of_utc),
        books_ytd=_whole(daily.get(BOOKS_YTD)),
        books_target=settings.books.target_per_year,
        summary_line=summary.strip() if isinstance(summary, str) and summary.strip() else None,
        claude=claude if claude is not None else claude_from_metrics(daily),
        stale_hours=settings.claude_usage.stale_hours,
        city_stale_minutes=city_stale_minutes(settings),
    )


def fixture_feature(days_file: Path, name: object, month: str) -> MonthFeature:
    """The sample feature fixtures/month/<name>.json, re-dated to `month` (YYYY-MM).

    ValueError naming the fixture if the name is not a bare file name, the file is missing, or
    the sample does not pass `spec.parse_feature`.
    """
    if not isinstance(name, str) or not name or Path(name).name != name:
        raise ValueError(f"{days_file.name}: month_feature {name!r} is not a file name")
    path = days_file.parent.parent / "month" / f"{name}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("days"), list):
            raise SpecError("not a feature object")
        return parse_feature({**raw, "month": month, "days": raw["days"][: days_in(month)]}, month)
    except (OSError, ValueError) as exc:
        raise ValueError(f"{days_file.name}: month_feature {name!r}: {exc}") from None


def _minutes_before(now: datetime, minutes: object) -> str | None:
    if minutes is None:
        return None
    if _number(minutes) is None:
        raise ValueError(f"minutes ago {minutes!r} is not a number")
    return to_utc_iso(now - timedelta(minutes=minutes))


def _fixture_weather(raw: dict, now: datetime) -> Weather:
    hours = tuple(
        HourWeather(
            hour_local=int(step["hour_local"]),
            temp_f=float(step["temp_f"]),
            precip_pct=int(step["precip_pct"]),
            code=int(step["code"]),
            tomorrow=step.get("tomorrow") is True,
        )
        for step in raw.get("hours", [])
    )
    return Weather(
        temp_f=_number(raw.get("temp_f")),
        feels_f=_number(raw.get("feels_f")),
        code=_whole(raw.get("code")),
        high_f=_number(raw.get("high_f")),
        low_f=_number(raw.get("low_f")),
        precip_pct_max=_whole(raw.get("precip_pct_max")),
        hours=hours,
        alerts=tuple(str(event) for event in raw.get("alerts", [])),
        fetched_at_utc=_minutes_before(now, raw.get("fetched_minutes_ago")),
    )


def _fixture_line(raw: dict) -> LineStatus:
    if raw["kind"] not in KINDS:
        raise ValueError(f"line kind {raw['kind']!r} is not one of {KINDS}")
    headline = raw.get("headline")
    return LineStatus(
        line=str(raw["line"]),
        kind=raw["kind"],
        status=str(raw.get("status", "ok")),
        headline=str(headline) if headline is not None else None,
        now=raw.get("now") is True,
        alerts=int(raw.get("alerts", 0)),
    )


def fixture_city(days_file: Path, name: object, day_local: str, now: datetime) -> CityStatus:
    """The sample city status fixtures/city_view/<name>.json, dated to `day_local`.

    The file holds `weather` (the Weather fields, `hours` as a list of HourWeather objects,
    and `fetched_minutes_ago`) or null, `lines` (LineStatus objects), and
    `transit_fetched_minutes_ago` or null for never fetched; minutes are counted back from
    `now`. ValueError naming the fixture if the name is not a bare file name, the file is
    missing, or it does not have that shape.
    """
    if not isinstance(name, str) or not name or Path(name).name != name:
        raise ValueError(f"{days_file.name}: city {name!r} is not a file name")
    path = days_file.parent.parent / "city_view" / f"{name}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        weather = raw.get("weather")
        return CityStatus(
            day_local=day_local,
            weather=_fixture_weather(weather, now) if weather is not None else None,
            lines=tuple(_fixture_line(line) for line in raw.get("lines", [])),
            transit_fetched_at_utc=_minutes_before(now, raw.get("transit_fetched_minutes_ago")),
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ValueError(f"{days_file.name}: city {name!r}: {type(exc).__name__}: {exc}") from None


def load_fixture(path: Path, settings: Settings) -> tuple[DayView, datetime]:
    """Read a fixtures/days file. Returns the view and the fixture's own "now" (UTC).

    ValueError naming the file if its `day_local` or `now_utc` is missing or not a real date,
    or if it names a `month_feature` or `city` sample that cannot be loaded.
    """
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError(f"{path.name}: not a JSON object")
    day_local, now_text = record.get("day_local"), record.get("now_utc")
    try:
        day_local = date.fromisoformat(day_local).isoformat()
    except (TypeError, ValueError):
        raise ValueError(f"{path.name}: day_local {day_local!r} is not a valid date") from None
    try:
        now = from_utc_iso(now_text)
    except (TypeError, ValueError):
        raise ValueError(f"{path.name}: now_utc {now_text!r} is not a UTC ISO time") from None
    view = view_from_metrics(
        day_local,
        record.get("daily_metrics") or {},
        record.get("weekly_metrics") or {},
        settings,
        as_of_utc=record.get("as_of_utc"),
    )
    if record.get("month_feature") is not None:
        feature = fixture_feature(path, record["month_feature"], day_local[:7])
        view = replace(view, month_feature=feature)
    if record.get("city") is not None:
        view = replace(view, city=fixture_city(path, record["city"], day_local, now))
    return view, now
