"""Pure parsers for the city panel: decoded feed JSON in, the contract's dataclasses out.

Shapes are the ones in fixtures/city/ (fetched 2026-10-04), not the ones in documentation:
- MTA alerts (subway and bus feeds alike): `entity[].alert` with `active_period[] {start,
  end?}` in epoch seconds (`end` absent = open-ended; the whole list may be absent),
  `informed_entity[] {agency_id, route_id}`, `header_text.translation[] {text, language}`
  and `alert["transit_realtime.mercury_alert"] {alert_type, updated_at}`. Route ids are
  bare in both feeds ("N", "Q66", "B62"); a prefixed form ("MTABC_Q66") is matched on the
  part after the last underscore in case the feed ever carries it.
- Open-Meteo forecast: `current`, `hourly` (parallel lists keyed by local
  "YYYY-MM-DDTHH:MM" times) and `daily` (parallel lists keyed by local dates).
- National Weather Service alerts: GeoJSON `features[].properties {event, severity, onset,
  effective, ends, expires}`.

Which alerts count for a line: those that name it and are in effect at some point from `now`
to the end of `now`'s home-timezone day. An alert with no active period counts as in effect.
Work that ended earlier today is over and is not shown as upcoming.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo

from app.city.model import STATUS_ORDER, HourWeather, LineStatus, Weather
from app.config import CityConfig
from app.render.font import normalize

MERCURY = "transit_realtime.mercury_alert"
HEADLINE_MAX = 120
HOUR_STEPS = 6
HOUR_SPACING = 3
MAX_WEATHER_ALERTS = 3
NWS_SEVERITY = ("Extreme", "Severe", "Moderate", "Minor", "Unknown")

PLANNED_PREFIX = "planned - "
SUSPENDED_TYPES = frozenset({"suspended", "no scheduled service", "no service"})
DELAY_TYPES = frozenset(
    {
        "delays",
        "expect delays",
        "some delays",
        "severe delays",
        "reduced service",
        "slow speeds",
    }
)
PLANNED_TYPES = frozenset(
    {
        "detour",
        "buses detoured",
        "part suspended",
        "stops skipped",
        "express to local",
        "local to express",
        "reroute",
        "trains rerouted",
        "some reroutes",
        "special schedule",
        "service change",
        "planned work",
        "multiple changes",
    }
)
IGNORED_TYPES = frozenset(
    {"station notice", "boarding change", "extra service", "information", "on or close"}
)
IGNORED_WORDS = ("elevator", "escalator")

log = logging.getLogger(__name__)
_unknown_logged: set[str] = set()


class ParseError(ValueError):
    """A reply that is not the feed it should be. Nothing is stored from it."""


@dataclass(frozen=True)
class WatchedLine:
    line: str
    kind: str


def weekend(day_local: str) -> bool:
    """Saturday and Sunday are the weekend; `day_local` is a home-timezone YYYY-MM-DD."""
    return date.fromisoformat(day_local).weekday() >= 5


def lines_for_day(
    subway: Iterable[str],
    subway_weekday: Iterable[str],
    subway_weekend: Iterable[str],
    bus: Iterable[str],
    day_local: str,
) -> tuple[WatchedLine, ...]:
    """The lines shown on `day_local`, in config order: every-day subways, then the
    weekday-only or weekend-only ones, then buses."""
    switched = subway_weekend if weekend(day_local) else subway_weekday
    found = [WatchedLine(line, "subway") for line in (*subway, *switched)]
    found += [WatchedLine(line, "bus") for line in bus]
    return tuple(dict.fromkeys(found))


def all_lines(city: CityConfig) -> tuple[WatchedLine, ...]:
    """Every configured line, whichever days it applies to."""
    subways = (*city.subway, *city.subway_weekday, *city.subway_weekend)
    found = [WatchedLine(line, "subway") for line in subways]
    found += [WatchedLine(line, "bus") for line in city.bus]
    return tuple(dict.fromkeys(found))


def classify(alert_type: object) -> str | None:
    """An MTA alert type as "suspended", "delays" or "planned"; None for a notice that does
    not change the service. A type this code has never seen is "planned" and logged once,
    so a new one is never silently dropped."""
    name = " ".join(str(alert_type or "").lower().split())
    if name in IGNORED_TYPES or any(word in name for word in IGNORED_WORDS):
        return None
    if name.startswith(PLANNED_PREFIX):
        return "suspended" if name[len(PLANNED_PREFIX) :] in SUSPENDED_TYPES else "planned"
    if name in SUSPENDED_TYPES:
        return "suspended"
    if name in DELAY_TYPES:
        return "delays"
    if name not in PLANNED_TYPES and name not in _unknown_logged:
        _unknown_logged.add(name)
        log.warning("city: unknown MTA alert type %r is shown as planned work", alert_type)
    return "planned"


def _periods(alert: dict) -> list[tuple[float, float]]:
    found = []
    for period in alert.get("active_period") or []:
        if not isinstance(period, dict):
            continue
        start, end = period.get("start"), period.get("end")
        found.append(
            (
                float(start) if isinstance(start, int | float) else float("-inf"),
                float(end) if isinstance(end, int | float) else float("inf"),
            )
        )
    return found


def in_effect(alert: dict, start: float, end: float) -> bool:
    """True when an active period overlaps [start, end), or the alert carries none."""
    periods = _periods(alert)
    return not periods or any(begin < end and finish > start for begin, finish in periods)


def in_effect_at(alert: dict, moment: float) -> bool:
    periods = _periods(alert)
    return not periods or any(begin <= moment < finish for begin, finish in periods)


def day_end(now: datetime, tz: tzinfo) -> datetime:
    """The first instant of the home-timezone day after `now`'s."""
    tomorrow = now.astimezone(tz).date() + timedelta(days=1)
    return datetime.combine(tomorrow, time(), tzinfo=tz)


def clean_headline(text: str) -> str:
    """Plain ASCII on one line, at most HEADLINE_MAX characters, cut at a word."""
    folded = unicodedata.normalize("NFKD", normalize(text)).encode("ascii", "ignore").decode()
    plain = " ".join(folded.split()).rstrip(" ,;:")
    if len(plain) <= HEADLINE_MAX:
        return plain
    room = HEADLINE_MAX - 3
    cut = plain[: room + 1]
    kept = cut[: cut.rfind(" ")] if " " in cut else plain[:room]
    return kept.rstrip(" ,;:.-") + "..."


def header_text(alert: dict) -> str | None:
    """The alert's English plain header. The "en-html" copy is used, tags removed, only when
    there is no plain one."""
    translations = (alert.get("header_text") or {}).get("translation") or []
    by_language = {
        t.get("language"): t.get("text")
        for t in reversed(translations)
        if isinstance(t, dict) and isinstance(t.get("text"), str)
    }
    if by_language.get("en"):
        return by_language["en"]
    if by_language.get("en-html"):
        return re.sub(r"<[^>]+>", " ", by_language["en-html"])
    return next((text for text in by_language.values() if text), None)


def route_names(alert: dict, kind: str) -> set[str]:
    """The rider-facing lines an alert names. "MTABC_Q66" is Q66; a subway express variant
    ("6X", "FX") is its line; a Select Bus "+" id ("Q52+") is also its plain number."""
    found: set[str] = set()
    for entity in alert.get("informed_entity") or []:
        route = entity.get("route_id") if isinstance(entity, dict) else None
        if not isinstance(route, str) or not route.strip():
            continue
        name = route.strip().rsplit("_", 1)[-1].upper()
        found.add(name)
        if kind == "subway" and len(name) == 2 and name.endswith("X"):
            found.add(name[:-1])
        if kind == "bus" and name.endswith("+"):
            found.add(name[:-1])
    return found


def feed_alerts(feed: object, name: str) -> list[dict]:
    """The alerts of one decoded MTA feed. ParseError when it is not an alerts feed, so an
    error page never reads as "no alerts"."""
    entities = feed.get("entity") if isinstance(feed, dict) else None
    if not isinstance(feed, dict) or "header" not in feed or not isinstance(entities, list):
        raise ParseError(f"the {name} reply is not an MTA alerts feed (no header and entity list)")
    alerts = [e.get("alert") for e in entities if isinstance(e, dict)]
    return [alert for alert in alerts if isinstance(alert, dict)]


@dataclass(frozen=True)
class _Ranked:
    status: str
    now: bool
    updated_at: float
    headline: str | None

    @property
    def key(self) -> tuple[int, bool, float]:
        return (STATUS_ORDER.index(self.status), self.now, self.updated_at)


def _rank(alert: dict, now: datetime) -> _Ranked | None:
    mercury = alert.get(MERCURY) if isinstance(alert.get(MERCURY), dict) else {}
    severity = classify(mercury.get("alert_type"))
    if severity is None:
        return None
    current = in_effect_at(alert, now.timestamp())
    status = severity if current else "planned"
    updated = mercury.get("updated_at")
    text = header_text(alert)
    return _Ranked(
        status=status,
        now=current,
        updated_at=float(updated) if isinstance(updated, int | float) else 0.0,
        headline=clean_headline(text) if text else None,
    )


def line_status(
    watched: WatchedLine, alerts: Iterable[dict], now: datetime, tz: tzinfo
) -> LineStatus:
    """One line's status from its own feed's alerts: the most severe alert in effect between
    `now` and the end of the day; ties go to one in effect now, then the latest updated."""
    window = (now.timestamp(), day_end(now, tz).timestamp())
    ranked = []
    for alert in alerts:
        if watched.line.upper() not in route_names(alert, watched.kind):
            continue
        if not in_effect(alert, *window):
            continue
        entry = _rank(alert, now)
        if entry is not None:
            ranked.append(entry)
    if not ranked:
        return LineStatus(watched.line, watched.kind)
    top = max(ranked, key=lambda entry: entry.key)
    return LineStatus(
        line=watched.line,
        kind=watched.kind,
        status=top.status,
        headline=top.headline,
        now=top.now,
        alerts=len(ranked),
    )


def line_statuses(
    subway_feed: object,
    bus_feed: object,
    lines: Iterable[WatchedLine],
    now: datetime,
    tz: tzinfo,
) -> tuple[LineStatus, ...]:
    """A status for each of `lines`, subway lines from the subway feed and buses from the
    bus feed. `now` is an aware datetime."""
    alerts = {
        "subway": feed_alerts(subway_feed, "subway alerts"),
        "bus": feed_alerts(bus_feed, "bus alerts"),
    }
    return tuple(line_status(watched, alerts[watched.kind], now, tz) for watched in lines)


def _at(values: object, index: int) -> object:
    return values[index] if isinstance(values, list) and 0 <= index < len(values) else None


def _float(value: object) -> float | None:
    ok = isinstance(value, int | float) and not isinstance(value, bool)
    return float(value) if ok else None


def _whole(value: object) -> int | None:
    number = _float(value)
    return None if number is None else round(number)


def forecast_hours(forecast: dict, now: datetime, tz: tzinfo) -> tuple[HourWeather, ...]:
    """HOUR_STEPS steps HOUR_SPACING hours apart, the first at `now`'s local hour. A step the
    reply has no complete hour for is left out."""
    hourly = forecast.get("hourly") if isinstance(forecast.get("hourly"), dict) else {}
    times = hourly.get("time") if isinstance(hourly.get("time"), list) else []
    index = {stamp: i for i, stamp in enumerate(times)}
    local = now.astimezone(tz).replace(minute=0, second=0, microsecond=0, tzinfo=None)
    steps = []
    for step in range(HOUR_STEPS):
        moment = local + timedelta(hours=step * HOUR_SPACING)
        at = index.get(moment.strftime("%Y-%m-%dT%H:%M"), -1)
        temp = _float(_at(hourly.get("temperature_2m"), at))
        code = _whole(_at(hourly.get("weather_code"), at))
        if temp is None or code is None:
            continue
        chance = _whole(_at(hourly.get("precipitation_probability"), at))
        steps.append(
            HourWeather(
                hour_local=moment.hour,
                temp_f=temp,
                precip_pct=chance or 0,
                code=code,
                tomorrow=moment.date() > local.date(),
            )
        )
    return tuple(steps)


def _instant(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def weather_alerts(nws: object, now: datetime, tz: tzinfo) -> tuple[str, ...]:
    """Event names of the NWS alerts in effect at some point from `now` to the end of
    `now`'s home-timezone day, most severe first, no repeats, at most MAX_WEATHER_ALERTS.
    An alert runs from `onset` (else `effective`) to `ends` (else `expires`); a missing
    bound does not exclude. The service lists an advisory hours or days before its onset,
    so one that starts tomorrow is left out and one that starts this afternoon is shown."""
    features = nws.get("features") if isinstance(nws, dict) else None
    if not isinstance(features, list):
        raise ParseError("the weather alerts reply is not GeoJSON (no features list)")
    horizon = day_end(now, tz)
    ranked = []
    for position, feature in enumerate(features):
        props = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(props, dict):
            continue
        event = props.get("event")
        if not isinstance(event, str) or not event.strip():
            continue
        begins = _instant(props.get("onset")) or _instant(props.get("effective"))
        finishes = _instant(props.get("ends")) or _instant(props.get("expires"))
        if (begins is not None and begins >= horizon) or (finishes is not None and finishes <= now):
            continue
        severity = props.get("severity")
        rank = NWS_SEVERITY.index(severity) if severity in NWS_SEVERITY else len(NWS_SEVERITY)
        ranked.append((rank, position, clean_headline(event)))
    names = dict.fromkeys(name for _, _, name in sorted(ranked))
    return tuple(names)[:MAX_WEATHER_ALERTS]


def parse_weather(forecast: object, nws: object | None, now: datetime, tz: tzinfo) -> Weather:
    """A Weather from the Open-Meteo reply and, when given, the NWS alerts reply. Today's
    high, low and precipitation chance are the `daily` entry for `now`'s local date."""
    if not isinstance(forecast, dict) or not isinstance(forecast.get("hourly"), dict):
        raise ParseError("the forecast reply is not an Open-Meteo forecast (no hourly block)")
    current = forecast.get("current") if isinstance(forecast.get("current"), dict) else {}
    daily = forecast.get("daily") if isinstance(forecast.get("daily"), dict) else {}
    days = daily.get("time") if isinstance(daily.get("time"), list) else []
    today = now.astimezone(tz).date().isoformat()
    at = days.index(today) if today in days else -1
    hours = forecast_hours(forecast, now, tz)
    if not hours:
        raise ParseError(f"the forecast reply has no hours from {today} on")
    return Weather(
        temp_f=_float(current.get("temperature_2m")),
        feels_f=_float(current.get("apparent_temperature")),
        code=_whole(current.get("weather_code")),
        high_f=_float(_at(daily.get("temperature_2m_max"), at)),
        low_f=_float(_at(daily.get("temperature_2m_min"), at)),
        precip_pct_max=_whole(_at(daily.get("precipitation_probability_max"), at)),
        hours=hours,
        alerts=() if nws is None else weather_alerts(nws, now, tz),
    )
