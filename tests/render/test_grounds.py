"""One ground per page, and every word readable on it: the gates for the pages as the panel
shows them.

Max and Mei-Lin, 2026-11-15: a page that is part coloured and part black looks unfinished,
and a black outline cut round every word looks busy. So every rotation page is laid on one
ground from edge to edge (Weather on its sky and the floor under it), words sit directly on
it, and black is ink only. Most other render tests run on black (the `no_scenery` fixture)
because they are about where a word sits; the tests here render the real pages, over every
fixture and every weekday, and hold what only the real pages have: the grounds, the contrast
that makes them legible without outlines, and the colours that carry meaning.

Two audits on 2026-11-15 shaped this file. The first version re-dated a view without
re-dating its city status, so every city page it rendered was the "NO DATA" still, and it
skipped every paged clip; both are fixed here (`on_day`, and contrast checked as each word is
drawn, on whatever page it is drawn on).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

import pytest

from app.city.model import LineStatus
from app.render import city as city_screen
from app.render import font, month, screens, usage
from app.render.city import (
    BOARD_X,
    CONDITIONS,
    FLOOR_SHADE,
    FLOOR_TOP,
    HIGH_LOW_Y,
    ICONS,
    MINI_ICONS,
    NIGHT_SKY,
    NOTICE_Y,
    OPEN_NIGHT,
    ROWS_TOP,
    SKY_TONES,
    condition,
    render_city,
    weather_grounds,
)
from app.render.frame import SIZE, Frame, new_frame
from app.render.month import FACT_YS, render_month
from app.render.palette import (
    AMBER,
    ATTRIBUTION,
    BLACK,
    CONTRAST_FLOOR,
    DAY_GROUNDS,
    GOLD,
    GREEN,
    LABEL,
    RED,
    SECONDARY,
    SKY,
    SLOT_SHADE,
    TEXT,
    TRACK,
    VOICE,
    YELLOW,
    contrast,
    day_ground,
    dim,
    legible_on,
    luminance,
)
from app.render.screens import (
    DOT_ROW_Y,
    HILL_BACK,
    HILL_CREST,
    HILL_FRONT,
    MARK_FLOOR,
    band,
    dot_layout,
    render_books,
    render_parked,
    render_today,
    render_week,
    slot,
)
from app.render.theme import theme_of
from app.render.view import DayView
from tests.render import COMBOS, load
from tests.render.test_city import NOW as CITY_NOW
from tests.render.test_city import lines as city_lines
from tests.render.test_city import status, view_of, weather

DRAW_TEXT = font.draw_text  # the real one, taken before any test patches the module
WEEK = [f"2026-11-{9 + offset:02d}" for offset in range(7)]  # Monday 9 to Sunday 15
EXACT_FLOOR = 2.0  # a status or alert colour drawn as itself, on a band made to carry it
WORD_FLOOR = 2.5


def on_day(view: DayView, day: str) -> DayView:
    """The view moved to `day`, its city status with it: `city_for` drops a status dated
    another day, and a test that forgets this renders only "CITY NO DATA"."""
    city = replace(view.city, day_local=day) if view.city is not None else None
    return replace(view, day_local=day, city=city)


def pages(view: DayView, now) -> dict[str, list[Frame]]:
    return {
        "today": list(render_today(view, now).frames),
        "parked": list(render_parked(view, now).frames),
        "week": list(render_week(view, now).frames),
        "books": list(render_books(view).frames),
        "city": list(render_city(view, now).frames),
        "month": list(render_month(view, now).frames),
    }


class Contrast:
    """Wraps `font.draw_text` in every renderer module: as each word is drawn, each of its
    pixels is compared with every neighbouring pixel that is not text, on the frame it was
    drawn on. Works for stills and for every page of a paged clip alike."""

    def __init__(self, monkeypatch) -> None:
        self.worst: dict[bool, float] = {False: 99.0, True: 99.0}
        self.where: dict[bool, tuple] = {}
        self.words = 0
        self.text: dict[int, set[tuple[int, int]]] = {}
        for module in (font, screens, usage, city_screen, month):
            monkeypatch.setattr(module, "draw_text", self.draw)

    def draw(self, frame, x, y, text, color, font_=font.SMALL, scale=1, exact=False):
        after = DRAW_TEXT(frame, x, y, text, color, font_, scale, exact)
        alone = new_frame()
        DRAW_TEXT(alone, x, y, text, (255, 255, 255), font_, scale, True)
        lit = {(px, py) for py in range(SIZE) for px in range(SIZE) if alone.getpixel((px, py))[0]}
        known = self.text.setdefault(id(frame), set())
        known |= lit
        self.words += 1
        for px, py in lit:
            ink = frame.getpixel((px, py))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    at = (px + dx, py + dy)
                    if at in known or not (0 <= at[0] < SIZE and 0 <= at[1] < SIZE):
                        continue
                    if exact and dx and dy:
                        continue  # a letter in a bullet meets the page only corner to corner
                    ratio = contrast(ink, frame.getpixel(at))
                    if ratio < self.worst[exact]:
                        self.worst[exact] = ratio
                        self.where[exact] = (text, at, ink, frame.getpixel(at))
        return after


# the grounds themselves


def test_the_seven_grounds_are_distinct_deep_and_carry_every_word_colour():
    assert len(set(DAY_GROUNDS)) == 7
    assert [day_ground(day) for day in WEEK] == list(DAY_GROUNDS)
    for ground in DAY_GROUNDS:
        assert ground != BLACK and min(ground) <= 24, "a colour: its weakest channel near zero"
        assert luminance(ground) <= 0.135, ground
        for ink in (TEXT, LABEL, SECONDARY, GOLD, GREEN, YELLOW, SKY, VOICE, ATTRIBUTION):
            assert contrast(ink, ground) >= CONTRAST_FLOOR, (ink, ground)
            assert legible_on(ink, ground) == ink, "meaning colours are drawn as themselves"
    apart = [
        sum(abs(a - b) for a, b in zip(DAY_GROUNDS[i], DAY_GROUNDS[i + 1], strict=True))
        for i in range(6)
    ]
    assert min(apart) >= 30, "neighbouring weekdays are plainly different pages"


def test_a_word_colour_that_would_sink_into_its_ground_is_lightened_not_lost():
    assert legible_on(LABEL, BLACK) == LABEL
    red = legible_on(RED, DAY_GROUNDS[2])
    assert red != RED and red[0] == 255 and red[1] == red[2] < 200, "a lighter red"
    assert contrast(red, DAY_GROUNDS[2]) >= CONTRAST_FLOOR
    assert legible_on((0, 60, 150), (0, 60, 150)) != (0, 60, 150)
    assert legible_on((255, 255, 255), (232, 82, 0)) == (255, 255, 255), "white on MTA orange"
    assert contrast((255, 255, 255), BLACK) == pytest.approx(21.0)


def test_a_structural_tone_is_a_recess_of_its_ground_and_still_carries_the_month_theme():
    assert slot(BLACK, TRACK) == TRACK and band(BLACK) == BLACK
    themed = theme_of(((0xE8, 0x5C, 0xFF),)).track
    assert themed != TRACK
    for ground in DAY_GROUNDS:
        recess = slot(ground, TRACK)
        assert recess != BLACK and luminance(recess) < luminance(ground), "a recess, not a hole"
        assert slot(ground, themed) != recess, "the month's tint of the track shows through"
        assert max(abs(a - b) for a, b in zip(recess, dim(ground, SLOT_SHADE), strict=True)) < 20
        board = band(ground)
        assert BLACK != board and luminance(board) < luminance(ground), "a band is a recess too"
        assert board == dim(ground, screens.BAND_SHADE)
        for ink in (RED, AMBER, GREEN):
            assert contrast(ink, board) >= EXACT_FLOOR, (ink, ground)


# the pages as the panel shows them


@pytest.mark.parametrize("combo", COMBOS)
def test_every_page_is_one_ground_from_edge_to_edge(combo, settings):
    view, now = load(combo, settings)
    seen = Counter()
    for day in WEEK:
        shown = on_day(view, day)
        for screen, frames in pages(shown, now).items():
            for number, frame in enumerate(frames):
                corners = {frame.getpixel(at) for at in ((0, 0), (63, 0), (0, 63), (63, 63))}
                assert BLACK not in corners, (combo, day, screen, "a corner is bare black")
                edge = [frame.getpixel((x, y)) for x in range(SIZE) for y in (0, 63)]
                edge += [frame.getpixel((x, y)) for y in range(SIZE) for x in (0, 63)]
                assert edge.count(BLACK) == 0, (combo, day, screen, "black along the edge")
                black = sum(1 for pixel in frame.getdata() if pixel == BLACK)
                assert black <= SIZE * SIZE // 16, (combo, day, screen, number, black)
                seen[screen, number] += 1
                if screen in ("today", "parked", "week", "books"):
                    assert frame.getpixel((0, 0)) == day_ground(day), (combo, day, screen)
    if view.city is not None and view.city.weather is not None:
        assert seen["city", 1] == 7, "the real weather and lines pages, every weekday"


@pytest.mark.parametrize("combo", COMBOS)
def test_every_word_reads_on_what_it_sits_on(combo, settings, monkeypatch):
    """Every word on every page (paged clips included), against each neighbouring pixel that
    is not text: at least WORD_FLOOR, or EXACT_FLOOR for a status or alert colour drawn as
    itself on its band."""
    view, now = load(combo, settings)
    watch = Contrast(monkeypatch)
    for day in WEEK:
        pages(on_day(view, day), now)
    assert watch.words > 7 * 20
    assert watch.worst[False] >= WORD_FLOOR, (combo, watch.where.get(False))
    assert watch.worst[True] >= EXACT_FLOOR, (combo, watch.where.get(True))


def test_the_transit_and_alert_pages_keep_their_words_readable_too(monkeypatch):
    marked = city_lines(("W", "planned", False), ("M", "delays", True), ("Q66", "suspended", True))
    long = "Southbound [N][W] trains skip 39 Av; take the M or the Q69 bus instead of waiting."
    marked = (*marked[:1], LineStatus("W", "subway", "delays", long, True, 1), *marked[2:])
    watch = Contrast(monkeypatch)
    for day in WEEK:
        for code, alert in ((0, ()), (71, ("Winter Storm Warning",)), (3, ("Wind Advisory",))):
            stale = "2026-09-30T19:00:00Z" if code == 3 else "2026-09-30T22:05:00Z"
            w = weather(code=code, alerts=alert, fetched_at_utc=stale)
            city = replace(status(weather=w, lines=marked), day_local=day)
            render_city(view_of(city, day=day), CITY_NOW)
    assert watch.worst[False] >= WORD_FLOOR, watch.where.get(False)
    assert watch.worst[True] >= EXACT_FLOOR, watch.where.get(True)


# colours that carry meaning are drawn as themselves on the real pages


@pytest.mark.parametrize("day", WEEK)
def test_status_words_are_their_own_red_amber_and_green_on_the_board(day):
    marked = city_lines(("W", "planned", True), ("M", "delays", True), ("Q66", "suspended", True))
    city = replace(status(lines=marked), day_local=day)
    page = render_city(view_of(city, day=day), CITY_NOW).frames[1]
    ground = day_ground(day)
    assert page.getpixel((0, 0)) == ground and page.getpixel((SIZE - 1, SIZE - 1)) == band(ground)
    board = {page.getpixel((x, y)) for x in range(BOARD_X, SIZE) for y in range(ROWS_TOP, SIZE)}
    assert board == {band(ground), GREEN, AMBER, RED}, "the board, and three status colours"
    left = {page.getpixel((x, y)) for x in range(BOARD_X) for y in range(ROWS_TOP, SIZE)}
    assert band(ground) not in left and ground in left, "the bullets stay on the page itself"


@pytest.mark.parametrize("kind", ["clear", "snow", "fog", "overcast", "heavy-rain"])
def test_an_alert_is_amber_on_its_band_whatever_the_sky(kind):
    code = {"clear": 0, "snow": 71, "fog": 45, "overcast": 3, "heavy-rain": 65}[kind]
    sky, _floor = weather_grounds(kind, night=False)
    for alert, top in (("Wind Advisory", NOTICE_Y), ("Winter Storm Warning", HIGH_LOW_Y)):
        city = status(weather=weather(code=code, alerts=(alert,)))
        frame = city_screen._weather_frame(view_of(city), city, CITY_NOW)
        rows = range(max(top - 2, city_screen.BAND_TOP_MIN), FLOOR_TOP)
        strip = {frame.getpixel((x, y)) for x in range(SIZE) for y in rows}
        assert strip == {band(sky), AMBER}, (kind, alert, "one band, one amber, nothing else")
        first = max(top - 2, city_screen.BAND_TOP_MIN)
        above = {frame.getpixel((x, first - 1)) for x in range(SIZE)}
        assert band(sky) not in above, "the band starts where it should, no higher"
        under = {frame.getpixel((x, FLOOR_TOP - 1)) for x in range(SIZE)}
        assert under == {band(sky)}, "a clear row of band between the alert and the divider"
        calm = status(weather=weather(code=code))
        quiet = city_screen._weather_frame(view_of(calm), calm, CITY_NOW)
        hours = [(x, y) for x in range(SIZE) for y in range(FLOOR_TOP, SIZE)]
        assert [frame.getpixel(at) for at in hours] == [quiet.getpixel(at) for at in hours], (
            kind,
            "the hour-by-hour strip, its temperatures included, is untouched by an alert",
        )


def test_a_stale_header_is_amber_on_a_band_and_the_picture_stays_off_it():
    for code in (0, 2, 3, 45, 61, 65, 71, 95):
        for alert in ((), ("Wind Advisory",), ("Winter Storm Warning",)):
            w = weather(code=code, alerts=alert, fetched_at_utc="2026-09-30T19:00:00Z")
            city = status(weather=w)
            frame = city_screen._weather_frame(view_of(city), city, CITY_NOW)
            sky = weather_grounds(condition(code, 18), night=False)[0]
            head = {frame.getpixel((x, y)) for x in range(SIZE) for y in range(8)}
            assert head == {band(sky), AMBER}, (code, alert, "the as-of row and nothing over it")


@pytest.mark.parametrize("day", WEEK)
def test_today_keeps_its_hills_its_label_hierarchy_and_a_ring_that_can_be_seen(day, settings):
    view, now = load("rest__sleep-missing__broken-last-week__base", settings)
    view = replace(on_day(view, day), today_dot=False, steps=4321, sleep_hours=7.4)
    frame = render_today(view, now).poster
    assert frame.getpixel((0, SIZE - 1)) in (HILL_FRONT, HILL_BACK), "hills along the foot"
    hills = {frame.getpixel((x, y)) for x in range(SIZE) for y in range(38, SIZE)}
    assert {HILL_BACK, HILL_FRONT, HILL_CREST} <= hills
    colours = Counter(frame.getdata())
    assert colours[LABEL] > 30, "SLEEP and STEPS are both the label blue, on sky and on hill"
    steps_row = {frame.getpixel((x, y)) for x in range(2, 22) for y in range(51, 56)}
    assert LABEL in steps_row and TEXT not in steps_row, "STEPS is a label, not a value"
    assert GREEN in colours, "sleep in its band keeps its green"
    ring = [frame.getpixel((6 + dx, 44 + dy)) for dx, dy in ((4, 0), (-4, 0), (0, 4), (0, -4))]
    under = frame.getpixel((6, 44))
    assert all(contrast(pixel, under) >= MARK_FLOOR for pixel in ring), (day, ring, under)
    parked = render_parked(view, now).poster
    marks = {
        parked.getpixel((x, y))
        for x in range(screens.ZZ_X, screens.ZZ_X + 8)
        for y in range(screens.AS_OF_Y, screens.AS_OF_Y + 6)
    } - {HILL_FRONT, HILL_BACK, HILL_CREST}
    assert marks and all(contrast(mark, HILL_FRONT) >= WORD_FLOOR for mark in marks), marks


@pytest.mark.parametrize("target", [1, 2, 3, 4, 5, 7])
def test_planets_and_orbits_never_touch_whatever_the_weekly_target(target, settings):
    view = replace(load(COMBOS[0], settings)[0], week_target=target, week_dots=max(target - 1, 1))
    for day in WEEK:
        frame = render_week(on_day(view, day), CITY_NOW).poster
        assert frame.size == (SIZE, SIZE)
        ground = day_ground(day)
        row = [frame.getpixel((x, DOT_ROW_Y)) for x in range(SIZE)]
        runs = "".join("." if pixel == ground else "#" for pixel in row).strip(".")
        centres, _radius = dot_layout(target)
        assert row[0] == ground and row[-1] == ground, "clear of both edges"
        gaps = [gap for gap in runs.split("#") if gap]
        outer = [gap for gap in gaps if len(gap) >= 2]
        assert len(outer) >= len(centres) - 1, (target, runs, "two clear pixels between dots")
    white_rows = {y for y in range(8, 27) for x in range(SIZE) if frame.getpixel((x, y)) == TEXT}
    assert not white_rows, "no ring: nothing in the text white among the planets"


# the weather sky


def test_the_weather_page_is_its_sky_over_a_deeper_floor():
    assert len({tone for tone in SKY_TONES.values() if tone}) == 8, "eight skies"
    assert set(SKY_TONES) == set(CONDITIONS) == set(ICONS) == set(MINI_ICONS)
    for kind, tone in SKY_TONES.items():
        sky, floor = weather_grounds(kind, night=False)
        assert sky == (tone or OPEN_NIGHT) and floor == dim(sky, FLOOR_SHADE), kind
        assert sky != BLACK and floor != BLACK and luminance(floor) < luminance(sky)
        assert contrast(TEXT, sky) >= CONTRAST_FLOOR, (kind, "the temperature reads on its sky")
        assert contrast(AMBER, band(sky)) >= WORD_FLOOR, (kind, "an alert reads on its band")
    assert weather_grounds("overcast", night=True)[0] == dim(SKY_TONES["overcast"], NIGHT_SKY)
    assert weather_grounds("clear-night", night=True)[0] == OPEN_NIGHT
    assert weather_grounds("partly-night", night=True)[0] == OPEN_NIGHT, "a partly clear night"
    assert condition(2, 23) == "partly-night" and condition(2, 12) == "partly"
    assert "y" not in "".join(ICONS["partly-night"] + MINI_ICONS["partly-night"]), "no sun at night"
    assert weather_grounds("partly", night=False)[0] == SKY_TONES["partly"]


# the month feature's fact, on the page


def fact_feature():
    from app.month.spec import parse_feature
    from tests.month.conftest import feature_object

    return parse_feature(feature_object("2026-10"), "2026-10", facts=True)


def test_a_fact_is_drawn_whole_under_its_caption_on_the_weekday_wall(monkeypatch):
    from app.month.spec import fact_lines
    from tests.render import record_text

    feature = fact_feature()
    drawn = record_text(monkeypatch)
    for number in (1, 9, 23):
        day = f"2026-10-{number:02d}"
        plate = feature.days[number - 1]
        want = fact_lines(plate.fact)
        assert want and len(want) <= len(FACT_YS)
        drawn.clear()
        page = render_month(DayView(day_local=day, month_feature=feature)).frames[0]
        rows = {y: text for text, _x, y, *_ in drawn}
        got = [(rows.get(y) or "").upper() for y in FACT_YS[: len(want)]]
        assert got == want, "every line of the fact"
        assert rows[month.FACT_CAPTION_Y].upper() == plate.caption.upper()
        shown = {text.upper() for text in rows.values()}
        assert feature.title.upper() not in shown, "the title gives its row to the fact"
        ground = day_ground(day)
        assert page.getpixel((0, 0)) == ground == page.getpixel((SIZE - 1, SIZE - 1))
        assert page.getpixel((15, 1)) == slot(ground, BLACK), "the plate's mat, a recess"
        foot = [page.getpixel((x, SIZE - 1)) for x in range(SIZE)]
        assert set(foot) == {ground}, "a margin under the last line"
        top = [page.getpixel((x, 0)) for x in range(SIZE)]
        assert set(top) == {ground}, "and one above the plate"


def test_a_feature_without_facts_keeps_its_title_rails_and_big_plate(monkeypatch, settings):
    from tests.render import record_text

    view, now = load("train__all-sources__alive__base", settings)
    assert view.month_feature is not None and not view.month_feature.days[0].fact
    drawn = record_text(monkeypatch)
    render_month(view, now)
    texts = [text for text, *_ in drawn]
    assert view.month_feature.title.upper() in [text.upper() for text in texts]
