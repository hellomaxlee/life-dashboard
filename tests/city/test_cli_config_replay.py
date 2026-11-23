"""tools.city, the [city] config section, and the snapshot table's place in replay/backup."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.city import fetch, store
from app.config import DEFAULT_CONFIG_PATH, load_settings
from app.db import CODE_SCHEMA_VERSION, connect, table_names
from tests.city.conftest import CITY, FIXTURES, SUNDAY_NIGHT
from tests.city.test_fetch import Feeds
from tests.city.test_store import FETCHED, STAMP, statuses, weather
from tests.conftest import post_fixture
from tools import backup, city, replay
from tools.replay import checksum, snapshot

TONIGHT = """\
city 2026-10-04
transit fetched 2026-10-05T03:00:00Z
  N    subway ok               0
  W    subway ok               0
  F    subway delays    now    1  [F] trains are running with delays in both directions while we perform planned track maintenance in Brooklyn.
  Q103 bus    ok               0
  Q66  bus    planned   now    1  Westbound Q66 stop on Northern Blvd at 44th St is closed
  Q69  bus    ok               0
  B62  bus    planned   now    1  Northbound B62 buses are detoured due the Atlantic Antic street fair.
weather fetched 2026-10-05T03:00:00Z
  now 57F feels 56F code 3; high 62F low 57F; precip max 27%
  23:00    56.6F    2%  code 3
  02:00+1  56.6F    0%  code 2
  05:00+1  53.6F    0%  code 0
  08:00+1  55.2F    0%  code 0
  11:00+1  66.5F    0%  code 0
  14:00+1  70.6F    0%  code 0
  alerts: none
"""  # noqa: E501


@pytest.fixture
def shipped(settings, monkeypatch):
    """Settings as config.toml ships them, pointed at the test's own db."""
    monkeypatch.delenv("LIFE_CITY_ENABLED")
    loaded = load_settings()
    assert loaded.storage.db_path == settings.storage.db_path
    return loaded


def test_the_config_file_ships_the_decided_city_section(shipped, settings):
    assert shipped.city == CITY
    assert (shipped.city.transit_poll_minutes, shipped.city.weather_poll_minutes) == (5, 30)
    assert settings.city.enabled is False, "the test environment switches the panel off"
    assert settings.city == CITY.__class__(**{**vars(CITY), "enabled": False})


def test_fixture_mode_prints_tonight_and_touches_no_db_or_network(shipped, capsys, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("fixture mode must not fetch")

    monkeypatch.setattr(fetch, "get_json", no_network)
    real = str(FIXTURES / "real")
    assert city.main(["--fixture", real, "--now", "2026-10-04T23:00"]) == 0
    assert capsys.readouterr().out == TONIGHT
    assert not shipped.storage.db_path.exists()

    assert city.main(["--fixture", real]) == 0
    assert "transit fetched 2026-10-05T02:56:07Z" in capsys.readouterr().out, "the feed's own time"

    assert city.main(["--fixture", real, "--now", "2026-10-05T08:30"]) == 0
    monday = capsys.readouterr().out
    assert "  M    subway ok" in monday and "  F    subway" not in monday
    assert (
        "  N    subway planned   later  1  In Brooklyn, Manhattan-bound [N] local skips" in monday
    )

    assert city.main(["--fixture", str(FIXTURES / "synthetic"), "--now", "2026-10-04T23:00"]) == 0
    synthetic = capsys.readouterr().out
    assert (
        "  W    subway suspended now    2  No [W] service - take the [N] or [R] instead"
        in synthetic
    )
    assert "  alerts: Wind Advisory" in synthetic


def test_bad_arguments_are_refused(shipped, capsys, tmp_path):
    assert city.main(["--now", "2026-10-04T23:00"]) == 2
    assert city.main(["--fixture", str(tmp_path)]) == 2
    assert city.main(["--fixture", str(FIXTURES / "real"), "--now", "tonight"]) == 2
    assert capsys.readouterr().err.count("refusing:") == 3


def test_the_tool_refuses_a_db_the_code_does_not_match(shipped, capsys):
    assert city.main(["--show"]) == 2
    assert "no database at" in capsys.readouterr().err
    conn = connect(shipped.storage.db_path)
    conn.close()
    assert city.main([]) == 2
    assert f"expects {CODE_SCHEMA_VERSION}" in capsys.readouterr().err


def test_show_prints_the_stored_status_and_fetches_nothing(shipped, db, capsys, monkeypatch):
    monkeypatch.setattr(fetch, "get_json", lambda *a, **k: pytest.fail("--show must not fetch"))
    assert city.main(["--show"]) == 1
    assert "never been fetched" in capsys.readouterr().out
    store.save_transit(db, shipped.city, statuses(), FETCHED)
    store.save_weather(db, weather(), FETCHED, STAMP)
    assert city.main(["--show"]) == 0
    out = capsys.readouterr().out
    assert f"transit fetched {STAMP}" in out and "alerts: Wind Advisory" in out


def test_a_plain_run_fetches_stores_and_reports_a_failure(shipped, db, capsys, monkeypatch):
    feeds = Feeds()
    monkeypatch.setattr(fetch, "new_client", feeds.client)
    monkeypatch.setattr(fetch, "now_utc", lambda: SUNDAY_NIGHT)
    monkeypatch.setattr(city, "now_utc", lambda: SUNDAY_NIGHT)
    assert city.main([]) == 0
    assert len(feeds.requests) == 4
    assert "transit fetched" in capsys.readouterr().out
    assert store.load_status(db, "2026-10-04") is not None

    feeds.broken["api-endpoint.mta.info"] = httpx.ConnectError("no route")
    assert city.main([]) == 1
    captured = capsys.readouterr()
    assert "transit fetch failed: FetchError: api-endpoint.mta.info: ConnectError" in captured.err
    assert "transit fetched" in captured.out, "the last good snapshot is still printed"


def with_city(tmp_path: Path, old: str, new: str) -> Path:
    text = DEFAULT_CONFIG_PATH.read_text()
    assert old in text
    path = tmp_path / "config.toml"
    path.write_text(text.replace(old, new))
    return path


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("latitude = 40.77", "latitude = 40.7712", "city.latitude must be rounded to two decimals"),
        ("longitude = -73.94", "longitude = -273.94", "city.longitude must be a number"),
        ("latitude = 40.77", 'latitude = "40.77"', "city.latitude must be a number"),
        ('subway = ["N", "W"]', 'subway = "N"', "city.subway must be a list"),
        ('subway = ["N", "W"]', 'subway = ["N", "N"]', "city.subway must be a list of distinct"),
        ('subway = ["N", "W"]', 'subway = ["N", "M"]', "city.subway_weekday must be lines"),
        ('bus = ["Q103"', 'bus = ["q103 / x"', "city.bus must be a list"),
        ("transit_poll_minutes = 5", "transit_poll_minutes = 0", "city.transit_poll_minutes"),
        ("transit_poll_minutes = 5", "transit_poll_minutes = 61", "city.transit_poll_minutes"),
        ("weather_poll_minutes = 30", "weather_poll_minutes = 2.5", "city.weather_poll_minutes"),
        ("stale_minutes = 45", 'stale_minutes = "45"', "city.stale_minutes"),
        ("[city]\nenabled = true", '[city]\nenabled = "yes"', "city.enabled must be true or false"),
    ],
)
def test_a_bad_city_section_stops_the_boot(tmp_path, old, new, message):
    with pytest.raises(ValueError, match=message):
        load_settings(with_city(tmp_path, old, new))


def test_the_env_switch_and_a_missing_section(tmp_path, monkeypatch):
    monkeypatch.setenv("LIFE_CITY_ENABLED", "1")
    assert load_settings().city.enabled is True
    monkeypatch.setenv("LIFE_CITY_ENABLED", "maybe")
    with pytest.raises(ValueError, match="LIFE_CITY_ENABLED"):
        load_settings()
    monkeypatch.delenv("LIFE_CITY_ENABLED")
    text = DEFAULT_CONFIG_PATH.read_text()
    path = tmp_path / "config.toml"
    path.write_text(text[: text.index("[city]")])
    absent = load_settings(path).city
    assert absent.enabled is False and absent.subway == () and absent.bus == ()


def ingest(client) -> None:
    post_fixture(client, "workouts_v2_overlap.json")
    post_fixture(client, "metrics_v2_days.json")


def test_the_snapshot_table_is_neither_data_nor_derived_nor_authored(db):
    assert CODE_SCHEMA_VERSION >= 7
    assert "city_snapshots" in table_names(db)
    listed = replay.DATA_TABLES + replay.DERIVED_TABLES + replay.AUTHORED_TABLES
    assert "city_snapshots" not in listed


def test_replay_and_backup_neither_fail_on_nor_compare_the_city_snapshots(
    client, db, settings, tmp_path, capsys
):
    ingest(client)
    before = snapshot(db)
    store.save_transit(db, CITY, statuses(), FETCHED)
    store.save_weather(db, weather(), FETCHED, STAMP)
    stored = [tuple(row) for row in db.execute("SELECT * FROM city_snapshots ORDER BY kind")]
    assert snapshot(db) == before and "city_snapshots" not in before

    assert replay.main(["--verify", "--scratch", str(tmp_path / "verify.db")]) == 0
    assert "0 difference(s)" in capsys.readouterr().out

    made = backup.create_backup(settings, tmp_path / "b1", datetime(2026, 10, 5, 7, 15, tzinfo=UTC))
    assert made.data_checksum == checksum(before)
    assert backup.verify(made.path, settings) == []
    restored = backup.restore(made.path, tmp_path / "restored")
    conn = connect(restored)
    try:
        assert store.load_status(conn, "2026-10-04") == store.load_status(db, "2026-10-04")
    finally:
        conn.close()

    store.save_transit(db, CITY, statuses()[:1], SUNDAY_NIGHT)
    assert backup.verify(made.path, settings) == [], "a newer snapshot is not a difference"

    db.execute("UPDATE steps_daily SET steps = 1")
    assert replay.main(["--rebuild-live", "--scratch", str(tmp_path / "rebuild.db")]) == 0
    capsys.readouterr()
    kept = [tuple(row) for row in db.execute("SELECT * FROM city_snapshots ORDER BY kind")]
    assert kept[1] == stored[1] and kept[0][0] == "transit"
    assert replay.main(["--verify", "--scratch", str(tmp_path / "verify2.db")]) == 0
