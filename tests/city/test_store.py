from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime

from app.city import store
from app.city.model import CityStatus, HourWeather, LineStatus, Weather
from app.city.parse import all_lines, line_statuses, parse_weather
from tests.city.conftest import CITY, NY, SUNDAY_NIGHT, load
from tests.conftest import count

FETCHED = datetime(2026, 10, 5, 3, 0, 7, tzinfo=UTC)
STAMP = "2026-10-05T03:00:07Z"


def statuses() -> tuple[LineStatus, ...]:
    return line_statuses(
        load("real", "subway"), load("real", "bus"), all_lines(CITY), SUNDAY_NIGHT, NY
    )


def weather() -> Weather:
    return parse_weather(load("real", "wx"), load("synthetic", "nws"), SUNDAY_NIGHT, NY)


def test_nothing_fetched_is_none(db):
    assert store.load_status(db, "2026-10-04") is None
    assert store.stored_weather_alerts(db) == ((), None)


def test_round_trip_gives_back_what_was_parsed(db):
    store.save_transit(db, CITY, statuses(), FETCHED)
    store.save_weather(db, weather(), FETCHED, STAMP)

    got = store.load_status(db, "2026-10-04")

    by_line = {s.line: s for s in statuses()}
    assert got == CityStatus(
        day_local="2026-10-04",
        weather=replace(weather(), fetched_at_utc=STAMP),
        lines=tuple(by_line[line] for line in ("N", "W", "F", "Q103", "Q66", "Q69", "B62")),
        transit_fetched_at_utc=STAMP,
    )
    assert isinstance(got.lines, tuple) and isinstance(got.weather.hours, tuple)
    assert isinstance(got.weather.hours[0], HourWeather) and got.weather.alerts == (
        "Wind Advisory",
    )
    assert store.stored_weather_alerts(db) == (("Wind Advisory",), STAMP)


def test_the_stored_snapshot_serves_weekdays_and_weekends(db):
    store.save_transit(db, CITY, statuses(), FETCHED)

    def lines(day: str) -> list[str]:
        return [s.line for s in store.load_status(db, day).lines]

    assert lines("2026-10-02") == ["N", "W", "M", "Q103", "Q66", "Q69", "B62"], "Friday"
    assert lines("2026-10-03") == ["N", "W", "F", "Q103", "Q66", "Q69", "B62"], "Saturday"
    assert lines("2026-10-04") == lines("2026-10-03"), "Sunday"
    assert lines("2026-10-05") == lines("2026-10-02"), "Monday"
    kinds = [s.kind for s in store.load_status(db, "2026-10-05").lines]
    assert kinds == ["subway"] * 3 + ["bus"] * 4


def test_one_part_alone_is_a_status_with_the_other_missing(db):
    store.save_weather(db, weather(), FETCHED, None)
    only_weather = store.load_status(db, "2026-10-04")
    assert only_weather.lines == () and only_weather.transit_fetched_at_utc is None
    assert only_weather.weather.fetched_at_utc == STAMP
    assert store.stored_weather_alerts(db) == ((), None), "alerts with no fetch time are unknown"

    db.execute("DELETE FROM city_snapshots")
    store.save_transit(db, CITY, statuses(), FETCHED)
    only_transit = store.load_status(db, "2026-10-04")
    assert only_transit.weather is None and len(only_transit.lines) == 7


def test_each_fetch_replaces_the_row(db):
    store.save_transit(db, CITY, statuses(), FETCHED)
    all_ok = tuple(LineStatus(w.line, w.kind) for w in all_lines(CITY))
    store.save_transit(db, CITY, all_ok, datetime(2026, 10, 5, 3, 5, tzinfo=UTC))
    store.save_weather(db, weather(), FETCHED, STAMP)
    store.save_weather(db, replace(weather(), temp_f=40.0, alerts=()), FETCHED, STAMP)

    assert count(db, "city_snapshots") == 2
    got = store.load_status(db, "2026-10-04")
    assert got.transit_fetched_at_utc == "2026-10-05T03:05:00Z"
    assert all(line.status == "ok" for line in got.lines)
    assert got.weather.temp_f == 40.0 and got.weather.alerts == ()


def test_a_row_that_cannot_be_read_counts_as_never_fetched_and_is_logged_once(db, caplog):
    store.save_weather(db, weather(), FETCHED, STAMP)
    db.execute(
        "INSERT INTO city_snapshots VALUES ('transit', ?, ?)",
        ('{"groups": {"subway": ["N"]}, "lines": [{"line": "N", "kind": "tram"}]}', STAMP),
    )
    with caplog.at_level(logging.ERROR):
        first = store.load_status(db, "2026-10-04")
        store.load_status(db, "2026-10-04")
    assert first.lines == () and first.transit_fetched_at_utc is None
    assert first.weather is not None
    assert len([r for r in caplog.records if "cannot be read" in r.getMessage()]) == 1

    db.execute("UPDATE city_snapshots SET snapshot_json = 'not json' WHERE kind = 'weather'")
    db.execute("UPDATE city_snapshots SET snapshot_json = '[]' WHERE kind = 'transit'")
    assert store.load_status(db, "2026-10-04") is None


def test_only_the_two_kinds_can_be_stored(db):
    import sqlite3

    import pytest

    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO city_snapshots VALUES ('raw', '{}', ?)", (STAMP,))
