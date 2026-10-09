from __future__ import annotations

import io
import json
import string
from dataclasses import replace

import pytest
from PIL import Image, ImageChops

from app.render.adapters.file import FileAdapter, gif_bytes, png_bytes
from app.render.celebrate import celebrations_for, party_clip, sparkle_clip
from app.render.font import BODY, SMALL, draw_text, text_width
from app.render.frame import SIZE, Clip, new_frame, still, upscale
from app.render.gamma import led_gamma, led_lut
from app.render.palette import SPINES
from app.render.rotation import PAGED_FRAME_MS, rotation_clips
from app.render.screens import as_of_label, render_books, render_today, sleep_text
from app.render.view import DayView
from app.timeutil import from_utc_iso
from tests.render import (
    COMBOS,
    DAYS,
    NO_READING,
    RED_91,
    REQUIRED_COMBOS,
    STALE,
    WEEK_41,
    load,
    rotation,
)

NOW = from_utc_iso("2026-10-02T16:00:00Z")


def lit(frame: Image.Image) -> int:
    return sum(1 for pixel in frame.get_flattened_data() if pixel != (0, 0, 0))


def test_clip_contract_rejects_bad_frames():
    good = new_frame()
    assert still(good).durations_ms == (8000,)
    with pytest.raises(ValueError):
        Clip((), ())
    with pytest.raises(ValueError):
        Clip((good,), (100, 100))
    with pytest.raises(ValueError):
        Clip((good,), (0,))
    with pytest.raises(ValueError):
        Clip((Image.new("RGB", (32, 32)),), (100,))
    with pytest.raises(ValueError):
        Clip((Image.new("RGBA", (64, 64)),), (100,))


def test_the_display_never_blanks_on_an_empty_view():
    clips = rotation(DayView(day_local="2026-10-02"), NOW)
    assert list(clips) == ["today", "city", "week", "month", "books"]
    for name, clip in clips.items():
        for frame in clip.frames:
            assert frame.size == (SIZE, SIZE) and frame.mode == "RGB"
            assert lit(frame) > 150, name
            assert lit(led_gamma(frame, brightness=0.2)) > 150, name
    assert len(clips["week"].frames) == 1


@pytest.mark.parametrize("combo", COMBOS)
def test_every_screen_and_clip_renders_at_1x_and_8x(combo, settings):
    view, now = load(combo, settings)
    clips = rotation_clips(view, now) + [(c.name, c.clip) for c in celebrations_for(view)]
    assert [name for name, _ in clips] == [
        "today",
        "city",
        "week",
        "month",
        "books",
        "sparkle",
        "party",
    ]
    for _, clip in clips:
        for scale in (1, 8):
            still_image = Image.open(io.BytesIO(png_bytes(clip.poster, scale)))
            assert still_image.size == (64 * scale, 64 * scale)
            assert still_image.convert("RGB").getpixel((5 * scale, 5 * scale)) == (
                clip.poster.getpixel((5, 5))
            )
        big = upscale(led_gamma(clip.poster), 8)
        assert big.size == (512, 512)


def test_gif_round_trips_frames_and_durations(settings):
    view, now = load(STALE, settings)
    clip = rotation(view, now)["week"]
    for scale in (1, 8):
        gif = Image.open(io.BytesIO(gif_bytes(clip, scale)))
        assert gif.size == (64 * scale, 64 * scale)
        assert gif.n_frames == len(clip.frames)
        total = 0
        for index in range(gif.n_frames):
            gif.seek(index)
            total += gif.info["duration"]
            if scale == 1:
                same = ImageChops.difference(gif.convert("RGB"), clip.frames[index]).getbbox()
                assert same is None, index
        assert total == clip.total_ms


def test_file_adapter_writes_poster_and_gif(tmp_path, settings):
    view, now = load(WEEK_41, settings)
    clips = rotation(view, now)
    assert [p.name for p in FileAdapter(tmp_path).send(clips["week"], "week")] == ["week.png"]
    written = FileAdapter(tmp_path, scale=8, transform=led_gamma).send(clips["books"], "books")
    assert [p.name for p in written] == ["books.png", "books.gif"]
    assert Image.open(written[0]).size == (512, 512)


def test_books_shelf_counts_spines(settings):
    view, _ = load(WEEK_41, settings)
    for read in (0, 3, 12, 15):
        frame = render_books(replace(view, books_ytd=read)).poster
        lit_spines = 0
        for index in range(12):
            if frame.getpixel((2 + index * 5, 40)) == SPINES[index % len(SPINES)]:
                lit_spines += 1
        assert lit_spines == min(read, 12)


def test_today_states(settings):
    view, _ = load(WEEK_41, settings)
    assert as_of_label(view) == "AS OF 6:10PM"
    delayed, _ = load(STALE, settings)
    assert as_of_label(delayed) == "AS OF TUE 10:10P"
    assert as_of_label(replace(view, as_of_utc=None)) is None
    winter = replace(view, day_local="2026-01-15", as_of_utc="2026-01-15T23:10:00Z")
    assert as_of_label(winter) == "AS OF 6:10PM"

    green, sky = (60, 220, 90), (80, 170, 255)
    met = render_today(view).poster
    assert met.getpixel((2, 27)) == green
    short, _ = load(RED_91, settings)
    frame = render_today(short).poster
    assert frame.getpixel((2, 27)) == sky
    assert not any(p == (255, 60, 50) for p in frame.get_flattened_data())
    missing, _ = load(NO_READING, settings)
    assert render_today(missing).poster.getpixel((2, 27)) == (34, 36, 54)
    for any_view in (view, short, missing):
        assert render_today(any_view).poster.getpixel((44, 25)) == (255, 255, 255)


def test_sleep_is_truncated_never_rounded_up():
    assert sleep_text(6.96) == "6.9"
    assert sleep_text(7.0) == "7.0"
    assert sleep_text(7.4) == "7.4"
    assert sleep_text(8.2) == "8.2"


def test_celebrations_are_deterministic_colourful_and_short():
    for build in (lambda: sparkle_clip("sleep"), lambda: party_clip(3, 3)):
        first, second = build(), build()
        assert [f.tobytes() for f in first.frames] == [f.tobytes() for f in second.frames]
        colours = {p for frame in first.frames for p in frame.get_flattened_data()}
        assert len(colours) > 20
        assert first.total_ms <= 6000
        assert all(lit(frame) > 20 for frame in first.frames)
        assert min(first.durations_ms) >= PAGED_FRAME_MS, "steps, sent as stills: no loading"
    assert len(sparkle_clip().frames) == 5
    assert sparkle_clip().total_ms == 1500, "five steps of 300 ms: about 9 s on the panel"
    assert len(party_clip().frames) == 6
    assert party_clip(5, 5).poster.size == (64, 64)


def test_fonts_cover_what_the_screens_print():
    for char in string.printable:
        if char in "\t\n\r\x0b\x0c":
            continue
        assert char in BODY.glyphs, char
    for font in (BODY, SMALL):
        for char, rows in font.glyphs.items():
            assert len(rows) == font.height, char
            assert len({len(row) for row in rows}) == 1, char
    for char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 :.,%<-/":
        assert char in SMALL.glyphs, char
    shapes = [SMALL.glyphs[c] for c in SMALL.glyphs if c != " "]
    assert len(set(shapes)) == len(shapes)
    assert SMALL.glyph("a") == SMALL.glyph("A")
    assert SMALL.glyph("é") == SMALL.glyph("?")


def test_text_width_matches_drawn_extent():
    for font, text in ((SMALL, "RESETS IN 3D"), (BODY, "Second dot, 7.4 h")):
        frame = Image.new("RGB", (200, 20))
        end = draw_text(frame, 3, 2, text, (255, 255, 255), font)
        box = frame.getbbox()
        assert box[0] == 3
        assert box[2] - box[0] == text_width(text, font)
        assert end == 3 + text_width(text, font) + 1
    assert text_width("RESETS IN 3D", SMALL) <= 60
    assert text_width("AS OF TUE 22:10", SMALL) <= 60
    assert text_width("WORKOUT DONE", SMALL) <= 64 - 14


def test_led_gamma_curve():
    lut = led_lut()
    assert lut[0] == 0 and lut[255] == 255
    assert lut == sorted(lut)
    assert lut[34] == round(255 * (34 / 255) ** (1 / 2.2)) == 102
    assert led_lut(panel_gamma=2.2) == list(range(256))
    night = led_lut(brightness=0.2)
    assert night[4] == 0 and night[5] > 0
    frame = new_frame((34, 36, 54))
    shown = led_gamma(frame)
    assert shown.size == (64, 64) and shown.mode == "RGB"
    assert shown.getpixel((0, 0)) == (lut[34], lut[36], lut[54])
    assert frame.getpixel((0, 0)) == (34, 36, 54)
    with pytest.raises(ValueError):
        led_lut(brightness=1.5)


def test_fixture_files_name_their_cell_and_cover_the_required_states(settings):
    paths = sorted(DAYS.glob("*.json"))
    assert {p.stem for p in paths} >= set(REQUIRED_COMBOS)
    assert {p.stem for p in paths} == set(COMBOS)
    used = []
    for path in paths:
        assert len(path.stem.split("__")) == 4
        record = json.loads(path.read_text())
        assert record["combo"].lower().replace(" / ", "__").replace(" ", "-") == path.stem.lower()
        line = record["daily_metrics"].get("summary_device_line", "")
        assert len(line) <= 110
        assert "!" not in line
        used.append(record["daily_metrics"].get("claude_week_used_pct"))
    assert 41.2 in used and None in used


def test_a_night_over_nine_hours_is_yellow_and_not_a_sleep_win():
    from app.render.celebrate import earned_wins
    from app.render.palette import GREEN, SKY, YELLOW
    from app.render.screens import sleep_goal_label

    def number_colours(hours: float) -> set:
        frame = render_today(DayView(day_local="2026-10-02", sleep_hours=hours)).poster
        return {frame.getpixel((x, y)) for x in range(16, 62) for y in range(9, 24)}

    for hours, colour, win in (
        (6.96, SKY, False),
        (7.0, GREEN, True),
        (9.09, GREEN, True),
        (9.1, YELLOW, False),
        (9.83, YELLOW, False),
    ):
        view = DayView(day_local="2026-10-02", sleep_hours=hours)
        assert number_colours(hours) == {(0, 0, 0), colour}, hours
        assert ("sleep" in earned_wins(view)) is win, hours
    view = DayView(day_local="2026-10-02", sleep_hours=8.0)
    assert sleep_goal_label(view) == "GOAL 7-9H"
    frame = render_today(view).poster
    assert [frame.getpixel((x, 25)) for x in (2 + 42, 2 + 54)] == [(255, 255, 255)] * 2, (
        "a tick at each end of the band"
    )


@pytest.mark.parametrize("win", ["workout", "sleep", "book"])
def test_every_sparkle_step_is_a_different_picture(win, monkeypatch):
    from tests.render import record_text

    drawn = record_text(monkeypatch)
    frames = sparkle_clip(win).frames
    for step, (before, after) in enumerate(zip(frames, frames[1:], strict=False)):
        moved = ImageChops.difference(before, after).convert("L").point(lambda v: 255 if v else 0)
        assert moved.histogram()[255] >= 100, f"step {step} to {step + 1} barely moves"
    assert [text for text, *_ in drawn].count("SMALL WIN") == 3, "the label arrives with step 3"
    low = frames[0].crop((0, 34, SIZE, SIZE)).getbbox()
    assert low is not None and frames[0].crop((20, 14, 44, 34)).getbbox() is None, (
        "the icon starts low and rises"
    )
    assert frames[1].crop((0, 0, SIZE, 4)).getbbox() is not None, "the rays reach the edge"
