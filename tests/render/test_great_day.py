"""The great day (Max, 2026-11-27): a day with both its quality workout and a night in the sleep
band has its own clip in the rotation, and its calendar cell glows."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.render.celebrate import (
    CELEBRATION_ORDER,
    GREAT_DAY,
    GREAT_DAY_BEATS,
    STEP_MS,
    celebrations_for,
    great_day,
    great_day_clip,
)
from app.render.frame import SIZE
from app.render.month import CELL, GLOW, QUIET_DAY, calendar_layout, render_month
from app.render.palette import CORAL, DAY_GROUNDS, GOLD, GREEN, SKY, TEAL, VIOLET, WHITE, day_ground
from app.render.rotation import device_parts, hold_ms, is_paged, render_screen, sequence_names
from app.render.view import DayView

NOW = datetime(2026, 11, 27, 17, 0, tzinfo=UTC)
BOTH = DayView(day_local="2026-11-27", today_dot=True, sleep_hours=7.5, week_dots=1)
LABEL_ROWS = range(57, 62)


def colours(frame, rows=range(SIZE)) -> set[tuple[int, int, int]]:
    return {frame.getpixel((x, y)) for y in rows for x in range(SIZE)}


def test_a_great_day_is_a_workout_that_day_and_a_night_in_the_band():
    assert great_day(BOTH)
    assert not great_day(replace(BOTH, today_dot=False))
    assert not great_day(replace(BOTH, today_dot=None))
    assert not great_day(replace(BOTH, sleep_hours=None))
    assert not great_day(replace(BOTH, sleep_hours=6.9)), "short of the target"
    assert not great_day(replace(BOTH, sleep_hours=9.5)), "over the band is not a sleep win"
    carried = replace(BOTH, today_dot=False, week_dots=3)
    assert "win-workout" in sequence_names(carried), "a finished week carries the workout win"
    assert not great_day(carried), "but a carried win is not a workout that day"


def test_the_great_day_follows_the_sparkles_and_precedes_the_party():
    assert GREAT_DAY not in sequence_names(replace(BOTH, sleep_hours=6.0))
    assert sequence_names(BOTH)[5:] == ("win-workout", "win-sleep", GREAT_DAY)
    done = replace(BOTH, week_dots=3, book_finished=True)
    assert sequence_names(done)[5:] == ("win-workout", "win-sleep", "win-book", GREAT_DAY, "party")
    assert CELEBRATION_ORDER == ("sparkle", GREAT_DAY, "party")
    assert [(c.name, c.earned) for c in celebrations_for(BOTH)] == [
        ("sparkle", True),
        (GREAT_DAY, True),
        ("party", False),
    ]
    sample = celebrations_for(DayView(day_local="2026-11-27"))[1]
    assert not sample.earned and sample.clip.poster.getbbox() is not None, "a sample to look at"


def test_the_clip_is_five_stills_paged_like_a_sparkle():
    clip = render_screen(GREAT_DAY, BOTH, NOW)
    assert len(clip.frames) == GREAT_DAY_BEATS == 5
    assert clip.durations_ms == (STEP_MS,) * 5 and is_paged(clip)
    assert hold_ms(GREAT_DAY, clip, 6) == hold_ms(GREAT_DAY, clip, 45) == 1500, "one pass"
    assert [len(part.frames) for part in device_parts(clip)] == [1] * 5, "no loading cycle"
    assert len({frame.tobytes() for frame in clip.frames}) == 5, "every step its own picture"
    again = great_day_clip()
    assert [f.tobytes() for f in again.frames] == [f.tobytes() for f in clip.frames], "seeded"


def test_the_clip_shows_both_wins_then_the_rainbow_then_the_words():
    frames = great_day_clip().frames
    for index, frame in enumerate(frames):
        assert {GOLD, VIOLET} <= colours(frame), (index, "the badge and the moon, every step")
        assert frame.getpixel((0, 0)) == (0, 0, 0), "celebrations stay on black"
    bands = {CORAL, GOLD, GREEN, SKY, VIOLET}
    crown = (SIZE // 2, 43 - 25)
    assert [frame.getpixel(crown) == CORAL for frame in frames] == [False, False, False, True, True]
    assert CORAL in colours(frames[2], range(36, 44)), "half risen: the feet are up"
    assert bands <= colours(frames[3], range(18, 24)) and bands <= colours(frames[4], range(18, 24))
    words = [frame.crop((14, 57, 50, 62)).getbbox() is not None for frame in frames]
    assert not words[0] and words[2:] == [True] * 3, "GREAT DAY arrives with the rainbow"
    for frame in frames[2:]:
        assert frame.crop((0, 49, SIZE, 56)).getbbox() is None, "clear air over the words"


@pytest.mark.usefixtures("no_scenery")
def test_a_great_days_cell_glows_and_no_other_does():
    wins = (
        (1, ()),
        (2, ("workout", "sleep")),
        (3, ("workout", "sleep", "book")),
        (4, ("sleep",)),
        (5, ("workout",)),
        (6, ("book",)),
        (9, ("workout", "sleep")),
        (16, ("workout", "sleep")),
    )
    day = "2026-11-20"
    frame = render_month(DayView(day_local=day, month_wins=wins)).poster
    _, cells = calendar_layout(day)

    def rim(n: int) -> set[tuple[int, int, int]]:
        x, y = cells[n]
        sides = [(x + d, y - 1) for d in range(CELL)] + [(x + d, y + CELL) for d in range(CELL)]
        sides += [(x - 1, y + d) for d in range(CELL)] + [(x + CELL, y + d) for d in range(CELL)]
        return {frame.getpixel(at) for at in sides}

    def corners(n: int) -> set[tuple[int, int, int]]:
        x, y = cells[n]
        return {frame.getpixel((x + dx, y + dy)) for dx in (-1, CELL) for dy in (-1, CELL)}

    def inside(n: int) -> list[tuple[int, int, int]]:
        x, y = cells[n]
        return [frame.getpixel((x + dx, y + dy)) for dy in range(CELL) for dx in range(CELL)]

    assert GLOW == WHITE
    for n in (2, 3, 9, 16):
        assert rim(n) == {WHITE}, (n, "a white rim on all four sides")
        assert corners(n) == {(0, 0, 0)}, (n, "corners open, so neighbours stay two cells")
        assert inside(n)[:18] == [GOLD] * 18, "the meaning colours stay: gold over"
    assert inside(2)[18:] == [VIOLET] * 18, "violet"
    assert TEAL in inside(3), "the book's corner stays on a great day"
    for n in (1, 5, 6, 12, 20, 21):
        assert WHITE not in rim(n), (n, "no win, one win, a book, today, a day to come: no rim")
    assert rim(4) - {WHITE} == {(0, 0, 0)} and inside(4) == [VIOLET] * 36, "only its neighbour's"
    assert inside(1) == [QUIET_DAY] * 36


def test_the_glow_is_white_on_every_weekday_ground():
    wins = ((2, ("workout", "sleep")),)
    for offset in range(7):
        day = f"2026-11-{9 + offset:02d}"
        frame = render_month(DayView(day_local=day, month_wins=wins)).poster
        x, y = calendar_layout(day)[1][2]
        assert frame.getpixel((x - 1, y)) == frame.getpixel((x + CELL, y + CELL - 1)) == WHITE
        assert frame.getpixel((x - 1, y - 1)) == day_ground(day) == DAY_GROUNDS[offset]
