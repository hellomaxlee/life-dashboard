"""The city panel's outbound layer, against httpx.MockTransport. Nothing here dials."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest

from app.city import fetch, store
from app.city.fetch import FetchError, get_json, poll_transit, poll_weather
from app.city.parse import ParseError
from app.config import CityConfig
from tests.city.conftest import FIXTURES, SUNDAY_NIGHT

BODIES = {
    "/Dataservice/mtagtfsfeeds/camsys%2Fsubway-alerts.json": ("real", "subway"),
    "/Dataservice/mtagtfsfeeds/camsys%2Fbus-alerts.json": ("real", "bus"),
    "/v1/forecast": ("real", "wx"),
    "/alerts/active": ("synthetic", "nws"),
}


class Feeds:
    """Serves the fixtures; `broken` maps a host to a reply or an exception to raise."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.broken: dict[str, object] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        fault = self.broken.get(request.url.host)
        if isinstance(fault, Exception):
            raise fault
        if isinstance(fault, httpx.Response):
            return fault
        folder, name = BODIES[request.url.raw_path.decode().split("?")[0]]
        return httpx.Response(200, content=(FIXTURES / folder / f"{name}.json").read_bytes())

    def client(self, **options) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self), **options)


@pytest.fixture
def feeds() -> Feeds:
    return Feeds()


@pytest.mark.parametrize(
    "url",
    [
        "https://api.weather.com/alerts/active",
        "https://api.weather.gov.example.net/alerts/active",
        "https://example.com/Dataservice/mtagtfsfeeds/camsys%2Fsubway-alerts.json",
        "http://api.weather.gov/alerts/active",
        "http://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Fbus-alerts.json",
        "https://user@evil.example/x?host=api.open-meteo.com",
        "ftp://api.open-meteo.com/v1/forecast",
        "api.open-meteo.com/v1/forecast",
    ],
)
def test_a_url_off_the_allowlist_is_refused_before_any_io(feeds, url):
    with pytest.raises(FetchError, match="refusing to fetch"):
        get_json(url, client=feeds.client())
    assert feeds.requests == []


def test_the_allowlist_is_the_three_feeds_and_the_urls_are_on_it():
    assert fetch.ALLOWED_HOSTS == {"api-endpoint.mta.info", "api.open-meteo.com", "api.weather.gov"}
    urls = (
        fetch.SUBWAY_ALERTS_URL,
        fetch.BUS_ALERTS_URL,
        fetch.FORECAST_URL,
        fetch.WEATHER_ALERTS_URL,
    )
    for url in urls:
        assert httpx.URL(url).scheme == "https" and httpx.URL(url).host in fetch.ALLOWED_HOSTS


def test_the_client_ignores_the_environment_and_never_follows(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.example:3128")
    client = fetch.new_client()
    try:
        assert client.trust_env is False and client.follow_redirects is False
        assert client.timeout.connect == 5.0 and client.timeout.read == 10.0
        assert all(mount is None for mount in client._mounts.values())
    finally:
        client.close()


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize(
    "location",
    ["https://evil.example/feed", "https://api.weather.gov/alerts/active?x=1", "/elsewhere"],
)
def test_a_redirect_is_refused_whatever_its_target(feeds, status, location):
    feeds.broken["api.weather.gov"] = httpx.Response(status, headers={"location": location})
    with pytest.raises(FetchError, match=f"refusing redirect {status}"):
        get_json(fetch.WEATHER_ALERTS_URL, client=feeds.client(follow_redirects=True))
    assert len(feeds.requests) == 1, "the redirect was not followed"


def test_an_oversized_reply_is_refused(feeds, monkeypatch):
    monkeypatch.setattr(fetch, "MAX_BYTES", 1000)
    big = json.dumps({"entity": ["x" * 5000]}).encode()
    feeds.broken["api-endpoint.mta.info"] = httpx.Response(200, content=big)
    with pytest.raises(FetchError, match="declares .* bytes, over the 1000 cap"):
        get_json(fetch.SUBWAY_ALERTS_URL, client=feeds.client())

    served = []

    def drip():
        for _ in range(100):
            served.append(1)
            yield b" " * 100

    feeds.broken["api-endpoint.mta.info"] = httpx.Response(200, content=drip())
    with pytest.raises(FetchError, match="over the 1000 byte cap"):
        get_json(fetch.SUBWAY_ALERTS_URL, client=feeds.client())
    assert len(served) < 100, "reading stopped at the cap"

    monkeypatch.setattr(fetch, "MAX_BYTES", 4 * 1024 * 1024)
    for name in ("subway", "bus"):
        assert (FIXTURES / "real" / f"{name}.json").stat().st_size < fetch.MAX_BYTES // 4
    assert fetch.MAX_BYTES > 2 * 843_000, "tonight's full subway feed fits twice"


@pytest.mark.parametrize(
    ("fault", "message"),
    [
        (httpx.Response(500, content=b"oops"), "api.weather.gov: answered 500"),
        (httpx.Response(404, json={"title": "Not Found"}), "api.weather.gov: answered 404"),
        (httpx.Response(204), "api.weather.gov: answered 204"),
        (httpx.ReadTimeout("timed out"), "api.weather.gov: ReadTimeout: timed out"),
        (httpx.ConnectError("no route"), "api.weather.gov: ConnectError: no route"),
        (httpx.Response(200, content=b"<html>busy</html>"), "api.weather.gov: reply is not JSON"),
    ],
)
def test_every_failure_is_one_typed_error_with_a_stable_message(feeds, fault, message):
    feeds.broken["api.weather.gov"] = fault
    for _ in range(2):
        with pytest.raises(FetchError) as caught:
            get_json(fetch.WEATHER_ALERTS_URL, client=feeds.client())
        assert str(caught.value) == message


def test_what_each_request_carries(feeds, db, city_settings):
    precise = replace(
        city_settings, city=replace(city_settings.city, latitude=40.7712345, longitude=-73.9367891)
    )
    poll_transit(db, precise, feeds.client(), SUNDAY_NIGHT)
    poll_weather(db, precise, feeds.client(), SUNDAY_NIGHT)

    subway, bus, forecast, alerts = feeds.requests
    assert str(subway.url) == fetch.SUBWAY_ALERTS_URL and subway.url.query == b""
    assert str(bus.url) == fetch.BUS_ALERTS_URL and bus.url.query == b""
    assert subway.url.raw_path.endswith(b"camsys%2Fsubway-alerts.json"), "%2F stays encoded"
    assert dict(forecast.url.params) == {
        "latitude": "40.77",
        "longitude": "-73.94",
        "timezone": "America/New_York",
        "forecast_days": "2",
        "current": fetch.CURRENT_FIELDS,
        "hourly": fetch.HOURLY_FIELDS,
        "daily": fetch.DAILY_FIELDS,
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
    }
    assert dict(alerts.url.params) == {"point": "40.77,-73.94"}
    for request in feeds.requests:
        assert request.method == "GET" and request.content == b""
        assert request.headers["user-agent"] == "life-dashboard (personal use)"
        assert set(request.headers) <= {
            "host",
            "accept",
            "accept-encoding",
            "connection",
            "user-agent",
        }
        assert "771" not in str(request.url) and "936" not in str(request.url)


def test_a_poll_stores_what_the_feeds_say(feeds, db, city_settings):
    statuses = poll_transit(db, city_settings, feeds.client(), SUNDAY_NIGHT)
    weather = poll_weather(db, city_settings, feeds.client(), SUNDAY_NIGHT)

    assert [s.line for s in statuses] == ["N", "W", "M", "F", "Q103", "Q66", "Q69", "B62"]
    assert weather.alerts == ("Wind Advisory",)
    got = store.load_status(db, "2026-10-04")
    assert {s.line: s.status for s in got.lines} == {
        "N": "ok",
        "W": "ok",
        "F": "delays",
        "Q103": "ok",
        "Q66": "planned",
        "Q69": "ok",
        "B62": "planned",
    }
    assert got.transit_fetched_at_utc == got.weather.fetched_at_utc == "2026-10-05T03:00:00Z"
    assert (got.weather.temp_f, got.weather.alerts) == (56.6, ("Wind Advisory",))
    assert [r["kind"] for r in db.execute("SELECT kind FROM city_snapshots ORDER BY kind")] == [
        "transit",
        "weather",
    ]


@pytest.mark.parametrize(
    "fault",
    [
        httpx.ReadTimeout("timed out"),
        httpx.Response(500),
        httpx.Response(302, headers={"location": "https://evil.example/"}),
        httpx.Response(200, content=b"not json"),
    ],
)
def test_a_failed_transit_fetch_leaves_the_last_snapshot(feeds, db, city_settings, fault):
    poll_transit(db, city_settings, feeds.client(), SUNDAY_NIGHT)
    before = store.load_status(db, "2026-10-04")
    row = tuple(db.execute("SELECT * FROM city_snapshots").fetchone())

    feeds.broken["api-endpoint.mta.info"] = fault
    with pytest.raises(FetchError):
        poll_transit(db, city_settings, feeds.client(), SUNDAY_NIGHT + timedelta(minutes=5))

    assert tuple(db.execute("SELECT * FROM city_snapshots").fetchone()) == row
    assert store.load_status(db, "2026-10-04") == before


def test_a_reply_that_is_not_a_feed_leaves_the_last_snapshot(feeds, db, city_settings):
    poll_transit(db, city_settings, feeds.client(), SUNDAY_NIGHT)
    poll_weather(db, city_settings, feeds.client(), SUNDAY_NIGHT)
    before = store.load_status(db, "2026-10-04")

    feeds.broken["api-endpoint.mta.info"] = httpx.Response(200, json={"message": "Forbidden"})
    feeds.broken["api.open-meteo.com"] = httpx.Response(200, json={"error": True, "reason": "x"})
    later = SUNDAY_NIGHT + timedelta(minutes=30)
    with pytest.raises(ParseError, match="not an MTA alerts feed"):
        poll_transit(db, city_settings, feeds.client(), later)
    with pytest.raises(ParseError, match="not an Open-Meteo forecast"):
        poll_weather(db, city_settings, feeds.client(), later)
    assert store.load_status(db, "2026-10-04") == before


def test_a_failed_forecast_leaves_the_last_weather(feeds, db, city_settings):
    poll_weather(db, city_settings, feeds.client(), SUNDAY_NIGHT)
    before = store.load_status(db, "2026-10-04")
    feeds.broken["api.open-meteo.com"] = httpx.ConnectTimeout("timed out")
    feeds.requests.clear()
    with pytest.raises(FetchError, match="api.open-meteo.com: ConnectTimeout"):
        poll_weather(db, city_settings, feeds.client(), SUNDAY_NIGHT + timedelta(minutes=30))
    assert store.load_status(db, "2026-10-04") == before
    assert [r.url.host for r in feeds.requests] == ["api.open-meteo.com"]


def test_failed_weather_alerts_keep_the_forecast_and_carry_alerts_only_while_fresh(
    feeds, db, city_settings
):
    assert city_settings.city.stale_minutes == CityConfig().stale_minutes == 45
    poll_weather(db, city_settings, feeds.client(), SUNDAY_NIGHT)
    feeds.broken["api.weather.gov"] = httpx.Response(503)

    half_hour = SUNDAY_NIGHT + timedelta(minutes=30)
    with pytest.raises(FetchError, match=r"weather alerts not refreshed \(forecast stored\)"):
        poll_weather(db, city_settings, feeds.client(), half_hour)
    got = store.load_status(db, "2026-10-04").weather
    assert got.fetched_at_utc == "2026-10-05T03:30:00Z", "the new forecast is stored"
    assert got.alerts == ("Wind Advisory",), "alerts 30 minutes old are carried"
    assert store.stored_weather_alerts(db)[1] == "2026-10-05T03:00:00Z", "with their own age"

    with pytest.raises(FetchError):
        poll_weather(db, city_settings, feeds.client(), SUNDAY_NIGHT + timedelta(minutes=46))
    got = store.load_status(db, "2026-10-04").weather
    assert got.alerts == () and got.temp_f == 56.6, "alerts past stale_minutes are dropped"
    assert store.stored_weather_alerts(db) == ((), None)

    feeds.broken["api.weather.gov"] = httpx.Response(200, json={"title": "Not Found"})
    with pytest.raises(FetchError, match="not GeoJSON"):
        poll_weather(db, city_settings, feeds.client(), SUNDAY_NIGHT + timedelta(minutes=60))
    del feeds.broken["api.weather.gov"]
    poll_weather(db, city_settings, feeds.client(), SUNDAY_NIGHT + timedelta(minutes=61))
    assert store.load_status(db, "2026-10-04").weather.alerts == ("Wind Advisory",)
