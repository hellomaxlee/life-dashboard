"""What each screen says, asserted as text against hand-typed expectations.

Snapshots prove pixels did not change; they cannot say the pixels were right, and
UPDATE_SNAPSHOTS=1 would bless a wrong digit. These tests record every string handed to the
text drawer, with its position, font and scale, and compare it with values typed by hand from
the fixture files. The digit shapes of both fonts are typed out here too.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from PIL import Image

from app.render.font import BODY, SMALL, draw_text
from app.render.rotation import render_screen
from app.render.screens import render_books, render_today, render_week
from tests.render import WEEK_41, load, record_text

RACE_WEEK = "race-week__all-sources__broken-last-week__base"
DELAYED = "rest__health-delayed__alive__peak"
S, B = "small-3x5", "body-5x7"
CORAL, GOLD, TEAL = (255, 90, 80), (255, 200, 40), (40, 220, 200)
TEXT, RING = (235, 235, 245), (72, 76, 104)
DOT_CENTRES = ((11, 17), (32, 17), (53, 17))


def page_lines(drawn: list[tuple]) -> list[list[str]]:
    """The summary lines of each page, in order: a new page starts at each y=45 line."""
    pages: list[list[str]] = []
    for text, _x, y, font, _scale in drawn:
        if font != B or y not in (45, 54):
            continue
        if y == 45:
            pages.append([])
        pages[-1].append(text)
    return pages


def test_week_screen_text_race_week(monkeypatch, settings):
    view, now = load(RACE_WEEK, settings)
    drawn = record_text(monkeypatch)
    clip = render_screen("week", view, now)
    assert drawn == [
        ("WEEK", 2, 2, S, 1),
        ("2 OF 3", 41, 2, S, 1),
        ("CLAUDE", 2, 33, S, 1),
        ("87%", 28, 33, S, 1),
        ("RESETS IN 2D", 2, 40, S, 1),
        ("0", 10, 50, B, 1),
        ("WK STREAK", 18, 52, S, 1),
    ]
    centres = [clip.poster.getpixel(xy) for xy in DOT_CENTRES]
    assert centres == [CORAL, GOLD, (0, 0, 0)]


def test_week_screen_text_week_41(monkeypatch, settings):
    view, now = load(WEEK_41, settings)
    drawn = record_text(monkeypatch)
    render_screen("week", view, now)
    assert [item[0] for item in drawn] == [
        "WEEK",
        "2 OF 3",
        "CLAUDE",
        "41%",
        "RESETS IN 3D",
        "4",
        "WK STREAK",
    ]


def test_stale_week_text_alternates_reset_and_age(monkeypatch, settings):
    view, now = load(DELAYED, settings)
    drawn = record_text(monkeypatch)
    clip = render_screen("week", view, now)
    assert len(clip.frames) == 16
    per_frame = [drawn[i : i + 7] for i in range(0, len(drawn), 7)]
    assert len(per_frame) == 16
    for index, frame_text in enumerate(per_frame):
        line_2 = "RESETS IN 14H" if index < 8 else "SEEN 2D AGO"
        assert frame_text == [
            ("WEEK", 2, 2, S, 1),
            ("3 OF 3", 41, 2, S, 1),
            ("CLAUDE", 2, 33, S, 1),
            ("24%", 28, 33, S, 1),
            (line_2, 2, 40, S, 1),
            ("76", 7, 50, B, 1),
            ("WK STREAK", 21, 52, S, 1),
        ], index
    assert [clip.poster.getpixel(xy) for xy in DOT_CENTRES] == [CORAL, GOLD, TEAL]


def test_today_screen_text_race_week(monkeypatch, settings):
    view, now = load(RACE_WEEK, settings)
    drawn = record_text(monkeypatch)
    render_screen("today", view, now)
    assert drawn == [
        ("TODAY", 2, 2, S, 1),
        ("SUN 8", 43, 2, S, 1),
        ("9.8", 16, 9, B, 2),
        ("h", 47, 16, B, 1),
        ("SLEEP", 2, 32, S, 1),
        ("GOAL 7H", 36, 32, S, 1),
        ("NO DOT YET", 13, 42, S, 1),
        ("STEPS", 2, 51, S, 1),
        ("20634", 43, 51, S, 1),
        ("AS OF 01:30", 2, 58, S, 1),
    ]


def test_today_screen_text_when_health_is_delayed(monkeypatch, settings):
    view, now = load(DELAYED, settings)
    drawn = record_text(monkeypatch)
    render_screen("today", view, now)
    assert drawn == [
        ("TODAY", 2, 2, S, 1),
        ("WED 5", 42, 2, S, 1),
        ("--", 16, 9, B, 2),
        ("SLEEP", 2, 32, S, 1),
        ("NO DATA", 35, 32, S, 1),
        ("DOT NO DATA", 13, 42, S, 1),
        ("STEPS", 2, 51, S, 1),
        ("NO DATA", 35, 51, S, 1),
        ("AS OF MON 18:00", 2, 58, S, 1),
    ]


@pytest.mark.parametrize(
    ("dot", "label", "centre"),
    [
        (True, "WORKOUT DONE", GOLD),
        (False, "NO DOT YET", (0, 0, 0)),
        (None, "DOT NO DATA", (0, 0, 0)),
    ],
)
def test_today_dot_label_for_each_branch(monkeypatch, settings, dot, label, centre):
    view, _ = load(WEEK_41, settings)
    drawn = record_text(monkeypatch)
    frame = render_today(replace(view, today_dot=dot)).poster
    assert [item for item in drawn if item[2] == 42] == [(label, 13, 42, S, 1)]
    assert frame.getpixel((6, 44)) == centre
    assert [item[0] for item in drawn if item[2] in (9, 51, 58)] == [
        "7.4",
        "STEPS",
        "8412",
        "AS OF 18:10",
    ]


def test_books_screen_text_and_pages_race_week(monkeypatch, settings):
    view, now = load(RACE_WEEK, settings)
    drawn = record_text(monkeypatch)
    clip = render_screen("books", view, now)
    assert drawn[:4] == [
        ("BOOKS", 2, 2, S, 1),
        ("2026", 47, 2, S, 1),
        ("10", 2, 9, B, 2),
        ("of 12", 28, 16, B, 1),
    ]
    assert page_lines(drawn) == [
        ["Finished", "Jazz"],
        ["(10 of 12);", "9.8 h of"],
        ["sleep. It's", "enough -"],
        ["rest is 57%", "of the"],
        ["work."],
    ]
    assert len(clip.frames) == 5 and set(clip.durations_ms) == {2000}
    assert all(item[1] == 2 for item in drawn if item[2] in (45, 54))


def test_books_screen_text_and_pages_week_41(monkeypatch, settings):
    view, now = load(WEEK_41, settings)
    drawn = record_text(monkeypatch)
    render_screen("books", view, now)
    assert drawn[:4] == [
        ("BOOKS", 2, 2, S, 1),
        ("2026", 47, 2, S, 1),
        ("3", 2, 9, B, 2),
        ("of 12", 16, 16, B, 1),
    ]
    assert page_lines(drawn) == [
        ["Second dot", "this week,"],
        ["7.4 h of", "sleep. What"],
        ["you repeat", "is what you"],
        ["become."],
    ]


def test_page_pips_mark_the_current_page(settings):
    view, _ = load(RACE_WEEK, settings)
    clip = render_books(view)
    # five pages: pips two pixels wide at x = 25, 28, 31, 34, 37 on the bottom row
    for page, frame in enumerate(clip.frames):
        colours = [frame.getpixel((25 + 3 * pip, 63)) for pip in range(5)]
        assert colours == [TEXT if pip == page else RING for pip in range(5)], page
        lit = [x for x in range(64) if frame.getpixel((x, 63)) != (0, 0, 0)]
        assert lit == [25, 26, 28, 29, 31, 32, 34, 35, 37, 38]
    single = render_books(replace(view, summary_line="Rest day.")).poster
    assert all(single.getpixel((x, 63)) == (0, 0, 0) for x in range(64))


def test_week_dot_count_is_drawn_dot_for_dot(settings):
    view, now = load(WEEK_41, settings)
    expected = {
        0: [(0, 0, 0)] * 3,
        1: [CORAL, (0, 0, 0), (0, 0, 0)],
        2: [CORAL, GOLD, (0, 0, 0)],
        3: [CORAL, GOLD, TEAL],
        4: [CORAL, GOLD, TEAL],
    }
    for dots, centres in expected.items():
        frame = render_week(replace(view, week_dots=dots), now).poster
        assert [frame.getpixel(xy) for xy in DOT_CENTRES] == centres, dots


BODY_DIGITS = {
    "0": (".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."),
    "1": ("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "2": (".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"),
    "3": ("#####", "...#.", "..#..", "...#.", "....#", "#...#", ".###."),
    "4": ("...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."),
    "5": ("#####", "#....", "####.", "....#", "....#", "#...#", ".###."),
    "6": ("..##.", ".#...", "#....", "####.", "#...#", "#...#", ".###."),
    "7": ("#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."),
    "8": (".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."),
    "9": (".###.", "#...#", "#...#", ".####", "....#", "...#.", ".##.."),
}
SMALL_DIGITS = {
    "0": ("###", "#.#", "#.#", "#.#", "###"),
    "1": (".#.", "##.", ".#.", ".#.", "###"),
    "2": ("###", "..#", "###", "#..", "###"),
    "3": ("###", "..#", ".##", "..#", "###"),
    "4": ("#.#", "#.#", "###", "..#", "..#"),
    "5": ("###", "#..", "###", "..#", "###"),
    "6": ("###", "#..", "###", "#.#", "###"),
    "7": ("###", "..#", "..#", ".#.", ".#."),
    "8": ("###", "#.#", "###", "#.#", "###"),
    "9": ("###", "#.#", "###", "..#", "###"),
}


def drawn_rows(char: str, font, scale: int = 1) -> tuple[str, ...]:
    width, height = len(font.glyphs[char][0]) * scale, font.height * scale
    canvas = Image.new("RGB", (width + 4, height + 4))
    draw_text(canvas, 2, 2, char, (255, 255, 255), font, scale)
    assert canvas.getbbox() is not None
    return tuple(
        "".join("#" if canvas.getpixel((2 + x, 2 + y)) != (0, 0, 0) else "." for x in range(width))
        for y in range(height)
    )


@pytest.mark.parametrize("digit", list("0123456789"))
def test_digit_shapes_in_both_fonts(digit):
    assert drawn_rows(digit, BODY) == BODY_DIGITS[digit]
    assert drawn_rows(digit, SMALL) == SMALL_DIGITS[digit]
    doubled = tuple(
        "".join(cell * 2 for cell in row) for row in BODY_DIGITS[digit] for _ in range(2)
    )
    assert drawn_rows(digit, BODY, scale=2) == doubled


def test_all_ten_digits_are_distinct_in_each_font():
    assert len(set(BODY_DIGITS.values())) == 10
    assert len(set(SMALL_DIGITS.values())) == 10
