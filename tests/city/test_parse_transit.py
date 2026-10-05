"""Line status from the MTA alert feeds. The `real` fixtures are tonight's feeds (fetched
2026-10-04 about 23:00 New York); the expected rows were read off them by hand."""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest

from app.city import parse
from app.city.model import LineStatus
from app.city.parse import (
    ParseError,
    WatchedLine,
    all_lines,
    classify,
    clean_headline,
    line_status,
    line_statuses,
    lines_for_day,
)
from app.timeutil import local_day
from tests.city.conftest import (
    CITY,
    EMPTY_FEED,
    MONDAY_MORNING,
    N_LINE,
    NY,
    SUNDAY_NIGHT,
    alert,
    feed,
    load,
    ny,
)

F_DELAYS = (
    "[F] trains are running with delays in both directions while we perform planned track "
    "maintenance in Brooklyn."
)
Q66_CLOSED = "Westbound Q66 stop on Northern Blvd at 44th St is closed"
B62_DETOUR = "Northbound B62 buses are detoured due the Atlantic Antic street fair."
N_SKIPS = "In Brooklyn, Manhattan-bound [N] local skips 25 St, Prospect Av, 4 Av-9 St and Union St"


def every_line(folder: str, now) -> dict[str, LineStatus]:
    found = line_statuses(load(folder, "subway"), load(folder, "bus"), all_lines(CITY), now, NY)
    return {status.line: status for status in found}


def row(status: LineStatus) -> tuple:
    return (status.status, status.now, status.alerts, status.headline)


def test_tonights_real_feeds_at_sunday_2300():
    got = every_line("real", SUNDAY_NIGHT)
    assert list(got) == ["N", "W", "M", "F", "Q103", "Q66", "Q69", "B62"]
    assert {line: row(status) for line, status in got.items()} == {
        "N": ("ok", False, 0, None),
        "W": ("ok", False, 0, None),
        "M": ("ok", False, 0, None),
        "F": ("delays", True, 1, F_DELAYS),
        "Q103": ("ok", False, 0, None),
        "Q66": ("planned", True, 1, Q66_CLOSED),
        "Q69": ("ok", False, 0, None),
        "B62": ("planned", True, 1, B62_DETOUR),
    }
    assert [got[line].kind for line in got] == ["subway"] * 4 + ["bus"] * 4


def test_tonights_real_feeds_read_on_monday_0830():
    got = every_line("real", MONDAY_MORNING)
    assert {line: row(status) for line, status in got.items()} == {
        "N": ("planned", False, 1, N_SKIPS),
        "W": ("ok", False, 0, None),
        "M": ("ok", False, 0, None),
        "F": ("delays", True, 3, F_DELAYS),
        "Q103": ("ok", False, 0, None),
        "Q66": ("planned", True, 1, Q66_CLOSED),
        "Q69": ("ok", False, 0, None),
        "B62": ("planned", True, 1, B62_DETOUR),
    }


def test_the_synthetic_feeds_cover_what_tonight_lacks(monkeypatch, caplog):
    monkeypatch.setattr(parse, "_unknown_logged", set())
    with caplog.at_level(logging.WARNING):
        got = every_line("synthetic", SUNDAY_NIGHT)
        again = every_line("synthetic", SUNDAY_NIGHT)
    assert got == again
    statuses = {line: (s.status, s.now, s.alerts) for line, s in got.items()}
    assert statuses == {
        "N": ("delays", True, 2),
        "W": ("suspended", True, 2),
        "M": ("ok", False, 0),
        "F": ("planned", True, 1),
        "Q103": ("planned", True, 1),
        "Q66": ("ok", False, 0),
        "Q69": ("planned", True, 1),
        "B62": ("ok", False, 0),
    }
    assert got["W"].headline == "No [W] service - take the [N] or [R] instead"
    assert got["N"].headline.startswith("[N] trains are running with delays")
    assert got["F"].headline.startswith("Free shuttle buses replace [F] trains")
    unknown = [r for r in caplog.records if "unknown MTA alert type" in r.getMessage()]
    assert len(unknown) == 1, "an unknown type is logged once, not once per poll"
    assert "Shuttle Buses Replace Trains" in unknown[0].getMessage()


def test_a_suspension_counts_as_suspended_only_while_it_is_in_effect():
    def w_line(now):
        return every_line("synthetic", now)["W"]

    before = w_line(ny(2026, 10, 2, 23, 0))
    assert (before.status, before.now, before.alerts) == ("planned", False, 1)
    assert before.headline == "No [W] service - take the [N] or [R] instead"
    during = w_line(ny(2026, 10, 2, 23, 30))
    assert (during.status, during.now) == ("suspended", True)
    after = w_line(ny(2026, 10, 5, 5, 0))
    assert (after.status, after.alerts) == ("ok", 0), "the end of a period is exclusive"


def test_work_that_ended_earlier_today_is_not_shown_as_upcoming():
    m_at_2030 = every_line("synthetic", ny(2026, 10, 4, 20, 30))["M"]
    assert (m_at_2030.status, m_at_2030.now) == ("suspended", True)
    m_at_2100 = every_line("synthetic", ny(2026, 10, 4, 21, 0))["M"]
    assert (m_at_2100.status, m_at_2100.alerts, m_at_2100.headline) == ("ok", 0, None)


def test_only_periods_that_touch_the_rest_of_today_count():
    now = ny(2026, 10, 7, 12, 0)
    tomorrow = alert("N", "Planned - Reroute", [(ny(2026, 10, 8, 0, 0), ny(2026, 10, 8, 5, 0))])
    yesterday = alert("N", "Delays", [(ny(2026, 10, 6, 9, 0), ny(2026, 10, 7, 11, 59))])
    assert line_status(N_LINE, [tomorrow, yesterday], now, NY) == LineStatus("N", "subway")
    tonight = alert("N", "Planned - Reroute", [(ny(2026, 10, 7, 23, 59), ny(2026, 10, 8, 5, 0))])
    got = line_status(N_LINE, [tomorrow, yesterday, tonight], now, NY)
    assert (got.status, got.now, got.alerts) == ("planned", False, 1)
    open_ended = alert("N", "Delays", [(ny(2026, 10, 1, 0, 0), None)])
    assert line_status(N_LINE, [open_ended], now, NY).status == "delays"
    no_period = alert("N", "Planned - Stops Skipped", None)
    got = line_status(N_LINE, [no_period], now, NY)
    assert (got.status, got.now, got.alerts) == ("planned", True, 1)
    empty_list = {**no_period, "active_period": []}
    assert line_status(N_LINE, [empty_list], now, NY).now is True


def test_today_ends_at_local_midnight_on_the_25_hour_day():
    """2026-11-01 is 25 hours long in New York. Work starting at 23:30 that night is today's."""
    now = ny(2026, 11, 1, 0, 30)
    late = alert("N", "Planned - Reroute", [(ny(2026, 11, 1, 23, 30), ny(2026, 11, 2, 5, 0))])
    assert late["active_period"][0]["start"] - int(now.timestamp()) == 24 * 3600
    assert line_status(N_LINE, [late], now, NY).status == "planned"
    next_day = alert("N", "Planned - Reroute", [(ny(2026, 11, 2, 0, 0), ny(2026, 11, 2, 5, 0))])
    assert line_status(N_LINE, [next_day], now, NY).status == "ok"


def day_lines(day: str) -> list[str]:
    wanted = lines_for_day(CITY.subway, CITY.subway_weekday, CITY.subway_weekend, CITY.bus, day)
    return [w.line for w in wanted]


def test_the_m_on_weekdays_and_the_f_at_weekends():
    weekday = ["N", "W", "M", "Q103", "Q66", "Q69", "B62"]
    weekend = ["N", "W", "F", "Q103", "Q66", "Q69", "B62"]
    assert day_lines("2026-10-02") == weekday, "Friday"
    assert day_lines("2026-10-03") == weekend, "Saturday"
    assert day_lines("2026-10-04") == weekend, "Sunday"
    assert day_lines("2026-10-05") == weekday, "Monday"
    friday_2359 = ny(2026, 10, 2, 23, 59)
    saturday_0000 = friday_2359 + timedelta(minutes=1)
    assert day_lines(local_day(friday_2359, "America/New_York")) == weekday
    assert day_lines(local_day(saturday_0000, "America/New_York")) == weekend
    kinds = lines_for_day(("N",), ("M",), ("F",), ("Q69",), "2026-10-03")
    assert kinds == (
        WatchedLine("N", "subway"),
        WatchedLine("F", "subway"),
        WatchedLine("Q69", "bus"),
    )


def test_the_weekend_switch_follows_the_home_clock_across_dst_changes():
    from datetime import UTC, datetime

    def lines_at(utc: datetime) -> list[str]:
        return day_lines(local_day(utc, "America/New_York"))

    after_fall_back = datetime(2026, 11, 7, 4, 30, tzinfo=UTC)
    assert after_fall_back.astimezone(NY).strftime("%a %H:%M") == "Fri 23:30"
    assert "M" in lines_at(after_fall_back), "still Friday at UTC-5; Saturday only at UTC-4"
    assert "F" in lines_at(datetime(2026, 11, 7, 5, 0, tzinfo=UTC))
    after_spring_forward = datetime(2026, 3, 9, 4, 30, tzinfo=UTC)
    assert after_spring_forward.astimezone(NY).strftime("%a %H:%M") == "Mon 00:30"
    assert "M" in lines_at(after_spring_forward), "Monday at UTC-4; still Sunday at UTC-5"
    assert "F" in lines_at(datetime(2026, 3, 9, 3, 59, tzinfo=UTC))


NOW = ny(2026, 10, 7, 12, 0)
RUNNING = [(ny(2026, 10, 7, 6, 0), ny(2026, 10, 7, 18, 0))]
LATER = [(ny(2026, 10, 7, 20, 0), ny(2026, 10, 7, 23, 0))]


def test_the_most_severe_alert_names_the_line():
    planned = alert("N", "Planned - Stops Skipped", RUNNING, updated=900, text="skips")
    delays = alert("N", "Delays", RUNNING, updated=100, text="delays")
    suspended_later = alert("N", "Planned - Suspended", LATER, updated=999, text="no trains later")
    got = line_status(N_LINE, [planned, delays, suspended_later], NOW, NY)
    assert (got.status, got.headline, got.now, got.alerts) == ("delays", "delays", True, 3)
    suspended = alert("N", "Suspended", RUNNING, updated=1, text="no trains")
    got = line_status(N_LINE, [planned, delays, suspended_later, suspended], NOW, NY)
    assert (got.status, got.headline, got.alerts) == ("suspended", "no trains", 4)
    got = line_status(N_LINE, [planned, suspended_later], NOW, NY)
    assert (got.status, got.headline) == ("planned", "skips"), "a later suspension is planned"


def test_a_tie_goes_to_the_alert_in_effect_now_then_the_latest_update():
    later_newer = alert("N", "Planned - Reroute", LATER, updated=500, text="later")
    now_older = alert("N", "Planned - Reroute", RUNNING, updated=100, text="now older")
    now_newer = alert("N", "Planned - Express to Local", RUNNING, updated=200, text="now newer")
    for order in ([later_newer, now_older], [now_older, later_newer]):
        got = line_status(N_LINE, order, NOW, NY)
        assert (got.headline, got.now) == ("now older", True)
    for order in ([now_older, now_newer, later_newer], [later_newer, now_newer, now_older]):
        assert line_status(N_LINE, order, NOW, NY).headline == "now newer"
    delays_later = alert("N", "Reduced Service", LATER, updated=999, text="fewer trains later")
    got = line_status(N_LINE, [delays_later, now_older], NOW, NY)
    assert (got.status, got.headline) == ("planned", "now older"), "delays later are planned"


SEEN_TONIGHT = {
    "Delays": "delays",
    "Expect Delays": "delays",
    "Reduced Service": "delays",
    "Planned - Part Suspended": "planned",
    "Planned - Suspended": "suspended",
    "Planned - Stops Skipped": "planned",
    "Planned - Express to Local": "planned",
    "Planned - Reroute": "planned",
    "Planned - Detour": "planned",
    "Detour": "planned",
    "Special Schedule": "planned",
    "Boarding Change": None,
    "Station Notice": None,
    "Extra Service": None,
}


def test_every_alert_type_seen_tonight_is_known(monkeypatch, caplog):
    monkeypatch.setattr(parse, "_unknown_logged", set())
    seen = set()
    for name in ("subway", "bus"):
        for entity in load("real", name)["entity"]:
            seen.add(entity["alert"][parse.MERCURY]["alert_type"])
    assert seen <= set(SEEN_TONIGHT)
    with caplog.at_level(logging.WARNING):
        assert {name: classify(name) for name in SEEN_TONIGHT} == SEEN_TONIGHT
        assert classify("Suspended") == classify("No Scheduled Service") == "suspended"
        assert classify("Slow Speeds") == "delays"
        assert classify("Service Change") == classify("Part Suspended") == "planned"
        assert classify("  planned -  stops skipped ") == "planned"
    assert caplog.records == []


@pytest.mark.parametrize(
    "alert_type",
    ["Station Notice", "Boarding Change", "Extra Service", "Elevator Outage", "Escalator Notice"],
)
def test_notices_never_count(alert_type):
    notice = alert("N", alert_type, RUNNING, updated=999)
    assert classify(alert_type) is None
    assert line_status(N_LINE, [notice], NOW, NY) == LineStatus("N", "subway")
    real = alert("N", "Planned - Reroute", RUNNING, text="reroute")
    got = line_status(N_LINE, [notice, real, notice], NOW, NY)
    assert (got.status, got.alerts, got.headline) == ("planned", 1, "reroute")


def test_an_unknown_type_is_planned_and_logged_once(monkeypatch, caplog):
    monkeypatch.setattr(parse, "_unknown_logged", set())
    odd = alert("N", "Trains Running In Reverse", RUNNING, text="odd")
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            got = line_status(N_LINE, [odd], NOW, NY)
    assert (got.status, got.now, got.alerts, got.headline) == ("planned", True, 1, "odd")
    assert [r.getMessage() for r in caplog.records] == [
        "city: unknown MTA alert type 'Trains Running In Reverse' is shown as planned work"
    ]
    missing = alert("N", "x", RUNNING)
    del missing[parse.MERCURY]
    assert line_status(N_LINE, [missing], NOW, NY).status == "planned"


def test_headline_is_plain_ascii_on_one_line_and_keeps_the_bullets():
    raw = "  [N] trains — “slow” near Café  St\n\tExpect… delays,  "
    assert clean_headline(raw) == '[N] trains - "slow" near Cafe St Expect... delays'
    assert clean_headline("[F][G] skip 4 Av-9 St") == "[F][G] skip 4 Av-9 St"
    exact = "word " * 23 + "ending"
    assert len(exact) == 121
    cut = clean_headline(exact)
    assert cut == ("word " * 23).strip() + "..." and len(cut) == 117
    assert clean_headline(exact[:-1]) == exact[:-1], "120 characters fit whole"
    unbroken = clean_headline("x" * 300)
    assert unbroken == "x" * 117 + "..."
    for entity in load("real", "subway")["entity"] + load("real", "bus")["entity"]:
        text = clean_headline(parse.header_text(entity["alert"]))
        assert text.isascii() and len(text) <= 120 and "\n" not in text and "<" not in text


def test_the_plain_english_header_is_preferred_over_html():
    both = alert("N", "Delays", RUNNING, text="plain text")
    assert parse.header_text(both) == "plain text"
    html_only = {**both, "header_text": {"translation": [both["header_text"]["translation"][1]]}}
    assert clean_headline(parse.header_text(html_only)) == "plain text"
    assert parse.header_text({}) is None
    got = line_status(N_LINE, [{**both, "header_text": {}}], NOW, NY)
    assert (got.status, got.headline) == ("delays", None)


def test_route_ids_match_whole_lines_only():
    def routes(*ids: str) -> dict:
        built = alert("x", "Delays", RUNNING)
        built["informed_entity"] = [{"agency_id": "MTABC", "route_id": i} for i in ids]
        built["informed_entity"].append({"agency_id": "MTABC", "stop_id": "R01"})
        return built

    def status(line: str, kind: str, *ids: str) -> str:
        return line_status(WatchedLine(line, kind), [routes(*ids)], NOW, NY).status

    assert status("Q66", "bus", "Q66") == "delays"
    assert status("Q66", "bus", "MTABC_Q66") == "delays", "a prefixed id is its last part"
    assert status("B62", "bus", "MTA NYCT_B62") == "delays"
    assert status("Q66", "bus", "Q6", "Q660", "Q65", "QM66") == "ok"
    assert status("Q52", "bus", "Q52+") == "delays"
    assert status("N", "subway", "Q", "W", "R") == "ok"
    assert status("F", "subway", "FX") == "delays"
    assert status("M", "subway", "M15", "M60+") == "ok"
    assert status("N", "subway", "SI", "GS", "H") == "ok"


def test_subway_lines_read_the_subway_feed_and_buses_the_bus_feed():
    m_bus = alert("M", "Delays", RUNNING)
    got = line_statuses(EMPTY_FEED, feed(m_bus), all_lines(CITY), NOW, NY)
    assert all(status.status == "ok" for status in got)
    got = line_statuses(feed(m_bus), EMPTY_FEED, all_lines(CITY), NOW, NY)
    assert [s.line for s in got if s.status != "ok"] == ["M"]


@pytest.mark.parametrize(
    "bad", [None, [], {}, {"message": "Forbidden"}, {"header": {}}, {"header": {}, "entity": {}}]
)
def test_a_reply_that_is_not_a_feed_is_an_error_not_all_clear(bad):
    with pytest.raises(ParseError, match="not an MTA alerts feed"):
        line_statuses(bad, EMPTY_FEED, all_lines(CITY), NOW, NY)
    with pytest.raises(ParseError):
        line_statuses(EMPTY_FEED, bad, all_lines(CITY), NOW, NY)


def test_a_malformed_alert_is_skipped_not_fatal():
    good = alert("N", "Delays", RUNNING, text="good")
    junk = feed(good)
    junk["entity"] += [None, {"id": "x"}, {"id": "y", "alert": "text"}, {"alert": {}}]
    junk["entity"].append(
        {"alert": {"informed_entity": "N", "active_period": [None, {"end": "x"}]}}
    )
    got = line_statuses(junk, EMPTY_FEED, [N_LINE], NOW, NY)
    assert (got[0].status, got[0].alerts) == ("delays", 1)
