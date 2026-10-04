"""Gates added after Noor's display audit of the merged tree (2026-10-02). Numbered as audited."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest

from app.render.adapters.pixoo import PixooAdapter
from app.render.celebrate import earned_wins
from app.render.font import BODY, SMALL, text_width
from app.render.frame import Clip, new_frame
from app.render.rotation import render_screen
from app.render.screens import (
    as_of_label,
    fit_summary,
    render_books,
    render_today,
    render_week,
    wrap_lines,
)
from app.render.usage import age_label, fill_width, reset_label, usage_state
from app.render.view import ClaudeUsage, DayView, load_fixture, view_from_metrics
from app.render.view_db import view_from_db
from app.timeutil import from_utc_iso, to_utc_iso
from tests.render import WEEK_41, WEEK_COMPLETE, load, record_text
from tools.render import main as render_main

HOST = "192.168.1.50"
GREEN = (60, 220, 90)
AMBER = (255, 170, 0)
RED = (255, 60, 50)
TRACK = (34, 36, 54)
NOON = from_utc_iso("2026-09-30T16:00:00Z")


def texts(drawn: list[tuple]) -> list[str]:
    return [item[0] for item in drawn]


def lit_span(frame, top: int, bottom: int) -> tuple[int, int]:
    box = frame.crop((0, top, 64, bottom + 1)).getbbox()
    assert box is not None
    return box[0], box[2] - 1


# 2. frames go to the device and nowhere else


def test_pixoo_client_ignores_proxy_environment(monkeypatch):
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "HTTPS_PROXY"):
        monkeypatch.setenv(name, "http://203.0.113.9:8080")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    adapter = PixooAdapter(HOST)
    client = adapter._client
    assert client.trust_env is False
    assert client._mounts == {}
    assert client._transport_for_url(httpx.URL(f"http://{HOST}/post")) is client._transport


# 3. the label states the dot, not the day


def test_false_dot_says_no_dot_yet_not_rest(monkeypatch, settings):
    view, _ = load(WEEK_41, settings)
    drawn = record_text(monkeypatch)
    render_today(replace(view, today_dot=False))
    assert ("NO DOT YET", 13, 42, "small-3x5", 1) in drawn
    assert "REST SO FAR" not in texts(drawn)


# 5. "as of" cannot make an old or future push look recent


@pytest.mark.parametrize(
    ("as_of", "label"),
    [
        ("2026-10-10T22:10:00Z", "AS OF 18:10"),
        ("2026-10-09T22:10:00Z", "AS OF FRI 18:10"),
        ("2026-10-04T22:10:00Z", "AS OF SUN 18:10"),
        ("2026-10-03T22:10:00Z", "AS OF 7D AGO"),
        ("2026-10-02T22:10:00Z", "AS OF 8D AGO"),
        ("2025-10-10T22:10:00Z", "AS OF 365D AGO"),
        ("2026-10-11T14:00:00Z", "NO PUSH YET"),
        ("2027-10-10T22:10:00Z", "NO PUSH YET"),
    ],
)
def test_as_of_prints_days_beyond_six_and_refuses_the_future(as_of, label):
    view = DayView(day_local="2026-10-10", as_of_utc=as_of)
    assert as_of_label(view) == label


def test_a_push_later_than_now_is_not_shown(monkeypatch, settings):
    view, now = load(WEEK_41, settings)
    drawn = record_text(monkeypatch)
    render_screen("today", view, from_utc_iso(view.as_of_utc) - timedelta(minutes=1))
    assert not [t for t in texts(drawn) if t.startswith("AS OF")]


# 6. the summary is folded to what the font can draw


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("Finished *Jazz* (10 of 12); fine.", "Finished Jazz (10 of 12); fine."),
        ("Finished _Jazz_ and **Beloved** and __Sula__.", "Finished Jazz and Beloved and Sula."),
        ("A café naïve façade, Ångström.", "A cafe naive facade, Angstrom."),
        ("Nice \U0001f600 work ✨ today → rest.", "Nice work today rest."),
        ("“It’s enough” — rest – now…", '"It\'s enough" - rest - now...'),
        ("HRV −20 % and ½ a book.", "HRV -20 % and 1/2 a book."),
        ("5 * 3 and snake_case_name stay.", "5 * 3 and snake_case_name stay."),
        ("Tabs\tand\nnewlines  collapse.", "Tabs and newlines collapse."),
    ],
)
def test_summary_text_is_folded_to_ascii(raw, clean):
    assert fit_summary(raw) == clean
    assert "?" not in fit_summary(raw)


# 7. the usage fill is floored, like its label


def test_usage_fill_is_floored_and_crosses_each_tick_with_its_colour(settings):
    assert [fill_width(p) for p in (0.0, 0.4, 41.2, 59.99, 60.0, 84.99, 85.0, 99.99, 100.0)] == [
        0,
        1,
        24,
        35,
        36,
        50,
        51,
        59,
        60,
    ]
    view, now = load(WEEK_41, settings)
    for pct, before, after, tick_x in (
        (59.99, GREEN, GREEN, 37),
        (60.0, AMBER, AMBER, 37),
        (84.99, AMBER, AMBER, 52),
        (85.0, RED, RED, 52),
    ):
        frame = render_week(replace(view, claude=replace(view.claude, used_pct=pct)), now).poster
        reached = frame.getpixel((tick_x, 42)) != TRACK
        assert frame.getpixel((2, 42)) == before == after
        assert reached == (pct in (60.0, 85.0)), pct
        for y in (41, 45):
            assert [x for x in range(64) if frame.getpixel((x, y)) != (0, 0, 0)] == [37, 52]


# 9. dates are canonical and never crash


def test_dates_are_canonicalised_and_out_of_range_days_do_not_500(client, settings, tmp_path):
    assert view_from_metrics("20261002", {}, {}, settings).day_local == "2026-10-02"
    page = client.get("/preview?date=20261002")
    assert page.status_code == 200
    assert "Preview: 2026-10-02" in page.text
    assert "date=2026-10-02&amp;" in page.text and "date=20261002" not in page.text
    for day in ("9999-12-31", "0001-01-01"):
        assert client.get(f"/preview?date={day}").status_code == 200, day
        assert client.get(f"/preview/image/today?date={day}").status_code == 200, day
    assert client.get("/preview?date=2026-02-30").status_code == 422


def test_render_tool_rejects_a_bad_date_cleanly(settings, tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        render_main(["--date", "2026-02-30", "--out", str(tmp_path / "x")])
    assert exit_info.value.code == 2
    assert "2026-02-30 is not a valid date" in capsys.readouterr().err
    assert not (tmp_path / "x").exists()


def test_fixture_with_a_bad_day_names_the_file(settings, tmp_path, capsys):
    bad = tmp_path / "bad__cell__x__y.json"
    bad.write_text(json.dumps({"day_local": "2026-13-01", "now_utc": "2026-10-02T16:00:00Z"}))
    with pytest.raises(ValueError, match=r"bad__cell__x__y\.json: day_local '2026-13-01'"):
        load_fixture(bad, settings)
    with pytest.raises(SystemExit) as exit_info:
        render_main(["--fixture", str(bad), "--out", str(tmp_path / "x")])
    assert exit_info.value.code == 2
    assert "bad__cell__x__y.json" in capsys.readouterr().err


# 10. no "NO PUSH YET" beside numbers a later push back-filled


def test_as_of_line_is_omitted_when_data_exists_without_a_push(monkeypatch, db, settings):
    db.execute("INSERT INTO steps_daily VALUES ('2026-09-01', 8412, 'aggregate')")
    db.execute(
        "INSERT INTO raw_archive (id, source, received_at_utc, sha256, path, byte_len, parsed_ok) "
        "VALUES (1, 'health', '2026-09-05T12:00:00Z', 'sha', 'x.json', 1, 1)"
    )
    view = view_from_db(db, settings, "2026-09-01")
    assert view.steps == 8412 and view.as_of_utc is None
    drawn = record_text(monkeypatch)
    render_today(view)
    assert "8412" in texts(drawn)
    assert "NO PUSH YET" not in texts(drawn)
    assert not [item for item in drawn if item[2] == 58]

    drawn.clear()
    render_today(DayView(day_local="2026-09-01"))
    assert ("NO PUSH YET", 2, 58, "small-3x5", 1) in drawn


# 11. nothing runs off the frame, and impossible times are not printed


def test_today_labels_and_long_sleep_stay_inside_the_margins(settings):
    view, _ = load(WEEK_41, settings)
    for dot in (True, False, None):
        frame = render_today(replace(view, today_dot=dot)).poster
        assert lit_span(frame, 41, 47)[1] <= 61, dot
    for hours in (9.9, 10.0, 12.5, 24.0):
        frame = render_today(replace(view, sleep_hours=hours)).poster
        left, right = lit_span(frame, 9, 23)
        assert left >= 2 and right <= 61, (hours, left, right)


@pytest.mark.parametrize("streak", [0, 9, 76, 999, 9999, 12345, 1234567, 10**9, 10**15])
def test_streak_line_steps_down_instead_of_overflowing(streak, settings):
    view, now = load(WEEK_41, settings)
    frame = render_week(replace(view, streak_weeks=streak), now).poster
    left, right = lit_span(frame, 49, 58)
    assert left >= 2 and right <= 61, (streak, left, right)


def test_impossible_reset_and_capture_times_are_not_printed(settings):
    assert reset_label(to_utc_iso(NOON + timedelta(days=7)), NOON) == "RESETS IN 7D"
    assert reset_label(to_utc_iso(NOON + timedelta(days=7, hours=1)), NOON) == "NO RESET TIME"
    assert reset_label(to_utc_iso(NOON + timedelta(days=26390)), NOON) == "NO RESET TIME"
    future = ClaudeUsage(
        41.2, to_utc_iso(NOON + timedelta(days=2)), to_utc_iso(NOON + timedelta(hours=3))
    )
    state = usage_state(future, NOON, 24)
    assert state.stale and state.age_label == "AGE UNKNOWN"


# 12. a number stays with its unit


@pytest.mark.parametrize(
    "line",
    [
        "Finished Jazz (10 of 12); 9.8 h of sleep. It's enough - rest is 57% of the work.",
        "Finished Jazz (3 of 12); 6.2 h of sleep. It's enough - rest is 22% of the work.",
        "Second dot this week, 7.4 h of sleep. What you repeat is what you become.",
        "A long walk, then 52 min easy and 9 km more; 2 of 3 dots by Thursday is plenty.",
    ],
)
def test_wrap_keeps_a_number_with_its_unit_or_of_n(line):
    lines = wrap_lines(line)
    assert " ".join(lines) == line
    for group in (
        "(10 of 12);",
        "9.8 h",
        "(3 of 12);",
        "6.2 h",
        "7.4 h",
        "52 min",
        "9 km",
        "2 of 3",
    ):
        if group in line:
            assert any(group in wrapped for wrapped in lines), (group, lines)


def test_a_group_wider_than_a_line_still_wraps():
    lines = wrap_lines("Read 1234567 of 7654321 pages.")
    assert " ".join(lines) == "Read 1234567 of 7654321 pages."


# 8. gates for mutants that survived


def test_age_label_switches_to_days_at_48_hours():
    assert [age_label(h) for h in (0.5, 1.0, 24.0, 25.9, 47.9, 48.0, 71.9, 72.0)] == [
        "SEEN <1H AGO",
        "SEEN 1H AGO",
        "SEEN 24H AGO",
        "SEEN 25H AGO",
        "SEEN 47H AGO",
        "SEEN 2D AGO",
        "SEEN 2D AGO",
        "SEEN 3D AGO",
    ]


def test_a_reading_captured_on_the_requested_day_is_used(db, settings):
    for day, pct in (("2026-09-29", 11.0), ("2026-09-30", 41.2)):
        db.execute(
            "INSERT INTO daily_metrics VALUES (?, ?)",
            (day, json.dumps({"claude_week_used_pct": pct})),
        )
    assert view_from_db(db, settings, "2026-09-30").claude.used_pct == 41.2


def test_adapter_sends_each_frame_its_own_duration():
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"error_code": 0, "PicId": 3})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    frame = new_frame()
    PixooAdapter(HOST, client).send(Clip((frame,) * 3, (100, 250, 1200)))
    assert [b["PicSpeed"] for b in bodies[2:]] == [100, 250, 1200]


def test_party_is_earned_at_the_target_not_only_above(settings):
    from app.render.celebrate import celebrations_for

    view, _ = load(WEEK_COMPLETE, settings)
    assert (view.week_dots, view.week_target) == (3, 3)
    assert [c.earned for c in celebrations_for(view) if c.name == "party"] == [True]
    below = replace(view, week_dots=2)
    assert [c.earned for c in celebrations_for(below) if c.name == "party"] == [False]


@pytest.mark.parametrize(
    "name",
    [
        "../../config",
        "..%2F..%2Fconfig",
        "%2Fetc%2Fpasswd",
        "train__all-sources__alive__base.json",
        "train__all-sources__alive__base/../train__all-sources__alive__base",
        "",
    ],
)
def test_preview_fixture_names_cannot_leave_the_fixture_folder(client, name):
    assert client.get(f"/preview?fixture={name}").status_code == 404
    assert client.get(f"/preview/image/week?fixture={name}").status_code == 404


def test_curly_apostrophe_is_drawn_as_a_straight_one(monkeypatch, settings):
    from app.render.screens import render_books

    view, _ = load(WEEK_41, settings)
    drawn = record_text(monkeypatch)
    render_books(replace(view, summary_line="It’s enough."))
    assert ("It's", 2, 45, "body-5x7", 1) in drawn
    assert not [t for t in texts(drawn) if "\u2019" in t or "?" in t]


# 4. the view looks up exact keys; rollover rows are the metrics engine's job


def test_view_reads_only_the_exact_day_and_week_rows(db, settings):
    db.execute(
        "INSERT INTO daily_metrics VALUES ('2026-10-04', ?)",
        (json.dumps({"books_ytd": 3, "summary_device_line": "Sunday.", "quality_workout": True}),),
    )
    db.execute(
        "INSERT INTO weekly_metrics VALUES ('2026-09-28', ?)",
        (json.dumps({"quality_workouts": 3, "weeks_hit_streak": 4}),),
    )
    sunday = view_from_db(db, settings, "2026-10-04")
    assert (sunday.books_ytd, sunday.summary_line, sunday.week_dots, sunday.streak_weeks) == (
        3,
        "Sunday.",
        3,
        4,
    )
    monday = view_from_db(db, settings, "2026-10-05")
    assert (monday.books_ytd, monday.summary_line) == (None, None)
    assert (monday.week_dots, monday.streak_weeks) == (None, None)
    # the one stated exception: Monday has no health data yet, so the Today screen shows
    # Sunday's day facts and says whose they are
    assert (monday.day_shown, monday.today_dot) == ("2026-10-04", True)
    assert sunday.day_shown is None


def test_today_screen_is_headed_yesterday_only_when_it_shows_the_day_before(
    db, settings, monkeypatch
):
    db.execute(
        "INSERT INTO daily_metrics VALUES ('2026-10-04', ?)",
        (
            json.dumps(
                {"sleep_hours": 7.6, "steps": 9100, "quality_workout": False, "workout_count": 0}
            ),
        ),
    )
    db.execute(
        "INSERT INTO daily_metrics VALUES ('2026-10-05', ?)",
        (json.dumps({"quality_workout": False, "workout_count": 0}),),
    )
    monday = view_from_db(db, settings, "2026-10-05")
    assert (monday.day_shown, monday.sleep_hours, monday.steps) == ("2026-10-04", 7.6, 9100)
    drawn = record_text(monkeypatch)
    render_today(monday)
    assert [item[0] for item in drawn if item[2] == 2] == ["YESTERDAY", "SUN 4"]
    assert ("NO WORKOUT", 13, 42, "small-3x5", 1) in drawn

    tuesday = view_from_db(db, settings, "2026-10-06")
    assert (tuesday.day_shown, tuesday.sleep_hours) == (None, None)
    drawn.clear()
    render_today(tuesday)
    assert [item[0] for item in drawn if item[2] == 2] == ["TODAY", "TUE 6"]
    assert "NO WORKOUT" not in texts(drawn)

    sunday = view_from_db(db, settings, "2026-10-04")
    assert sunday.day_shown is None
    drawn.clear()
    render_today(sunday)
    assert [item[0] for item in drawn if item[2] == 2] == ["TODAY", "SUN 4"]


def test_a_null_sleep_row_is_not_overridden_by_a_stored_session(settings):
    daily = {"sleep_hours": None, "sleep_win": False}
    view = view_from_metrics("2026-10-06", daily, {}, settings, stored_sleep_hours=7.5)
    assert view.sleep_hours is None
    assert "sleep" not in earned_wins(view)
    interim = view_from_metrics("2026-10-06", {}, {}, settings, stored_sleep_hours=7.5)
    assert interim.sleep_hours == 7.5


def test_a_word_one_mark_too_wide_drops_the_mark_instead_of_splitting(settings):
    from app.render.screens import LINE_WIDTH

    lines = wrap_lines("sleep unrecorded; the walking counts")
    assert lines[:2] == ["sleep", "unrecorded"]
    assert lines[2].startswith("the")
    assert all(text_width(line, BODY) <= LINE_WIDTH for line in lines)
    assert wrap_lines("rest day; walk")[0] == "rest day;"


def test_a_summary_with_no_drawable_character_wraps_the_placeholder(settings):
    view, _ = load(WEEK_41, settings)
    for line in ("😀🎉", "中文"):
        clip = render_books(replace(view, summary_line=line))
        assert (
            clip.frames[0].tobytes()
            == render_books(replace(view, summary_line=None)).frames[0].tobytes()
        )
        assert clip.frames[0].crop((62, 45, 64, 62)).getbbox() is None


@pytest.mark.parametrize("day", ["2026-09-28", "2026-09-30", "2026-10-13", "2026-10-03"])
def test_the_yesterday_header_never_touches_the_date(day, settings):
    view, now = load(WEEK_41, settings)
    frame = render_today(replace(view, day_local="2026-12-25", day_shown=day), now).frames[0]
    title_end = 2 + text_width("YESTERDAY", SMALL)
    gap = frame.crop((title_end, 0, title_end + 3, 9))
    assert gap.getbbox() is None
    assert frame.crop((title_end + 3, 0, 64, 9)).getbbox() is not None
