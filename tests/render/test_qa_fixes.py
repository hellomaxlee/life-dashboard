"""Gates added after Lucía's QA of Phase 1b (2026-10-02). One test per finding."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest

from app.render.font import BODY, text_width
from app.render.frame import Clip, new_frame
from app.render.screens import render_books, render_today, render_week
from app.render.view import ClaudeUsage, DayView, view_from_metrics
from app.timeutil import from_utc_iso, to_utc_iso
from tests.render import COMBOS, WEEK_41, load

AMBER = (255, 170, 0)
TRACK = (34, 36, 54)
LINE_109 = (
    "A hotel-gym session with no heart-rate data, so no dot; 6.9 h of sleep. "
    "You control the packing, not the day."
)


def lit_columns(frame, top: int, bottom: int) -> list[int]:
    box = frame.crop((0, top, 64, bottom + 1)).getbbox()
    return [] if box is None else [box[0], box[2] - 1]


# 1. the summary reaches the device: pages, not a pixel scroll


@pytest.mark.parametrize("combo", COMBOS)
def test_every_clip_fits_the_device_frame_limit(combo, settings):
    from app.render.celebrate import celebrations_for
    from app.render.frame import MAX_CLIP_FRAMES
    from app.render.rotation import rotation_clips

    view, now = load(combo, settings)
    clips = rotation_clips(view, now) + [(c.name, c.clip) for c in celebrations_for(view)]
    assert [name for name, _ in clips] == ["week", "today", "books", "sparkle", "party"]
    assert MAX_CLIP_FRAMES == 59
    for name, clip in clips:
        assert len(clip.frames) <= MAX_CLIP_FRAMES, f"{name}: {len(clip.frames)} frames"


def test_summary_is_paged_two_lines_at_a_time_without_splitting_words(settings):
    from app.render.screens import LINE_WIDTH, PAGE_MS, wrap_pages

    assert len(LINE_109) == 109
    pages = wrap_pages(LINE_109)
    lines = [line for page in pages for line in page]
    assert all(1 <= len(page) <= 2 for page in pages)
    assert all(text_width(line, BODY) <= LINE_WIDTH for line in lines)
    assert " ".join(lines).split() == LINE_109.split()

    view, _ = load(WEEK_41, settings)
    clip = render_books(replace(view, summary_line=LINE_109))
    assert len(clip.frames) == len(pages) <= 12
    assert set(clip.durations_ms) == {PAGE_MS} and PAGE_MS == 2000
    for frame in clip.frames:
        assert frame.crop((0, 0, 64, 44)).tobytes() == clip.frames[0].crop((0, 0, 64, 44)).tobytes()
        assert frame.crop((0, 45, 64, 62)).getbbox() is not None
        assert frame.crop((62, 45, 64, 62)).getbbox() is None
    assert len(render_books(replace(view, summary_line="Rest day.")).frames) == 1


def test_only_a_word_longer_than_a_line_is_split():
    from app.render.screens import LINE_WIDTH, wrap_pages

    lines = [line for page in wrap_pages("Rest Supercalifragilistic day") for line in page]
    assert all(text_width(line, BODY) <= LINE_WIDTH for line in lines)
    assert lines[0] == "Rest" and lines[-1].endswith("day")
    assert "".join(lines).replace(" ", "") == "RestSupercalifragilisticday"


def test_summary_over_110_chars_is_cut_at_a_word_with_an_ellipsis():
    from app.render.screens import SUMMARY_MAX_CHARS, fit_summary, wrap_pages

    assert SUMMARY_MAX_CHARS == 110
    assert fit_summary(LINE_109) == LINE_109
    assert fit_summary(LINE_109 + ".") == LINE_109 + "."
    long = LINE_109 + " And then some more words that will not fit."
    cut = fit_summary(long)
    assert len(cut) <= 110
    assert cut.endswith("...")
    assert long.startswith(cut[:-3])
    assert long[len(cut) - 3] in " .,;:"
    assert wrap_pages(long)[-1][-1].endswith("...")
    assert len(wrap_pages("x" * 400)) < 59


def test_pixoo_refuses_a_clip_over_the_frame_limit():
    from app.render.adapters import pixoo

    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"ReturnCode": 0})

    adapter = pixoo.PixooAdapter(
        "192.168.1.50", httpx.Client(transport=httpx.MockTransport(handler))
    )
    frame = new_frame()
    with pytest.raises(pixoo.PixooError, match="60 frames"):
        adapter.send(Clip((frame,) * 60, (40,) * 60))
    assert sent == []
    assert adapter.send(Clip((frame,) * 59, (40,) * 59)).frames_sent == 59
    assert not hasattr(pixoo, "thin")


# 3. the percent label agrees with the bar


def test_percent_label_truncates_and_last_pixel_needs_100():
    from app.render.usage import fill_width, percent_text

    assert [percent_text(p) for p in (84.9, 99.6, 100.0, 41.2, 0.0)] == [
        "84%",
        "99%",
        "100%",
        "41%",
        "0%",
    ]
    assert percent_text(0.4) == "<1%"
    assert [fill_width(p) for p in (0.0, 0.4, 41.2, 99.6, 99.99, 100.0)] == [0, 1, 24, 59, 59, 60]


# 4 and 5a. what counts as stale


def test_a_passed_reset_time_marks_the_reading_stale(settings):
    view, now = load(WEEK_41, settings)
    later = from_utc_iso(view.claude.resets_at_utc) + timedelta(minutes=5)
    fresh_capture = replace(view.claude, captured_at_utc=to_utc_iso(later - timedelta(hours=1)))
    clip = render_week(replace(view, claude=fresh_capture), later)
    assert len(clip.frames) == 16
    assert clip.frames[0].getpixel((60, 49)) == AMBER
    assert AMBER in {clip.frames[0].getpixel((x, 56)) for x in range(2, 62)}


def test_a_reading_with_no_capture_time_is_stale(settings):
    from app.render.usage import usage_state

    view, now = load(WEEK_41, settings)
    reading = replace(view.claude, captured_at_utc=None)
    state = usage_state(reading, now, 24)
    assert state.stale and state.age_label == "AGE UNKNOWN"
    clip = render_week(replace(view, claude=reading), now)
    assert len(clip.frames) == 16
    assert clip.frames[0].getpixel((60, 49)) == AMBER


# 5b. the latest reading on or before the day


def test_view_from_db_takes_the_latest_reading_on_or_before_the_day(db, settings):
    from app.render.view_db import view_from_db

    for day, pct in (("2026-09-27", 11.0), ("2026-09-29", 41.2), ("2026-10-01", 99.0)):
        db.execute(
            "INSERT INTO daily_metrics VALUES (?, ?)",
            (day, json.dumps({"claude_week_used_pct": pct, "claude_week_resets_at": None})),
        )
    assert view_from_db(db, settings, "2026-09-30").claude.used_pct == 41.2
    assert view_from_db(db, settings, "2026-09-28").claude.used_pct == 11.0
    assert view_from_db(db, settings, "2026-09-26").claude == ClaudeUsage()


# 6. out-of-range stored values are missing values


def test_out_of_range_values_become_no_data(settings):
    empty = view_from_metrics("2026-09-30", {}, {}, settings)
    for pct in (-5, -0.1, 100.5, 1e6, float("nan"), float("inf")):
        view = view_from_metrics("2026-09-30", {"claude_week_used_pct": pct}, {}, settings)
        assert view.claude == ClaudeUsage(), pct
    for ok in (0, 100, 100.0):
        view = view_from_metrics("2026-09-30", {"claude_week_used_pct": ok}, {}, settings)
        assert view.claude.used_pct == ok
    for hours in (-1.0, 24.5, float("nan")):
        view = view_from_metrics("2026-09-30", {"sleep_hours": hours}, {}, settings)
        assert view.sleep_hours is None, hours
        stored = view_from_metrics("2026-09-30", {}, {}, settings, stored_sleep_hours=hours)
        assert stored.sleep_hours is None, hours
    bad_counts = view_from_metrics(
        "2026-09-30",
        {"steps": -4, "books_ytd": 2.5},
        {"quality_workouts": -1, "weeks_hit_streak": 1.5},
        settings,
        stored_steps=-9,
    )
    assert bad_counts == empty
    assert render_today(replace(empty, sleep_hours=-1.0)).poster.tobytes() == (
        render_today(empty).poster.tobytes()
    )
    week = render_week(
        replace(empty, claude=ClaudeUsage(-5.0)), from_utc_iso("2026-09-30T12:00:00Z")
    )
    assert (
        week.poster.tobytes()
        == render_week(empty, from_utc_iso("2026-09-30T12:00:00Z")).poster.tobytes()
    )


# 7. the sleep bar never rounds up to the goal


def test_sleep_bar_fill_is_floored(settings):
    view, _ = load(WEEK_41, settings)
    at_goal = render_today(replace(view, sleep_hours=7.0)).poster
    short = render_today(replace(view, sleep_hours=6.95)).poster
    assert at_goal.getpixel((43, 27)) == (60, 220, 90)
    assert short.getpixel((43, 27)) == TRACK
    assert short.getpixel((42, 27)) == (80, 170, 255)
    assert at_goal.getpixel((44, 27)) == short.getpixel((44, 27)) == (255, 255, 255)


# 8. a three-digit book count fits


@pytest.mark.parametrize("count", [0, 9, 12, 99, 100, 365, 999, 1000, 12345])
def test_book_count_stays_inside_the_frame(count, settings):
    view, _ = load(WEEK_41, settings)
    frame = render_books(replace(view, books_ytd=count)).poster
    left, right = lit_columns(frame, 8, 23)
    assert left >= 2 and right <= 61, (count, left, right)
    wide = render_books(replace(view, books_ytd=count, books_target=120)).poster
    assert lit_columns(wide, 8, 23)[1] <= 61


# 9. celebrations are samples unless the day earned them


def test_celebrations_are_marked_sample_unless_earned(settings, client, capsys):
    from app.render.celebrate import celebrations_for, party_clip
    from tools.render import main as render_main

    plain = DayView(day_local="2026-10-02")
    assert [(c.name, c.earned) for c in celebrations_for(plain)] == [
        ("sparkle", False),
        ("party", False),
    ]
    two_dots, _ = load(WEEK_41, settings)
    assert [(c.name, c.earned) for c in celebrations_for(two_dots)] == [
        ("sparkle", True),
        ("party", False),
    ]
    four = replace(two_dots, week_dots=4, today_dot=None, sleep_hours=6.0)
    sparkle, party = celebrations_for(four)
    assert (sparkle.earned, party.earned) == (True, True), "a finished week keeps the workout win"
    short = replace(two_dots, week_dots=2, today_dot=None, sleep_hours=6.0)
    assert [c.earned for c in celebrations_for(short)] == [False, False]
    assert party.clip.poster.tobytes() == party_clip(4, 3).poster.tobytes()
    assert party.clip.poster.tobytes() != party_clip(3, 3).poster.tobytes()

    page = client.get(f"/preview?fixture={WEEK_41}").text
    assert "party (sample)" in page and "sparkle (sample)" not in page
    assert "sparkle (sample)" in client.get("/preview?date=2026-10-02").text
    render_main(["--date", "2026-10-02", "--out", str(settings.storage.raw_dir / "out")])
    out = capsys.readouterr().out
    assert "sparkle (sample): 20 frames" in out and "party (sample): 56 frames" in out


# 10. the Pixoo host must be a home-LAN address


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",
        "255.255.255.255",
        "169.254.10.10",
        "240.0.0.1",
        "198.18.0.1",
        "192.0.2.10",
        "172.32.0.1",
        "172.15.255.255",
        "11.0.0.1",
        "100.64.0.1",
        "192.169.0.1",
        "::1",
        "fd00::1",
    ],
)
def test_pixoo_host_guard_rejects_everything_but_rfc1918(host):
    from app.render.adapters.pixoo import require_lan_host

    with pytest.raises(ValueError):
        require_lan_host(host)


def test_pixoo_host_guard_accepts_the_three_private_ranges():
    from app.render.adapters.pixoo import require_lan_host

    for host in ("10.0.0.9", "172.16.0.1", "172.31.255.254", "192.168.1.50"):
        assert require_lan_host(host) == host


# 11. renderers load no ingest or database code


def test_renderers_import_no_ingest_db_or_network_code():
    code = (
        "import sys\n"
        "import app.render.screens, app.render.usage, app.render.celebrate\n"
        "import app.render.rotation, app.render.view, app.render.gamma, app.render.font\n"
        "banned = ('app.ingest', 'app.db', 'app.main', 'app.web', 'sqlite3', 'fastapi', 'httpx')\n"
        "print(sorted(m for m in sys.modules if m.startswith(banned)))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-400:]
    assert result.stdout.strip() == "[]"


def test_view_key_names_match_what_ingest_writes():
    from app.ingest import claude_usage
    from app.render import view

    assert (view.CLAUDE_USED_PCT, view.CLAUDE_RESETS_AT, view.CLAUDE_CAPTURED_AT) == (
        claude_usage.USED_PCT,
        claude_usage.RESETS_AT,
        claude_usage.CAPTURED_AT,
    )


# 12. the colour steps are marked by position, not hue alone


def test_usage_track_has_ticks_at_60_and_85(settings):
    view, now = load(WEEK_41, settings)
    for pct in (5.0, 41.2, 70.0, 100.0):
        frame = render_week(replace(view, claude=replace(view.claude, used_pct=pct)), now).poster
        for y in (41, 45):
            lit = [x for x in range(64) if frame.getpixel((x, y)) != (0, 0, 0)]
            assert lit == [37, 52], (pct, y, lit)
    amber_starts = render_week(replace(view, claude=replace(view.claude, used_pct=61.0)), now)
    assert amber_starts.poster.getpixel((37, 42)) == AMBER


# the entry point the scheduler calls


def test_rotation_clips_is_the_ordered_device_ready_rotation(settings):
    from app.render.frame import MAX_CLIP_FRAMES
    from app.render.rotation import rotation_clips

    view, now = load(WEEK_41, settings)
    clips = rotation_clips(view, now)
    assert [name for name, _ in clips] == ["week", "today", "books"]
    assert all(isinstance(clip, Clip) for _, clip in clips)
    assert clips[0][1].poster.tobytes() == render_week(view, now).poster.tobytes()
    assert clips[1][1].poster.tobytes() == render_today(view).poster.tobytes()
    assert clips[2][1].poster.tobytes() == render_books(view).poster.tobytes()
    empty = rotation_clips(DayView(day_local="2026-10-02"), now)
    assert [name for name, _ in empty] == ["week", "today", "books"]
    assert all(clip.poster.getbbox() and len(clip.frames) <= MAX_CLIP_FRAMES for _, clip in empty)
