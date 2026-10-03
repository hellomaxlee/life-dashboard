"""Issue #2 acceptance for the Week screen's Claude usage bar. Assertions are on pixels."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from PIL import Image, ImageChops

from app.render.gamma import led_gamma
from app.render.screens import render_week
from app.render.usage import reset_label
from app.render.view import ClaudeUsage, view_from_metrics
from app.timeutil import from_utc_iso, to_utc_iso
from tests.render import NO_READING, RED_91, STALE, WEEK_41, WEEK_COMPLETE, load

GREEN = (60, 220, 90)
AMBER = (255, 170, 0)
RED = (255, 60, 50)
TRACK = (34, 36, 54)
BAR_ROWS = (42, 43, 44)
BAR_X = range(2, 62)
STALE_DOT = (60, 49)


def bar_row(frame: Image.Image, y: int) -> list[tuple[int, int, int]]:
    return [frame.getpixel((x, y)) for x in range(64)]


def filled_columns(frame: Image.Image, color: tuple[int, int, int]) -> list[int]:
    columns = [x for x in range(64) if frame.getpixel((x, BAR_ROWS[0])) == color]
    for y in BAR_ROWS[1:]:
        assert [x for x in range(64) if frame.getpixel((x, y)) == color] == columns
    return columns


def test_41_2_percent_fills_24_of_60_pixels(settings):
    view, now = load(WEEK_41, settings)
    assert view.claude.used_pct == 41.2
    clip = render_week(view, now)
    assert len(clip.frames) == 1
    frame = clip.poster
    # floor(0.412 * 60) = 24 px: columns 2 through 25. The fill never overstates.
    assert filled_columns(frame, GREEN) == list(range(2, 26))
    assert filled_columns(frame, TRACK) == list(range(26, 62))
    for y in (40, 46):
        assert all(pixel == (0, 0, 0) for pixel in bar_row(frame, y))


def test_41_2_percent_width_survives_led_gamma(settings):
    view, now = load(WEEK_41, settings)
    shown = led_gamma(render_week(view, now).poster)
    green = shown.getpixel((2, 42))
    track = shown.getpixel((61, 42))
    assert green != track
    assert filled_columns(shown, green) == list(range(2, 26))
    assert filled_columns(shown, track) == list(range(26, 62))


def test_mutant_draws_a_different_width(settings, monkeypatch):
    view, now = load(WEEK_41, settings)
    monkeypatch.setenv("USAGE_BAR_MUTANT_REMAINING", "1")
    mutated = render_week(view, now).poster
    assert len(filled_columns(mutated, GREEN)) == 35


def test_missing_reading_says_no_data_and_leaves_the_week_intact(settings):
    view, now = load(NO_READING, settings)
    assert view.claude == ClaudeUsage()
    clip = render_week(view, now)
    assert len(clip.frames) == 1
    frame = clip.poster
    for y in BAR_ROWS:
        row = bar_row(frame, y)
        assert not {GREEN, AMBER, RED} & set(row)
        assert [x for x in range(64) if row[x] == TRACK] == [x for x in BAR_X if (x - 2) % 4 < 2]

    with_reading = replace(
        view,
        claude=ClaudeUsage(41.2, to_utc_iso(now + timedelta(days=3)), to_utc_iso(now)),
    )
    other = render_week(with_reading, now).poster
    box = (0, 0, 64, 40)
    assert ImageChops.difference(frame.crop(box), other.crop(box)).getbbox() is None
    assert frame.crop(box).getbbox() is not None
    label_band = (0, 47, 64, 52)
    assert frame.crop(label_band).getbbox() is not None
    assert ImageChops.difference(frame.crop(label_band), other.crop(label_band)).getbbox()
    assert frame.crop((0, 54, 64, 64)).getbbox() is None


def test_colour_steps_green_amber_red(settings):
    view, now = load(WEEK_41, settings)
    for pct, color in ((59.9, GREEN), (60.0, AMBER), (84.9, AMBER), (85.0, RED), (100.0, RED)):
        frame = render_week(replace(view, claude=replace(view.claude, used_pct=pct)), now).poster
        assert frame.getpixel((2, 42)) == color, pct
    assert render_week(*load(RED_91, settings)).poster.getpixel((2, 42)) == RED
    assert render_week(*load(WEEK_COMPLETE, settings)).poster.getpixel((2, 42)) == AMBER


def test_extremes_never_overflow_the_track(settings):
    view, now = load(WEEK_41, settings)
    for pct, width in ((0.0, 0), (0.2, 1), (99.6, 59), (100.0, 60)):
        frame = render_week(replace(view, claude=replace(view.claude, used_pct=pct)), now).poster
        lit = [x for x in range(64) if frame.getpixel((x, 42)) not in (TRACK, (0, 0, 0))]
        assert lit == list(range(2, 2 + width)), pct


def test_stale_reading_pulses_and_shows_its_age(settings):
    view, now = load(STALE, settings)
    clip = render_week(view, now)
    assert len(clip.frames) == 16
    assert set(clip.durations_ms) == {250}
    dot = [frame.getpixel(STALE_DOT) for frame in clip.frames]
    assert len(set(dot)) == 3
    assert (0, 0, 0) not in dot
    assert dot[0] == AMBER
    bars = {tuple(bar_row(frame, 42)) for frame in clip.frames}
    assert len(bars) == 1
    line_2 = (0, 54, 64, 59)
    assert ImageChops.difference(clip.frames[0].crop(line_2), clip.frames[8].crop(line_2)).getbbox()
    assert all(frame.crop(line_2).getbbox() for frame in clip.frames)


def test_stale_boundary_uses_config_hours_and_explicit_now(settings):
    view, _ = load(WEEK_41, settings)
    captured = from_utc_iso(view.claude.captured_at_utc)
    assert view.stale_hours == settings.claude_usage.stale_hours == 24
    at_limit = render_week(view, captured + timedelta(hours=24))
    past_limit = render_week(view, captured + timedelta(hours=24, seconds=1))
    assert len(at_limit.frames) == 1
    assert at_limit.poster.getpixel(STALE_DOT) == (0, 0, 0)
    assert len(past_limit.frames) == 16
    longer = render_week(replace(view, stale_hours=48), captured + timedelta(hours=30))
    assert len(longer.frames) == 1


def test_only_the_seven_day_keys_feed_the_bar(settings):
    five_hour_only = {
        "claude_five_hour_used_pct": 73.5,
        "claude_five_hour_resets_at": "2026-09-30T23:00:00Z",
        "claude_week_captured_at": "2026-09-30T21:40:00Z",
    }
    view = view_from_metrics("2026-09-30", five_hour_only, {}, settings)
    assert view.claude == ClaudeUsage()

    base, now = load(WEEK_41, settings)
    record = {
        "claude_week_used_pct": 41.2,
        "claude_week_resets_at": "2026-10-03T20:00:00Z",
        "claude_week_captured_at": "2026-09-30T21:40:00Z",
        **{k: v for k, v in five_hour_only.items() if "five_hour" in k},
    }
    both = view_from_metrics("2026-09-30", record, {}, settings)
    assert both.claude == base.claude
    assert filled_columns(render_week(both, now).poster, GREEN) == list(range(2, 26))


@pytest.mark.parametrize(
    ("delta", "label"),
    [
        (timedelta(days=2, hours=22), "RESETS IN 3D"),
        (timedelta(days=1, hours=11), "RESETS IN 1D"),
        (timedelta(hours=24), "RESETS IN 1D"),
        (timedelta(hours=8, minutes=5), "RESETS IN 9H"),
        (timedelta(minutes=20), "RESETS IN <1H"),
        (timedelta(0), "RESET PASSED"),
        (timedelta(days=-1), "RESET PASSED"),
    ],
)
def test_reset_label_never_says_this_week(delta, label):
    now = from_utc_iso("2026-09-30T22:15:00Z")
    assert reset_label(to_utc_iso(now + delta), now) == label
    assert reset_label(None, now) == "NO RESET TIME"
