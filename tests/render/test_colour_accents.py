"""Gates for the colour pass Max approved on 2026-10-05: an accent per screen header, a shelf of
dim tints waiting to fill, and the summary's three voices (quotation, attribution, own words)."""

from __future__ import annotations

import colorsys
from dataclasses import replace

import pytest

from app.render.font import BODY, text_width
from app.render.frame import Color, Frame
from app.render.gamma import led_gamma, led_lut
from app.render.palette import (
    ATTRIBUTION,
    GOLD,
    HEADERS,
    LABEL,
    SHELF_TINT,
    SPINES,
    TEXT,
    TRACK,
    VIOLET,
    VOICE,
    WOOD,
    shelf_tint,
)
from app.render.rotation import rotation_clips
from app.render.screens import (
    LEFT,
    SUMMARY_LINE_YS,
    line_colors,
    render_books,
    summary_colors,
    wrap_pages,
)
from app.render.view import DayView
from app.timeutil import from_utc_iso
from tests.render import COMBOS, WEEK_41, load, record_text

CORAL = (255, 90, 80)
BLACK = (0, 0, 0)
EPICTETUS = (
    "Yesterday's sleep data has not arrived. "
    '"Of things some are in our power, and others are not." - Epictetus'
)
BANK_LINE = 'Reflection. "Quote words." - Author'


def lit_colours(frame: Frame, box: tuple[int, int, int, int]) -> set[Color]:
    region = frame.crop(box)
    return {pixel for pixel in region.get_flattened_data() if pixel != BLACK}


def first_lit(frame: Frame, box: tuple[int, int, int, int]) -> Color:
    x0, y0, x1, y1 = box
    for y in range(y0, y1):
        for x in range(x0, x1):
            if frame.getpixel((x, y)) != BLACK:
                return frame.getpixel((x, y))
    raise AssertionError(f"nothing lit in {box}")


def luma(color: Color) -> float:
    return 0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2]


def saturation(color: Color) -> float:
    return colorsys.rgb_to_hsv(*(channel / 255 for channel in color))[1]


def on_panel(color: Color) -> Color:
    lut = led_lut()
    return (lut[color[0]], lut[color[1]], lut[color[2]])


# 1. a colour per header


def test_header_accents_are_three_distinct_colours_and_not_the_label_blue():
    assert HEADERS == {"today": VIOLET, "week": CORAL, "books": GOLD}
    assert len(set(HEADERS.values())) == 3
    assert LABEL not in HEADERS.values()
    assert TEXT not in HEADERS.values()


@pytest.mark.parametrize("combo", [*COMBOS, "empty"])
def test_each_header_word_wears_its_accent_and_the_stamp_stays_text(combo, settings):
    if combo == "empty":
        view, now = DayView(day_local="2026-10-02"), from_utc_iso("2026-10-02T16:00:00Z")
    else:
        view, now = load(combo, settings)
    clips = dict(rotation_clips(view, now))
    for screen, accent in HEADERS.items():
        for frame in clips[screen].frames:
            assert frame.getpixel((2, 2)) == accent, (combo, screen)
            assert lit_colours(frame, (0, 2, 32, 7)) == {accent}, (combo, screen)
            assert lit_colours(frame, (34, 2, 64, 7)) == {TEXT}, (combo, screen)


def test_yesterday_header_is_violet_too(settings):
    view, now = load(WEEK_41, settings)
    frame = dict(rotation_clips(replace(view, day_shown="2026-09-29"), now))["today"].poster
    assert lit_colours(frame, (0, 2, 40, 7)) == {VIOLET}
    assert lit_colours(frame, (41, 2, 64, 7)) == {TEXT}


# 2. the shelf: unread slots are dim tints of the spine they will become


def test_every_unread_slot_is_the_tint_of_its_positions_spine(settings):
    view, _ = load(WEEK_41, settings)
    for read in (0, 3, 7, 12, 15, None):
        frame = render_books(replace(view, books_ytd=read)).poster
        for index in range(12):
            spine = SPINES[index % len(SPINES)]
            expected = spine if index < (read or 0) else shelf_tint(spine)
            assert frame.getpixel((2 + index * 5, 40)) == expected, (read, index)
            assert frame.getpixel((2 + index * 5 + 3, 40)) == expected, (read, index)
        assert frame.getpixel((2, 41)) == WOOD
        assert TRACK not in lit_colours(frame, (0, 24, 64, 43))


def test_shelf_tint_is_dark_but_still_coloured_through_the_panel_curve():
    assert SHELF_TINT == 0.18
    night = led_lut(brightness=0.2)
    for spine in SPINES:
        tint, lit = on_panel(shelf_tint(spine)), on_panel(spine)
        assert luma(tint) / luma(lit) < 0.5, (spine, tint, lit)
        assert saturation(tint) > 0.3, (spine, tint)
        assert max(tint) - min(tint) > 30, (spine, tint)
        assert abs(luma(tint) - luma(on_panel(TRACK))) < 25, (spine, tint)
        assert max(night[channel] for channel in shelf_tint(spine)) > 0, spine
    tints = {shelf_tint(spine) for spine in SPINES}
    assert len(tints) == len(SPINES)


def test_the_shelf_tints_survive_the_gamma_emulator_in_a_rendered_frame(settings):
    view, _ = load(WEEK_41, settings)
    shown = led_gamma(render_books(replace(view, books_ytd=0)).poster)
    colours = [shown.getpixel((2 + index * 5, 40)) for index in range(12)]
    assert len(set(colours)) == len(SPINES)
    assert all(saturation(colour) > 0.3 for colour in colours)


# 3. the summary's three voices


def test_summary_colours_split_quote_attribution_and_own_words():
    colours = summary_colors(BANK_LINE)
    start, end = BANK_LINE.index('"'), BANK_LINE.rindex('"')
    dash = BANK_LINE.index("-")
    assert colours[:start] == [VOICE] * start, "his own words around a quote"
    assert colours[start : end + 1] == [TEXT] * (end + 1 - start), "the quotation in white"
    assert colours[end + 1] == TEXT, "the space after the closing mark keeps its colour"
    assert colours[dash:] == [ATTRIBUTION] * (len(BANK_LINE) - dash)
    assert ATTRIBUTION == GOLD


@pytest.mark.parametrize(
    ("text", "gold_slice"),
    [
        ('"Quoted." - Marcus Aurelius. Then more.', "- Marcus Aurelius."),
        ('"Quoted." - Seneca, who knew; and so on.', "- Seneca,"),
        ('"Quoted." - Seneca', "- Seneca"),
        ('"Quoted.", - Heraclitus; rest.', "- Heraclitus;"),
    ],
)
def test_attribution_runs_to_the_end_of_its_clause(text, gold_slice):
    colours = summary_colors(text)
    gold = "".join(ch for ch, colour in zip(text, colours, strict=True) if colour == ATTRIBUTION)
    assert gold.strip() == gold_slice
    after = text.index(gold_slice) + len(gold_slice)
    assert set(colours[after:]) <= {VOICE, ATTRIBUTION}
    assert colours[after + 1 :] == [VOICE] * (len(text) - after - 1), "his words after the name"


def test_a_dash_that_is_not_an_attribution_stays_text():
    text = 'It\'s enough - rest is the work. "Quoted." Plain - ending.'
    colours = summary_colors(text)
    assert ATTRIBUTION not in colours
    assert colours[text.index('"') : text.rindex('"') + 1] == [TEXT] * len('"Quoted."')
    assert colours[text.rindex('"') + 2 :] == [VOICE] * len("Plain - ending.")


@pytest.mark.parametrize(
    "text",
    [
        'He wrote "never mind - Author',
        'Three " marks " here " today',
        "No marks at all - Author",
        "Yesterday's \"cut quote that ran past the limit and lost its clo...",
    ],
)
def test_unbalanced_marks_mean_no_quotation(text):
    assert summary_colors(text) == [TEXT] * len(text)


def test_line_colours_carry_a_quotation_across_lines_and_pages():
    pages = wrap_pages(EPICTETUS)
    lines = [line for page in pages for line in page]
    colours = line_colors(lines)
    assert len(pages) == 5
    assert [len(c) for c in colours] == [len(line) for line in lines]
    assert lines == [
        "Yesterday's",
        "sleep data",
        "has not",
        "arrived.",
        '"Of things',
        "some are in",
        "our power,",
        "and others",
        'are not."',
        "- Epictetus",
    ]
    assert all(set(c) == {VOICE} for c in colours[:4])
    assert all(set(c) == {TEXT} for c in colours[4:9]), lines[4:9]
    assert set(colours[9]) == {ATTRIBUTION}


def glyph_colours(frame: Frame, y: int, line: str) -> list[Color | None]:
    """The colour of each character's glyph on a drawn line, None for a space."""
    out: list[Color | None] = []
    for index, char in enumerate(line):
        if char == " ":
            out.append(None)
            continue
        x = LEFT + (text_width(line[:index], BODY) + 1 if index else 0)
        width = text_width(char, BODY)
        out.append(first_lit(frame, (x, y, x + width, y + BODY.height)))
    return out


def test_the_bank_line_paints_three_colours_in_place_on_every_page(settings):
    view, _ = load(WEEK_41, settings)
    clip = render_books(replace(view, summary_line=BANK_LINE))
    pages = wrap_pages(BANK_LINE)
    lines = [line for page in pages for line in page]
    assert len(pages) >= 2 and " ".join(lines) == BANK_LINE
    expected = iter(line_colors(lines))
    seen: set[Color] = set()
    for frame, page in zip(clip.frames, pages, strict=True):
        for line, y in zip(page, SUMMARY_LINE_YS, strict=False):
            colours = next(expected)
            drawn = glyph_colours(frame, y, line)
            for char, want, got in zip(line, colours, drawn, strict=True):
                if char != " ":
                    assert got == want, (line, char)
            seen |= {colour for colour in drawn if colour}
    assert seen == {TEXT, VOICE, ATTRIBUTION}
    for frame in clip.frames:
        assert lit_colours(frame, (0, 45, 64, 62)) <= {TEXT, VOICE, ATTRIBUTION}


def test_the_epictetus_line_is_cream_then_white_then_gold(settings):
    view, _ = load(WEEK_41, settings)
    frames = render_books(replace(view, summary_line=EPICTETUS)).frames
    assert len(frames) == 5
    summary = (0, 45, 64, 62)
    assert lit_colours(frames[0], summary) == {VOICE}, "his own clause in the third colour"
    assert lit_colours(frames[1], summary) == {VOICE}
    assert lit_colours(frames[2], summary) == {TEXT}, "the quotation in white"
    assert lit_colours(frames[3], summary) == {TEXT}
    assert lit_colours(frames[4], summary) == {TEXT, ATTRIBUTION}
    assert lit_colours(frames[4], (0, 45, 64, 52)) == {TEXT}
    assert lit_colours(frames[4], (0, 54, 64, 62)) == {ATTRIBUTION}


def test_colouring_moves_no_pixel_a_line_without_marks_is_identical(monkeypatch, settings):
    import app.render.screens as screens

    view, _ = load(WEEK_41, settings)
    plain = "Second dot this week, 7.4 h of sleep. What you repeat is what you become."
    coloured = render_books(replace(view, summary_line=plain))
    quoted = render_books(replace(view, summary_line=EPICTETUS))
    monkeypatch.setattr(screens, "summary_colors", lambda text: [TEXT] * len(text))
    before_plain = render_books(replace(view, summary_line=plain))
    before_quoted = render_books(replace(view, summary_line=EPICTETUS))
    assert [f.tobytes() for f in coloured.frames] == [f.tobytes() for f in before_plain.frames]
    assert coloured.durations_ms == before_plain.durations_ms
    masks = [f.convert("1").tobytes() for f in quoted.frames]
    assert masks == [f.convert("1").tobytes() for f in before_quoted.frames]
    assert [f.tobytes() for f in quoted.frames] != [f.tobytes() for f in before_quoted.frames]


def test_a_line_without_marks_is_still_one_draw_call_per_line(monkeypatch, settings):
    view, _ = load(WEEK_41, settings)
    drawn = record_text(monkeypatch)
    render_books(view)
    body = [item for item in drawn if item[3] == BODY.name and item[2] in SUMMARY_LINE_YS]
    assert [item[1] for item in body] == [LEFT] * len(body)
    lines = [line for page in wrap_pages(view.summary_line) for line in page]
    assert [item[0] for item in body] == lines


def test_quote_colour_is_told_from_text_and_gold_through_the_panel_curve():
    quote, text, gold = on_panel(VOICE), on_panel(TEXT), on_panel(ATTRIBUTION)
    assert quote == (255, 234, 181)
    assert text[2] - quote[2] >= 60, "warmer than TEXT by a visible margin"
    assert quote[2] - gold[2] >= 60, "paler than GOLD by a visible margin"
    assert min(quote) >= 180, "still a light colour: legibility, not dimness, carries the text"
