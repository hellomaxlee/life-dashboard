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


# every scene: the scenery is whatever a render without it lacks, and none of it may sit
# within a pixel of anything else on the frame


def scenes(view: DayView, now) -> dict[str, list[Frame]]:
    from app.render.city import render_city
    from app.render.screens import render_books, render_parked, render_today

    return {
        "today": list(render_today(view, now).frames),
        "parked": list(render_parked(view, now).frames),
        "week": list(render_week(view, now).frames),
        "books": list(render_books(view).frames),
        "city": list(render_city(view, now).frames),
    }


def bare_scenes(view: DayView, now, monkeypatch) -> dict[str, list[Frame]]:
    from app.render import city, screens

    with monkeypatch.context() as patch:
        for module in (screens, city):
            patch.setattr(module, "draw_field", lambda *args, **kwargs: None)
            patch.setattr(module, "scatter_stars", lambda *args, **kwargs: None)
        return scenes(view, now)


def scenery_touching(frame: Frame, bare: Frame) -> list[tuple[int, int]]:
    lit = {(x, y) for y in range(SIZE) for x in range(SIZE) if bare.getpixel((x, y)) != (0, 0, 0)}
    found = []
    for y in range(SIZE):
        for x in range(SIZE):
            if (x, y) in lit or frame.getpixel((x, y)) == bare.getpixel((x, y)):
                continue
            if any((x + dx, y + dy) in lit for dy in (-1, 0, 1) for dx in (-1, 0, 1)):
                found.append((x, y))
    return found


@pytest.mark.parametrize("combo", COMBOS)
def test_no_scenery_touches_anything_on_any_screen(combo, settings, monkeypatch):
    view, now = load(combo, settings)
    seen = 0
    for offset in range(7):
        day = replace(view, day_local=f"2026-11-{9 + offset:02d}")
        full, bare = scenes(day, now), bare_scenes(day, now, monkeypatch)
        for screen, frames in full.items():
            assert len(frames) == len(bare[screen]), screen
            for frame, plain in zip(frames, bare[screen], strict=True):
                for x in range(SIZE):
                    for y in range(SIZE):
                        if plain.getpixel((x, y)) != (0, 0, 0):
                            assert frame.getpixel((x, y)) == plain.getpixel((x, y)), (
                                screen,
                                "scenery overwrote a drawn pixel",
                            )
                stray = scenery_touching(frame, plain)
                assert not stray, (combo, day.day_local, screen, stray[:5])
                seen += frame.tobytes() != plain.tobytes()
    assert seen >= 7 * 4, "the scenes are there: today, parked, week and books every day"


def test_the_weather_sky_follows_the_condition_and_the_hour():
    from app.render.city import SKY_ROWS, SKY_TONES, _weather_frame
    from app.render.palette import dim
    from tests.render.test_city import status, view_of, weather

    noon, late = from_utc_iso("2026-10-02T16:00:00Z"), from_utc_iso("2026-10-03T02:00:00Z")

    def sky(code: int, now) -> set:
        w = weather(code=code, fetched_at_utc=now.strftime("%Y-%m-%dT%H:%M:%SZ"))
        city = status(weather=w)
        frame = _weather_frame(view_of(city), city, now)
        rows = range(SKY_ROWS[0], SKY_ROWS[1] + 1)
        return {frame.getpixel((x, y)) for y in rows for x in (0, 63)}

    assert sky(0, noon) == {SKY_TONES["clear"]}
    assert sky(3, noon) == {SKY_TONES["overcast"]}
    assert sky(61, noon) == {SKY_TONES["rain"]}
    assert sky(3, late) == {dim(SKY_TONES["overcast"], 0.6)}, "dimmer at night"
    assert sky(0, late) == {(0, 0, 0)}, "a clear night is open sky: black, with stars"
    assert len(set(SKY_TONES.values()) - {None}) == 8, "eight skies, each its own tone"
