"""The plant on the top Books shelf grows with the weeks-hit streak, and after ten weeks
overgrows the shelf by rule, not by drawing (Max, 2026-11-20)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.render.palette import CORAL, GREEN
from app.render.screens import (
    DECOR_X,
    LAMP_KEEP_OUT,
    LEAF_DARK,
    OVERGROWTH,
    PLANT_MAX_STREAK,
    PLANT_POT,
    SHELF_RIGHT,
    TIER_BOARDS,
    overgrowth,
    plant_rows,
    render_books,
)
from app.render.view import DayView

VIEW = DayView(day_local="2026-11-17", books_ytd=9)
AS_FIRST_DRAWN = (
    "..g.g..",
    ".g.g.g.",
    "..ggg..",
    "...g...",
    ".ppppp.",
    ".ppppp.",
    "..ppp..",
)
HEADER_BOTTOM = 8


def green(rows: tuple[str, ...]) -> int:
    return sum(row.count("g") for row in rows)


def test_one_row_of_green_for_every_week_up_to_the_cap():
    for weeks in range(PLANT_MAX_STREAK + 1):
        rows = plant_rows(weeks)
        assert rows[-len(PLANT_POT) :] == PLANT_POT
        assert len(rows) - len(PLANT_POT) == weeks + 1, weeks
        assert all("g" in row for row in rows[: -len(PLANT_POT)]), weeks
    assert plant_rows(0) == ("...g...", *PLANT_POT)
    assert plant_rows(PLANT_MAX_STREAK + 1) == plant_rows(76) == plant_rows(PLANT_MAX_STREAK)


def test_every_week_adds_green_and_the_stem_stays_whole():
    counts = [green(plant_rows(weeks)) for weeks in range(PLANT_MAX_STREAK + 1)]
    assert counts == sorted(set(counts))
    for weeks in range(PLANT_MAX_STREAK + 1):
        for row in plant_rows(weeks)[1 : -len(PLANT_POT)]:
            assert row[3] == "g", (weeks, row)


def test_three_weeks_is_the_plant_as_first_drawn_and_so_is_no_streak_on_record():
    assert plant_rows(3) == AS_FIRST_DRAWN
    assert plant_rows(None) == AS_FIRST_DRAWN
    assert plant_rows(-2) == plant_rows(0)


def test_leaves_alternate_sides_up_the_stem():
    tall = plant_rows(PLANT_MAX_STREAK)
    leaves = [row for row in tall[3 : -len(PLANT_POT)] if row != "...g..."]
    assert leaves == ["...ggg.", ".ggg...", "...ggg.", ".ggg..."]


def decor_pixels(streak: int | None) -> dict[tuple[int, int], tuple[int, int, int]]:
    frame = render_books(replace(VIEW, streak_weeks=streak)).poster
    return {
        (x, y): frame.getpixel((x, y))
        for x in range(DECOR_X, DECOR_X + 7)
        for y in range(TIER_BOARDS[0])
    }


@pytest.mark.parametrize("weeks", [0, 1, 3, 7, 10, 76])
def test_the_drawn_plant_stands_on_the_shelf_and_clear_of_the_header(weeks):
    pixels = decor_pixels(weeks)
    rows = plant_rows(weeks)
    top = TIER_BOARDS[0] - len(rows)
    assert top > HEADER_BOTTOM + 1
    for dy, row in enumerate(rows):
        for dx, cell in enumerate(row):
            if cell in "gp":
                assert pixels[(DECOR_X + dx, top + dy)] == (GREEN if cell == "g" else CORAL)
    drawn = {y for (x, y), colour in pixels.items() if colour in (GREEN, CORAL)}
    assert min(drawn) == top and max(drawn) == TIER_BOARDS[0] - 1


def test_the_streak_changes_the_plant_and_nothing_else_on_the_page():
    short = render_books(replace(VIEW, streak_weeks=0)).poster
    tall = render_books(replace(VIEW, streak_weeks=10)).poster
    changed = {
        (x, y)
        for x in range(64)
        for y in range(64)
        if short.getpixel((x, y)) != tall.getpixel((x, y))
    }
    assert changed
    assert all(
        DECOR_X <= x < DECOR_X + 7 and HEADER_BOTTOM + 1 < y < TIER_BOARDS[0] for x, y in changed
    )
    assert render_books(replace(VIEW, streak_weeks=None)).poster.tobytes() == (
        render_books(replace(VIEW, streak_weeks=3)).poster.tobytes()
    )


def test_nothing_overgrows_through_ten_weeks_or_without_a_streak():
    for weeks in (None, 0, 3, PLANT_MAX_STREAK):
        assert overgrowth(weeks) == ()
    assert len(overgrowth(PLANT_MAX_STREAK + 1)) == 2


def test_every_further_week_adds_green_and_never_takes_any_away():
    full_at = next(w for w in range(11, 400) if len(overgrowth(w)) == len(OVERGROWTH))
    assert 104 < full_at < 140, "two years is overgrown and still growing"
    for weeks in range(PLANT_MAX_STREAK, full_at):
        now, later = overgrowth(weeks), overgrowth(weeks + 1)
        assert later[: len(now)] == now and len(later) > len(now), weeks
    assert overgrowth(full_at + 500) == OVERGROWTH
    assert len(overgrowth(104)) > 130
    assert len(set(OVERGROWTH)) == len(OVERGROWTH)


def test_overgrowth_keeps_off_the_header_the_books_the_lamp_and_the_summary():
    x0, y0, x1, y1 = LAMP_KEEP_OUT
    for x, y in OVERGROWTH:
        assert 0 <= x < 64 and HEADER_BOTTOM + 1 < y < TIER_BOARDS[1], (x, y)
        assert not (x0 <= x <= x1 and y0 <= y <= y1), (x, y)
        if y0 <= y <= y1:
            assert x > x1, "at the lamp's height, only the far edge grows"
        if x <= SHELF_RIGHT:
            assert TIER_BOARDS[0] <= y <= TIER_BOARDS[0] + 2, (x, y)


@pytest.mark.parametrize("weeks", [11, 30, 52, 104, 500])
def test_the_drawn_overgrowth_is_exactly_the_rule_and_the_lamp_is_untouched(weeks):
    upright = render_books(replace(VIEW, streak_weeks=PLANT_MAX_STREAK)).poster
    grown = render_books(replace(VIEW, streak_weeks=weeks)).poster
    changed = {
        (x, y)
        for x in range(64)
        for y in range(64)
        if upright.getpixel((x, y)) != grown.getpixel((x, y))
    }
    assert changed == set(overgrowth(weeks))
    assert {grown.getpixel(cell) for cell in changed} <= {GREEN, LEAF_DARK}
