"""The city panel's outbound calls: MTA service alerts, the Open-Meteo forecast and National
Weather Service alerts. All three are public and need no key.

What leaves the house: nothing but the request for the MTA feeds; latitude and longitude
rounded to two decimals for the two weather calls; a fixed User-Agent with no name or email.
No health data, no address.

Every request goes through `get_json`: https only, host in ALLOWED_HOSTS, redirects refused
whatever their target, proxies and other environment settings ignored (`trust_env=False`),
short timeouts, and a reply read no further than MAX_BYTES. Every failure is a FetchError.

`poll_transit` and `poll_weather` fetch, parse and replace the stored snapshot. A fetch or a
reply that fails stores nothing, so the last good snapshot stays. The feeds are display-only
and are not archived under data/raw (workflows/city.md).
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.city import store
from app.city.model import LineStatus, Weather
from app.city.parse import ParseError, all_lines, line_statuses, parse_weather, weather_alerts
from app.config import Settings
from app.timeutil import from_utc_iso, now_utc, to_utc_iso

ALLOWED_HOSTS = frozenset({"api-endpoint.mta.info", "api.open-meteo.com", "api.weather.gov"})
SUBWAY_ALERTS_URL = (
    "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Fsubway-alerts.json"
)
BUS_ALERTS_URL = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Fbus-alerts.json"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_ALERTS_URL = "https://api.weather.gov/alerts/active"
USER_AGENT = "life-dashboard (personal use)"
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
TOTAL_SECONDS = 25.0
MAX_BYTES = 4 * 1024 * 1024

CURRENT_FIELDS = "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation"
HOURLY_FIELDS = "temperature_2m,precipitation_probability,precipitation,weather_code,wind_speed_10m"
DAILY_FIELDS = (
    "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code,"
    "sunrise,sunset"
)


class FetchError(RuntimeError):
    """A feed could not be fetched from its one allowed place."""


def new_client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, trust_env=False, follow_redirects=False)


def coordinate(value: float) -> str:
    """A latitude or longitude as it is sent: two decimals, about a kilometre."""
    return f"{value:.2f}"


def forecast_params(latitude: float, longitude: float, home_tz: str) -> dict[str, str]:
    return {
        "latitude": coordinate(latitude),
        "longitude": coordinate(longitude),
        "timezone": home_tz,
        "forecast_days": "2",
        "current": CURRENT_FIELDS,
        "hourly": HOURLY_FIELDS,
        "daily": DAILY_FIELDS,
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
    }


def weather_alert_params(latitude: float, longitude: float) -> dict[str, str]:
    return {"point": f"{coordinate(latitude)},{coordinate(longitude)}"}


def _read_capped(response: httpx.Response, host: str) -> bytes:
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BYTES:
        raise FetchError(f"{host}: reply declares {declared} bytes, over the {MAX_BYTES} cap")
    deadline = time.monotonic() + TOTAL_SECONDS
    body = bytearray()
    for chunk in response.iter_bytes():
        body += chunk
        if len(body) > MAX_BYTES:
            raise FetchError(f"{host}: reply is over the {MAX_BYTES} byte cap")
        if time.monotonic() > deadline:
            raise FetchError(f"{host}: reply took longer than {TOTAL_SECONDS:.0f} s")
    return bytes(body)


def get_json(
    url: str, params: dict[str, str] | None = None, client: httpx.Client | None = None
) -> object:
    """GET one of the allowed feeds and decode it. The only outbound call of the city panel.

    Raises FetchError, before any I/O, for a URL that is not https on an allowed host; and
    FetchError for a redirect, a status other than 200, a transport failure or timeout, a
    reply over MAX_BYTES and a reply that is not JSON.
    """
    try:
        target = httpx.URL(url)
    except httpx.InvalidURL as exc:
        raise FetchError(f"not a URL: {exc}") from exc
    host = target.host
    if target.scheme != "https" or host not in ALLOWED_HOSTS:
        raise FetchError(f"refusing to fetch from {host!r}: not an allowed https host")
    own = client is None
    session = client or new_client()
    try:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        request = session.build_request("GET", target, params=params, headers=headers)
        try:
            response = session.send(request, stream=True, follow_redirects=False)
        except httpx.HTTPError as exc:
            raise FetchError(f"{host}: {type(exc).__name__}: {exc}") from exc
        try:
            if response.is_redirect or response.next_request is not None:
                raise FetchError(f"{host}: refusing redirect {response.status_code}")
            if response.url.host not in ALLOWED_HOSTS:
                raise FetchError(f"reply came from {response.url.host!r}, not {host}")
            if response.status_code != 200:
                raise FetchError(f"{host}: answered {response.status_code}")
            try:
                body = _read_capped(response, host)
            except httpx.HTTPError as exc:
                raise FetchError(f"{host}: {type(exc).__name__}: {exc}") from exc
        finally:
            response.close()
    finally:
        if own:
            session.close()
    try:
        return json.loads(body)
    except ValueError as exc:
        raise FetchError(f"{host}: reply is not JSON") from exc


def poll_transit(
    conn: sqlite3.Connection,
    settings: Settings,
    client: httpx.Client | None = None,
    now: datetime | None = None,
) -> tuple[LineStatus, ...]:
    """Fetch both MTA alert feeds and replace the transit snapshot with a status for every
    configured line. FetchError or ParseError: nothing is stored."""
    moment = now or now_utc()
    subway = get_json(SUBWAY_ALERTS_URL, client=client)
    bus = get_json(BUS_ALERTS_URL, client=client)
    tz = ZoneInfo(settings.home_tz)
    statuses = line_statuses(subway, bus, all_lines(settings.city), moment, tz)
    store.save_transit(conn, settings.city, statuses, moment)
    return statuses


def _carried_alerts(
    conn: sqlite3.Connection, settings: Settings, moment: datetime
) -> tuple[tuple[str, ...], str | None]:
    """The stored weather alerts while they are younger than `city.stale_minutes`."""
    alerts, stamp = store.stored_weather_alerts(conn)
    if stamp is None:
        return (), None
    try:
        age = moment - from_utc_iso(stamp)
    except ValueError:
        return (), None
    fresh = age <= timedelta(minutes=settings.city.stale_minutes)
    return (alerts, stamp) if fresh else ((), None)


def poll_weather(
    conn: sqlite3.Connection,
    settings: Settings,
    client: httpx.Client | None = None,
    now: datetime | None = None,
) -> Weather:
    """Fetch the forecast and the weather alerts and replace the weather snapshot.

    A failed forecast stores nothing. A failed alerts fetch still stores the new forecast,
    with the alerts of the stored snapshot while those are younger than `city.stale_minutes`
    and none after that, and then raises, so the job logs that the alerts are not current.
    """
    moment = now or now_utc()
    city = settings.city
    tz = ZoneInfo(settings.home_tz)
    params = forecast_params(city.latitude, city.longitude, settings.home_tz)
    forecast = get_json(FORECAST_URL, params, client)
    weather = parse_weather(forecast, None, moment, tz)
    try:
        nws = get_json(
            WEATHER_ALERTS_URL, weather_alert_params(city.latitude, city.longitude), client
        )
        alerts, alerts_at = weather_alerts(nws, moment, tz), to_utc_iso(moment)
        failure: Exception | None = None
    except (FetchError, ParseError) as exc:
        alerts, alerts_at = _carried_alerts(conn, settings, moment)
        failure = exc
    weather = replace(weather, alerts=alerts)
    store.save_weather(conn, weather, moment, alerts_at)
    if failure is not None:
        raise FetchError(f"weather alerts not refreshed (forecast stored): {failure}") from failure
    return weather
