"""Nothing bleeds (Max, 2026-11-15: "the white text doesn't touch the white cloud").

On a scene screen no lit pixel that is not text may sit within one pixel of a letter, in any
direction, and the scenery itself keeps to its own ground: stars never enter an orbit and
never land beside a planet. Checked on the frame as drawn, over every fixture, every weekday
and the counts a week can show.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.render import font
from app.render.frame import SIZE, Frame, new_frame
from app.render.screens import DOT_ROW_Y, SKY_BOTTOM, SKY_STARS, SKY_TOP, dot_layout, render_week
from app.render.view import DayView
from app.timeutil import from_utc_iso
from tests.render import COMBOS, load, record_text

FONTS = {face.name: face for face in (font.BODY, font.SMALL)}
NOW = from_utc_iso("2026-11-15T18:00:00Z")
DRAW_TEXT = font.draw_text  # the real one, taken before record_text patches the module


def glyph_pixels(drawn: list[tuple[str, int, int, str, int]]) -> set[tuple[int, int]]:
    """Every pixel the recorded text lights, redrawn alone on an empty frame."""
    alone = new_frame()
    for text, x, y, face, scale in list(drawn):
        DRAW_TEXT(alone, x, y, text, (255, 255, 255), FONTS[face], scale)
    return {(x, y) for y in range(SIZE) for x in range(SIZE) if alone.getpixel((x, y)) != (0, 0, 0)}


def touching(frame: Frame, letters: set[tuple[int, int]], rows: range) -> list[tuple[int, int]]:
    """Lit pixels in `rows` that are not text and sit within one pixel of a letter."""
    found = []
    for y in rows:
        for x in range(SIZE):
            if (x, y) in letters or frame.getpixel((x, y)) == (0, 0, 0):
                continue
            if any((x + dx, y + dy) in letters for dy in (-1, 0, 1) for dx in (-1, 0, 1)):
                found.append((x, y))
    return found


def week_cases(settings) -> list[DayView]:
    cases = [load(combo, settings)[0] for combo in COMBOS]
    base = cases[0]
    for offset in range(7):
        day = f"2026-11-{9 + offset:02d}"
        for dots, streak in ((0, 0), (1, 4), (2, 12), (3, 123), (None, None)):
            cases.append(replace(base, day_local=day, week_dots=dots, streak_weeks=streak))
    return cases


def test_nothing_in_the_week_sky_touches_a_letter(settings, monkeypatch):
    drawn = record_text(monkeypatch)
    for view in week_cases(settings):
        drawn.clear()
        frame = render_week(view, NOW).poster
        stray = touching(frame, glyph_pixels(drawn), range(0, SKY_BOTTOM + 1))
        assert not stray, (view.day_local, view.week_dots, stray[:5])


def test_stars_stay_out_of_every_orbit_and_off_every_planet(settings, monkeypatch):
    """The stars are the pixels a starless render lacks; each has its 3x3 block to itself and
    none is inside an orbit or within a pixel of one."""
    from app.render import screens

    for view in week_cases(settings):
        centres, radius = dot_layout(view.week_target)
        frame = render_week(view, NOW).poster
        with monkeypatch.context() as patch:
            patch.setattr(screens, "scatter_stars", lambda *args, **kwargs: None)
            bare = render_week(view, NOW).poster
        stars = [
            (x, y)
            for y in range(SIZE)
            for x in range(SIZE)
            if frame.getpixel((x, y)) != bare.getpixel((x, y))
        ]
        assert 0 < len(stars) <= SKY_STARS, (view.day_local, len(stars))
        for x, y in stars:
            assert SKY_TOP < y < SKY_BOTTOM, (view.day_local, (x, y))
            block = [
                frame.getpixel((x + dx, y + dy))
                for dy in (-1, 0, 1)
                for dx in (-1, 0, 1)
                if dx or dy
            ]
            assert set(block) == {(0, 0, 0)}, (view.day_local, view.week_dots, (x, y))
            for cx in centres:
                inside = (x - cx) ** 2 + (y - DOT_ROW_Y) ** 2 <= (radius + 1) ** 2
                assert not inside, (view.day_local, view.week_dots, (x, y))


def lit_sky(frame: Frame) -> set[tuple[int, int]]:
    return {
        (x, y)
        for y in range(SKY_TOP, SKY_BOTTOM + 1)
        for x in range(SIZE)
        if frame.getpixel((x, y)) != (0, 0, 0)
    }


def test_each_day_has_its_own_sky_and_a_day_keeps_it(settings):
    """Compared by where the sky is lit, not by colour, so the weekday accent alone cannot
    pass for a new sky."""
    view = load(COMBOS[0], settings)[0]
    monday = render_week(replace(view, day_local="2026-11-09"), NOW).poster
    again = render_week(replace(view, day_local="2026-11-09"), NOW).poster
    tuesday = render_week(replace(view, day_local="2026-11-10"), NOW).poster
    assert monday.tobytes() == again.tobytes()
    assert lit_sky(monday) != lit_sky(tuesday)
    assert len(lit_sky(monday)) > SKY_STARS, "a sky with planets, words and stars in it"


@pytest.mark.parametrize("target", [1, 2, 3, 4, 5, 7])
def test_any_week_target_draws_inside_the_frame(target, settings):
    view = replace(load(COMBOS[0], settings)[0], week_target=target, week_dots=target)
    assert render_week(view, NOW).poster.size == (SIZE, SIZE)
