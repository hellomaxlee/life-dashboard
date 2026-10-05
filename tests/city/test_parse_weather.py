"""Weather from the Open-Meteo reply saved tonight (fixtures/city/real/wx.json, whole) and
National Weather Service alerts (the Wind Advisory fixture is synthetic)."""

from __future__ import annotations

import pytest

from app.city.parse import ParseError, parse_weather, weather_alerts
from tests.city.conftest import MONDAY_MORNING, NY, SUNDAY_NIGHT, load, ny


def steps(weather) -> list[tuple]:
    return [(h.hour_local, h.tomorrow, h.temp_f, h.precip_pct, h.code) for h in weather.hours]


def test_tonight_at_2300_the_six_steps_run_into_tomorrow():
    weather = parse_weather(load("real", "wx"), load("real", "nws"), SUNDAY_NIGHT, NY)
    assert (weather.temp_f, weather.feels_f, weather.code) == (56.6, 56.1, 3)
    assert (weather.high_f, weather.low_f, weather.precip_pct_max) == (62.0, 56.6, 27)
    assert steps(weather) == [
        (23, False, 56.6, 2, 3),
        (2, True, 56.6, 0, 2),
        (5, True, 53.6, 0, 0),
        (8, True, 55.2, 0, 0),
        (11, True, 66.5, 0, 0),
        (14, True, 70.6, 0, 0),
    ]
    assert weather.alerts == ()
    assert weather.fetched_at_utc is None, "the store stamps it"


@pytest.mark.parametrize("minute", [0, 45])
def test_from_0800_the_six_steps_stay_inside_today(minute):
    weather = parse_weather(load("real", "wx"), None, ny(2026, 10, 4, 8, minute), NY)
    assert steps(weather) == [
        (8, False, 59.3, 0, 0),
        (11, False, 62.0, 2, 3),
        (14, False, 59.3, 14, 3),
        (17, False, 56.9, 27, 3),
        (20, False, 57.3, 3, 3),
        (23, False, 56.6, 2, 3),
    ]


def test_the_days_high_and_low_are_the_local_days_own():
    weather = parse_weather(load("real", "wx"), None, MONDAY_MORNING, NY)
    assert (weather.high_f, weather.low_f, weather.precip_pct_max) == (70.6, 51.1, 1)
    assert [h.hour_local for h in weather.hours] == [8, 11, 14, 17, 20, 23]


def test_hours_the_reply_does_not_cover_are_left_out_not_invented():
    weather = parse_weather(load("real", "wx"), None, ny(2026, 10, 5, 14, 0), NY)
    assert [(h.hour_local, h.tomorrow) for h in weather.hours] == [
        (14, False),
        (17, False),
        (20, False),
        (23, False),
    ]
    with pytest.raises(ParseError, match="no hours from 2026-10-07"):
        parse_weather(load("real", "wx"), None, ny(2026, 10, 7, 9, 0), NY)
    day_missing = parse_weather(load("real", "wx"), None, ny(2026, 10, 5, 23, 0), NY)
    assert len(day_missing.hours) == 1


def test_a_reply_missing_fields_gives_none_not_zero():
    forecast = load("real", "wx")
    del forecast["current"], forecast["daily"]
    forecast["hourly"]["precipitation_probability"][23] = None
    weather = parse_weather(forecast, None, SUNDAY_NIGHT, NY)
    assert (weather.temp_f, weather.feels_f, weather.code) == (None, None, None)
    assert (weather.high_f, weather.low_f, weather.precip_pct_max) == (None, None, None)
    assert weather.hours[0].precip_pct == 0 and len(weather.hours) == 6
    for bad in (None, [], {}, {"error": True, "reason": "bad latitude"}, {"hourly": []}):
        with pytest.raises(ParseError, match="not an Open-Meteo forecast"):
            parse_weather(bad, None, SUNDAY_NIGHT, NY)


def test_the_synthetic_wind_advisory_shows_from_the_day_it_starts_until_it_ends():
    nws = load("synthetic", "nws")
    assert weather_alerts(nws, SUNDAY_NIGHT, NY) == ("Wind Advisory",)
    assert parse_weather(load("real", "wx"), nws, SUNDAY_NIGHT, NY).alerts == ("Wind Advisory",)
    assert weather_alerts(nws, ny(2026, 10, 4, 16, 0), NY) == ("Wind Advisory",), "starts 18:00"
    assert weather_alerts(nws, ny(2026, 10, 3, 23, 0), NY) == (), "starts tomorrow"
    assert weather_alerts(nws, ny(2026, 10, 5, 5, 59), NY) == ("Wind Advisory",)
    assert weather_alerts(nws, ny(2026, 10, 5, 6, 0), NY) == (), "ended at 06:00"
    assert weather_alerts(load("real", "nws"), SUNDAY_NIGHT, NY) == ()


def feature(event, severity, onset="2026-10-04T12:00:00-04:00", ends="2026-10-05T12:00:00-04:00"):
    return {
        "type": "Feature",
        "properties": {"event": event, "severity": severity, "onset": onset, "ends": ends},
    }


def test_weather_alerts_are_most_severe_first_without_repeats_and_at_most_three():
    nws = {
        "features": [
            feature("Coastal Flood Statement", "Minor"),
            feature("Wind Advisory", "Moderate"),
            feature("Tornado Warning", "Extreme"),
            feature("Air Quality Alert", "Unknown"),
            feature("Wind Advisory", "Moderate"),
            feature("Severe Thunderstorm Warning", "Severe"),
            feature("Test Message", "Sideways"),
            feature("Tornado Warning", "Extreme"),
        ]
    }
    assert weather_alerts(nws, SUNDAY_NIGHT, NY) == (
        "Tornado Warning",
        "Severe Thunderstorm Warning",
        "Wind Advisory",
    )
    nws["features"] = nws["features"][:2] + nws["features"][3:5]
    assert weather_alerts(nws, SUNDAY_NIGHT, NY) == (
        "Wind Advisory",
        "Coastal Flood Statement",
        "Air Quality Alert",
    )


def test_weather_alert_bounds_fall_back_and_a_missing_bound_does_not_exclude():
    def names(**props) -> tuple[str, ...]:
        base = {"event": "Flood Watch", "severity": "Severe"}
        return weather_alerts({"features": [{"properties": {**base, **props}}]}, SUNDAY_NIGHT, NY)

    assert names() == ("Flood Watch",)
    assert names(onset=None, ends=None) == ("Flood Watch",)
    assert names(onset=None, effective="2026-10-05T00:00:00-04:00") == (), "starts tomorrow"
    assert names(ends=None, expires="2026-10-04T22:59:00-04:00") == (), "already expired"
    assert names(ends="2026-10-05T03:00:00+00:00") == (), "ends exactly now"
    assert names(ends="2026-10-05T03:00:01+00:00") == ("Flood Watch",)
    assert names(ends="soon", onset="2026-10-04") == ("Flood Watch",), "unreadable = missing"
    assert names(event="") == () and names(event=None) == ()
    for bad in (None, [], {}, {"features": None}, {"title": "Not Found", "status": 404}):
        with pytest.raises(ParseError, match="not GeoJSON"):
            weather_alerts(bad, SUNDAY_NIGHT, NY)
    junk = {"features": [None, {}, {"properties": []}, feature("Wind Advisory", "Minor")]}
    assert weather_alerts(junk, SUNDAY_NIGHT, NY) == ("Wind Advisory",)
