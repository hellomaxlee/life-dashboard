"""The City screen: the weather page, the lines page, the detail pages, what it says when data
is stale or missing, and where it sits in the rotation. Views are built from CityStatus
objects made here; the store is a stub, so nothing depends on app/city's fetch or tables."""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.city.model import STATUS_ORDER, CityStatus, HourWeather, LineStatus, Weather
from app.render import city as city_screen
from app.render.city import (
    BAR_HEIGHT,
    BAR_WIDTH,
    BULLET_STATUS_X,
    CONDITIONS,
    DETAIL_BOTTOM,
    DETAIL_MS,
    DETAIL_TOP,
    HEADER_Y,
    HIGH_LOW_Y,
    ICON_X,
    ICON_Y,
    ICONS,
    LINES_MS,
    MINI_ICONS,
    NIGHT_SKY,
    NOTICE_Y,
    SKY_TONES,
    STATUS_WORDS,
    STEP_LABEL_Y,
    STEP_TEMP_Y,
    SUBWAY_COLORS,
    TEMP_X,
    TEMP_Y,
    WEATHER_MS,
    WIDTH,
    affected_lines,
    alert_lines,
    as_of_label,
    badge_box,
    bar_box,
    bullet_ink,
    condition,
    degrees,
    detail_lines,
    headline_pages,
    hour_label,
    line_color,
    line_rows,
    mark_lines,
    precip_px,
    render_city,
    sign_width,
    strip_steps,
    word_width,
    wrap_headline,
)
from app.render.font import BODY, SMALL, text_width
from app.render.frame import SIZE
from app.render.gamma import led_gamma
from app.render.palette import (
    AMBER,
    BUS_BLUE,
    GOLD,
    GREEN,
    MOONLIGHT,
    MTA_ORANGE,
    MTA_YELLOW,
    RAIN,
    RED,
    SECONDARY,
    TRACK,
    day_accent,
    dim,
)
from app.render.rotation import device_parts, hold_ms, rotation_sequence, sequence_names
from app.render.screens import LEFT, RIGHT, clean_summary
from app.render.view import DayView, city_stale_minutes, fixture_city
from app.render.view_db import view_from_db
from app.timeutil import from_utc_iso, to_utc_iso
from tests.render import COMBOS, DAYS, WEEK_41, load, record_text, without

DAY = "2026-09-30"
NOW = from_utc_iso("2026-09-30T22:15:00Z")
FRESH = "2026-09-30T22:05:00Z"
BLACK = (0, 0, 0)
HOURS = ((18, 57.2, 20, 3), (21, 55.1, 45, 61), (0, 53.4, 70, 63), (3, 52.0, 60, 61))
HOURS += ((6, 50.6, 30, 3), (9, 54.3, 10, 2))
SEVEN = (("N", "subway"), ("W", "subway"), ("M", "subway"))
SEVEN += (("Q103", "bus"), ("Q66", "bus"), ("Q69", "bus"), ("B62", "bus"))


pytestmark = pytest.mark.usefixtures("no_scenery")


def steps(rows=HOURS) -> tuple[HourWeather, ...]:
    out, tomorrow, last = [], False, -1
    for hour, temp, pct, code in rows:
        tomorrow = tomorrow or hour < last
        last = hour
        out.append(HourWeather(hour, temp, pct, code, tomorrow))
    return tuple(out)


SKIES = {tone for tone in SKY_TONES.values() if tone} | {
    dim(tone, NIGHT_SKY) for tone in SKY_TONES.values() if tone
}


ICON_BOX = (ICON_X, ICON_Y, ICON_X + 24, ICON_Y + 24)


def no_sky(frame):
    """The page with the sky's own tones put back to black; windows and stars stay."""
    return without(frame, SKIES)


def weather(**changes) -> Weather:
    base = Weather(56.5, 55.4, 3, 62.4, 50.6, 70, steps(), (), FRESH)
    return replace(base, **changes)


def lines(*statuses: tuple[str, str, bool], names=SEVEN) -> tuple[LineStatus, ...]:
    """The seven lines, all ok, with (line, status, now) overrides."""
    special = {name: (status, now) for name, status, now in statuses}
    out = []
    for name, kind in names:
        status, now = special.get(name, ("ok", False))
        headline = None if status == "ok" else f"{name} trains are affected."
        out.append(LineStatus(name, kind, status, headline, now, 0 if status == "ok" else 1))
    return tuple(out)


def view_of(city: CityStatus | None, day: str = DAY, **changes) -> DayView:
    return DayView(day_local=day, city=city, **changes)


def status(**changes) -> CityStatus:
    base = CityStatus(DAY, weather(), lines(), FRESH)
    return replace(base, **changes)


class Pages:
    """Everything drawn, split by page: a page starts when the screen takes a new frame."""

    def __init__(self, monkeypatch) -> None:
        self.recorded = record_text(monkeypatch)
        self._starts: list[int] = []
        original = city_screen.new_frame

        def marking(*args, **kwargs):
            self._starts.append(len(self.recorded))
            return original(*args, **kwargs)

        monkeypatch.setattr(city_screen, "new_frame", marking)

    def clear(self) -> None:
        self.recorded.clear()
        self._starts.clear()

    def __getitem__(self, page: int) -> list:
        ends = [*self._starts[1:], len(self.recorded)]
        return self.recorded[self._starts[page] : ends[page]]


def texts(recorded: list, y: int | None = None) -> list[str]:
    return [r[0] for r in recorded if y is None or r[2] == y]


def colours(image) -> set[tuple[int, int, int]]:
    return {colour for _, colour in image.getcolors(SIZE * SIZE)}


def black_count(image) -> int:
    return dict((colour, count) for count, colour in image.getcolors(SIZE * SIZE)).get(BLACK, 0)


def lit_mask(frame, box: tuple[int, int, int, int]) -> frozenset[tuple[int, int]]:
    x0, y0, x1, y1 = box
    return frozenset(
        (x, y)
        for x in range(x0, x1 + 1)
        for y in range(y0, y1 + 1)
        if frame.getpixel((x, y)) != BLACK
    )


# numbers


@pytest.mark.parametrize(
    ("value", "shown"),
    [(56.5, "57"), (56.49, "56"), (57.2, "57"), (-0.5, "0"), (-1.5, "-1"), (-1.51, "-2")],
)
def test_temperatures_round_half_up_to_whole_degrees(value, shown):
    assert degrees(value) == shown


def test_what_is_not_a_finite_number_is_no_temperature():
    for value in (None, float("nan"), float("inf"), True, "57"):
        assert degrees(value) is None


@pytest.mark.parametrize(
    ("percent", "pixels"), [(0, 0), (1, 0), (19, 0), (20, 1), (49, 2), (60, 3), (99, 4), (100, 5)]
)
def test_a_rain_gauge_is_floored_never_rounded_up(percent, pixels):
    assert BAR_HEIGHT == 5, "five rows since the strip made room for the sky (2026-11-15)"
    assert precip_px(percent) == pixels


def test_a_rain_percent_out_of_range_or_not_a_number_never_overfills():
    assert [precip_px(v) for v in (-5, 250, None, "x", float("nan"), True)] == [0, 5, 0, 0, 0, 0]


@pytest.mark.parametrize(
    ("hour", "label"),
    [(0, "12A"), (1, "1A"), (9, "9A"), (11, "11A"), (12, "12P"), (13, "1P"), (23, "11P")],
)
def test_hour_labels_are_twelve_hour_with_a_or_p(hour, label):
    assert hour_label(hour) == label


def test_something_that_is_not_an_hour_is_dashes():
    assert [hour_label(v) for v in (24, -1, None, 2.5, True)] == ["--"] * 5


# the weather page


def test_the_weather_page_shows_the_rounded_temperature_high_low_and_condition(monkeypatch):
    pages = Pages(monkeypatch)
    clip = render_city(view_of(status()), NOW)
    page, recorded = clip.frames[0], pages[0]
    assert ("57", TEMP_X, TEMP_Y, BODY.name, 2) in recorded, "56.5 is drawn as 57, double size"
    assert texts(recorded, HEADER_Y)[:2] == ["WEATHER", "WED 30"]
    high = next(r for r in recorded if r[0] == "62")
    low = next(r for r in recorded if r[0] == "51")
    assert high[2] == low[2] == HIGH_LOW_Y, "one line under the temperature"
    line = sorted((r[1], r[0]) for r in recorded if r[2] == HIGH_LOW_Y)
    assert [text for _, text in line] == ["H", "62", "L", "51"]
    assert line[0][0] == LEFT and line[-1][0] + text_width("51") < ICON_X, "clear of the icon"
    assert texts(recorded, NOTICE_Y) == [], "the icon says the condition; no word for it"
    degree = {page.getpixel((x, y)) for x in range(24, 29) for y in range(TEMP_Y, TEMP_Y + 4)}
    assert degree - SKIES == {BLACK, (255, 255, 255)}, "a degree mark after the number"
    big = page.crop((ICON_X, ICON_Y, ICON_X + 24, ICON_Y + 24))
    assert big.getbbox() is not None, "the condition's icon at twice its size"
    assert page.getpixel((LEFT, HEADER_Y)) == day_accent(DAY)


def test_every_number_on_the_weather_page_is_a_field_of_the_status(monkeypatch):
    pages = Pages(monkeypatch)
    render_city(view_of(status()), NOW)
    page_one, recorded = pages[0], pages.recorded
    allowed = {"57", "62", "51", "55", "53", "52", "54", "WED 30"}
    allowed |= {"6P", "9P", "2A", "3A", "6A", "9A"}
    numeric = {r[0] for r in page_one if any(ch.isdigit() for ch in r[0])}
    assert numeric == allowed, "12A is drawn as a stroke and '2A'; nothing else carries a digit"
    assert "55.4" not in " ".join(texts(recorded)) and "70" not in texts(recorded)


def test_six_steps_are_drawn_left_to_right_with_their_hours_past_midnight(monkeypatch):
    drawn = strip_steps(weather())
    assert [s.label for s in drawn] == ["6P", "9P", "12A", "3A", "6A", "9A"]
    assert [s.x for s in drawn] == [2, 12, 22, 32, 42, 52]
    assert [s.tomorrow for s in drawn] == [False, False, True, True, True, True]
    assert [s.temp for s in drawn] == ["57", "55", "53", "52", "51", "54"]
    pages = Pages(monkeypatch)
    page = render_city(view_of(status()), NOW).frames[0]
    recorded = pages[0]
    labels = sorted((r[1], r[0]) for r in recorded if r[2] == STEP_LABEL_Y)
    assert [text for _, text in labels] == ["6P", "9P", "2A", "3A", "6A", "9A"]
    for (x, text), step in zip(labels, drawn, strict=True):
        assert step.x <= x and x + text_width(text) <= step.x + 9, "inside its own column"
    stroke = {page.getpixel((22, y)) for y in range(STEP_LABEL_Y, STEP_LABEL_Y + 5)}
    assert stroke == {SECONDARY}, "the 1 of 12A is a one-pixel stroke so the label fits"
    assert page.getpixel((labels[0][0], STEP_LABEL_Y)) != SECONDARY, "today's steps are not dimmed"
    rule = [page.getpixel((21, y)) for y in range(city_screen.STEP_ICON_Y, 55)]
    assert rule[0] != BLACK and BLACK in rule, "a dotted rule where tomorrow begins"
    temps = sorted((r[1], r[0]) for r in recorded if r[2] == STEP_TEMP_Y)
    assert [text for _, text in temps] == ["57", "55", "53", "52", "51", "54"]
    more = weather(hours=steps() + steps())
    assert len(strip_steps(more)) == 6, "never more than six"
    assert render_city(view_of(status(weather=weather(hours=()))), NOW).frames[0].getbbox()


def test_late_hours_and_three_digit_labels_keep_their_columns(monkeypatch):
    late = steps(((22, 101.4, 0, 0), (1, 100.0, 0, 0), (4, -12.0, 0, 0), (7, -25.0, 0, 0)))
    drawn = strip_steps(weather(hours=late))
    assert [s.label for s in drawn] == ["10P", "1A", "4A", "7A"]
    assert [s.tomorrow for s in drawn] == [False, True, True, True]
    pages = Pages(monkeypatch)
    render_city(view_of(status(weather=weather(hours=late))), NOW)
    recorded = pages[0]
    row = sorted((r[1], r[0]) for r in recorded if r[2] == STEP_TEMP_Y)
    assert [text for _, text in row] == ["0", "00", "2"], "-25 cannot fit and is left out"
    for x, text in row + sorted((r[1], r[0]) for r in recorded if r[2] == STEP_LABEL_Y):
        column = LEFT + (x - LEFT) // 10 * 10
        assert x + text_width(text) <= column + 9, text


def test_eleven_oclock_is_two_matching_ones_never_a_stroke_beside_a_one():
    """Max, 2026-10-09: "11A" drawn as a one-pixel stroke and a full 1 read as "I1"."""
    rows = tuple((h, 60.0, 0, 3) for h in (8, 11, 14, 17, 20, 23))
    drawn = strip_steps(weather(hours=steps(rows)))
    assert [s.label for s in drawn] == ["8A", "11A", "2P", "5P", "8P", "11P"]
    page = render_city(view_of(status(weather=weather(hours=steps(rows)))), NOW).frames[0]
    one = [(False, True), (True, True), (False, True), (False, True), (False, True)]
    for step in (drawn[1], drawn[5]):
        lit = [
            [page.getpixel((step.x + dx, STEP_LABEL_Y + dy)) != BLACK for dx in range(9)]
            for dy in range(5)
        ]
        first = [tuple(row[0:2]) for row in lit]
        second = [tuple(row[3:5]) for row in lit]
        assert first == second == one, f"{step.label}: both ones are the same glyph"
        assert not any(row[2] or row[5] for row in lit), "a dark column after each one"
        assert any(row[8] for row in lit), "the letter ends on the column's last pixel"


def test_each_gauge_is_filled_from_the_bottom_by_the_floored_percent():
    rows = tuple(
        (h, 60.0, pct, 3) for h, pct in zip(range(3, 21, 3), (0, 1, 49, 99, 100, 50), strict=True)
    )
    page = render_city(view_of(status(weather=weather(hours=steps(rows)))), NOW).frames[0]
    drawn = strip_steps(weather(hours=steps(rows)))
    for step, want in zip(drawn, (0, 0, 2, 4, 5, 2), strict=True):
        x0, y0, x1, y1 = bar_box(step)
        assert (x1 - x0 + 1, y1 - y0 + 1) == (BAR_WIDTH, BAR_HEIGHT)
        column = [page.getpixel((x0, y)) for y in range(y0, y1 + 1)]
        assert column == [TRACK] * (BAR_HEIGHT - want) + [RAIN] * want, step.label
        box = {page.getpixel((x, y)) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)}
        assert box <= {TRACK, RAIN}
    lut = led_gamma(page)
    x0, y0, _, y1 = bar_box(strip_steps(weather(hours=steps(rows)))[2])
    assert lut.getpixel((x0, y0)) != lut.getpixel((x0, y1)) != BLACK, "told apart on LEDs"


WMO = {
    0: "clear",
    1: "partly",
    2: "partly",
    3: "overcast",
    45: "fog",
    48: "fog",
    51: "rain",
    53: "rain",
    55: "heavy-rain",
    56: "rain",
    57: "heavy-rain",
    61: "rain",
    63: "rain",
    65: "heavy-rain",
    66: "rain",
    67: "heavy-rain",
    71: "snow",
    73: "snow",
    75: "snow",
    77: "snow",
    80: "rain",
    81: "rain",
    82: "heavy-rain",
    85: "snow",
    86: "snow",
    95: "thunder",
    96: "thunder",
    99: "thunder",
}


def test_every_wmo_group_has_its_own_icon_and_an_unknown_code_a_neutral_one():
    assert {code: condition(code, 12) for code in WMO} == WMO
    assert condition(0, 22) == condition(0, 3) == "clear-night" and condition(0, 6) == "clear"
    assert condition(0) == "clear", "no hour: the sun"
    for odd in (4, 44, 68, 79, 100, 999, -1, None, "x", 3.0, True):
        assert condition(odd, 12) == "unknown"
    assert set(ICONS) == set(MINI_ICONS) == set(CONDITIONS)
    assert len({ICONS[k] for k in CONDITIONS}) == len(CONDITIONS), "no two groups share an icon"
    assert len({MINI_ICONS[k] for k in CONDITIONS}) == len(CONDITIONS)
    for kind in CONDITIONS:
        assert [len(row) for row in ICONS[kind]] == [12] * 12, kind
        assert [len(row) for row in MINI_ICONS[kind]] == [7] * 6, kind
        assert set("".join(ICONS[kind] + MINI_ICONS[kind])) <= set(city_screen.INK) | {"."}
        assert set("".join(ICONS[kind])) != {"."} and set("".join(MINI_ICONS[kind])) != {"."}
    inks = {ink for kind in ("unknown",) for row in ICONS[kind] for ink in row} - {"."}
    assert inks == {"g"}, "neutral grey, no weather colour"


def test_any_code_renders_and_the_icon_follows_the_code_and_the_hour():
    seen = {}
    for code in [*range(-1, 101), 999, None]:
        w = weather(code=code, hours=steps(tuple((h, 50.0, 10, code) for h in range(0, 18, 3))))
        page = render_city(view_of(status(weather=w)), NOW).frames[0]
        icon = no_sky(page).crop(ICON_BOX)
        assert icon.getbbox() is not None, code
        seen.setdefault(condition(code, 18), icon.tobytes())
        assert seen[condition(code, 18)] == icon.tobytes()
    assert len(set(seen.values())) == 9, "nine groups by day"
    clear = status(weather=weather(code=0))
    day = render_city(view_of(clear), NOW).frames[0].crop(ICON_BOX)
    night = render_city(view_of(clear), NOW + timedelta(hours=5)).frames[0].crop(ICON_BOX)
    assert GOLD in colours(day) and MOONLIGHT not in colours(day)
    assert MOONLIGHT in colours(night) and GOLD not in colours(night)


def test_an_alert_is_shown_in_amber_and_takes_the_notice_line(monkeypatch):
    pages = Pages(monkeypatch)
    w = weather(alerts=("Wind Advisory", "Coastal Flood Statement"))
    page = render_city(view_of(status(weather=w)), NOW).frames[0]
    recorded = pages[0]
    assert texts(recorded, NOTICE_Y) == ["WIND ADVISORY"], "the first alert, not the condition"
    assert "COASTAL FLOOD STATEMENT" not in texts(recorded)
    row = {page.getpixel((x, y)) for x in range(SIZE) for y in range(NOTICE_Y, NOTICE_Y + 5)}
    assert row - SKIES == {BLACK, AMBER}
    assert len([r for r in recorded if r[2] == STEP_TEMP_Y]) == 6, "one line: the temperatures stay"


def test_a_long_alert_wraps_over_the_step_temperatures_and_is_cut_safely(monkeypatch):
    assert alert_lines("Winter Storm Warning") == ["WINTER STORM", "WARNING"]
    assert alert_lines("Severe Thunderstorm Warning") == ["SEVERE T-STORM", "WARNING"]
    assert alert_lines("Special Weather Statement") == ["SPECIAL WEATHER", "STATEMENT"]
    long = "Severe Thunderstorm Warning until further notice for the whole borough ⚡"
    cut = alert_lines(long)
    assert len(cut) == 2 and cut[1].endswith("...") and cut[0] == "SEVERE T-STORM"
    assert all(text_width(line) <= WIDTH for line in cut)
    assert alert_lines("W" * 40)[0].endswith("...") and text_width(alert_lines("W" * 40)[0]) <= 60
    assert alert_lines("") == [] and alert_lines("  ☃ ") == []
    pages = Pages(monkeypatch)
    page = render_city(view_of(status(weather=weather(alerts=(long,)))), NOW).frames[0]
    recorded = pages[0]
    assert texts(recorded, HIGH_LOW_Y) == [cut[0]] and texts(recorded, NOTICE_Y) == [cut[1]]
    assert len([r for r in recorded if r[2] == STEP_TEMP_Y]) == 6, "the hourly temperatures stay"
    rows = range(HIGH_LOW_Y, NOTICE_Y + SMALL.height)
    left = {page.getpixel((x, y)) for x in range(LEFT, ICON_X - 8) for y in rows}
    assert left == {BLACK, AMBER}, "both lines in one colour, and nothing else beside them"
    for text, x, _, font_name, _ in recorded:
        assert font_name != SMALL.name or (0 <= x and x + text_width(text) <= SIZE), text
    blank = render_city(view_of(status(weather=weather(alerts=("", 7, None)))), NOW)
    assert blank.frames[0].tobytes() == render_city(view_of(status()), NOW).frames[0].tobytes()


def test_a_temperature_too_wide_for_double_size_steps_down_and_stays_whole(monkeypatch):
    pages = Pages(monkeypatch)
    sizes = []
    for temp, high in ((101.4, 103.6), (-12.3, -4.0), (None, None), (7.0, 9.0)):
        pages.clear()
        render_city(view_of(status(weather=weather(temp_f=temp, high_f=high))), NOW)
        recorded = pages[0]
        shown = degrees(temp) or "--"
        hero = [r for r in recorded if r[0] == shown and r[3] == BODY.name]
        assert len(hero) == 1, "drawn once, in the body face"
        text, x, _, _, scale = hero[0]
        sizes.append(scale)
        assert x + text_width(text, BODY, scale) + 4 < ICON_X + 4, "clear of the icon"
        assert (degrees(high) or "--") in texts(recorded)
    assert sizes == [1, 1, 2, 2]


# the lines page


def test_the_lines_page_shows_every_line_with_its_badge_and_a_matching_word(monkeypatch):
    marked = lines(("W", "planned", False), ("M", "delays", True), ("Q66", "suspended", True))
    pages = Pages(monkeypatch)
    page = render_city(view_of(status(lines=marked)), NOW).frames[1]
    assert texts(pages[1])[:2] == ["TRANSIT", "WED 30"]
    drawn = pages[1][2:]
    tight, rows = line_rows(7)
    assert not tight and rows == [8, 16, 24, 32, 40, 48, 56]
    words = {"ok": "OK", "planned": "WORK", "delays": "DELAYS", "suspended": "NO SVC"}
    assert words == STATUS_WORDS and set(words) == set(STATUS_ORDER)
    colours = {"ok": GREEN, "planned": AMBER, "delays": RED, "suspended": RED}
    statuses = [r for r in drawn if r[0] in words.values()]
    assert len(statuses) == len(marked)
    for line, y, word in zip(marked, rows, statuses, strict=True):
        assert (word[0], word[2], word[3]) == (words[line.status], y + 1, SMALL.name)
        assert word[1] >= BULLET_STATUS_X, "one status column, pushed right only by a long pill"
        colour, ink = line_color(line), bullet_ink(line_color(line))
        assert page.getpixel((LEFT + 3, y)) == colour, "the bullet is the line's colour"
        sign = {page.getpixel((x, yy)) for x in range(LEFT, word[1] - 2) for yy in range(y, y + 7)}
        assert sign == {BLACK, colour, ink}, (line.line, "the name sits INSIDE its bullet")
        assert not [r for r in drawn if r[0] == line.line and r[3] == BODY.name], "none beside it"
        band = {page.getpixel((x, yy)) for x in range(word[1], SIZE) for yy in range(y, y + 7)}
        assert band == {BLACK, colours[line.status]}, line.line


def test_bullets_use_the_mta_colours_and_a_bus_is_never_a_train():
    assert SUBWAY_COLORS["N"] == SUBWAY_COLORS["W"] == MTA_YELLOW == (252, 204, 10)
    assert SUBWAY_COLORS["F"] == SUBWAY_COLORS["M"] == MTA_ORANGE == (232, 82, 0)
    assert set(SUBWAY_COLORS) == set("ACEBDFMGJZLNQRW1234567S")
    groups = ("ACE", "BDFM", "G", "JZ", "L", "NQRW", "123", "456", "7")
    assert len({SUBWAY_COLORS[group[0]] for group in groups}) == 9
    for group in groups:
        assert len({SUBWAY_COLORS[letter] for letter in group}) == 1
    lut = led_gamma
    for letter, colour in SUBWAY_COLORS.items():
        assert max(colour) >= 147, letter
    assert BUS_BLUE not in SUBWAY_COLORS.values()
    assert line_color(LineStatus("Q69", "bus")) == BUS_BLUE
    assert line_color(LineStatus("n", "subway")) == MTA_YELLOW
    assert line_color(LineStatus("FX", "subway")) == MTA_ORANGE
    assert line_color(LineStatus("?", "subway")) == SUBWAY_COLORS["S"]
    subway = render_city(view_of(status(lines=(LineStatus("N", "subway"),))), NOW).frames[1]
    bus = render_city(view_of(status(lines=(LineStatus("N", "bus"),))), NOW).frames[1]
    box = badge_box(8)
    assert lit_mask(subway, box) != lit_mask(bus, box), "a disc and a pill: shape, not only colour"
    assert lut(subway).getpixel((5, 11)) != lut(bus).getpixel((5, 11))


def test_status_is_never_conveyed_by_colour_alone():
    def page_for(state: str, now: bool):
        only = (LineStatus("N", "subway", state, "x", now, 1),)
        return render_city(view_of(status(lines=only)), NOW).frames[1]

    box = (BULLET_STATUS_X, 8, SIZE - 1, 14)
    masks = {
        (state, now): lit_mask(page_for(state, now), box)
        for state in STATUS_ORDER
        for now in (True, False)
    }
    assert all(masks.values()), "every status draws a mark"
    assert masks["ok", True] == masks["ok", False]
    distinct = [("ok", True), ("planned", True), ("planned", False), ("delays", True)]
    distinct += [("suspended", True)]
    for i, a in enumerate(distinct):
        for b in distinct[i + 1 :]:
            assert masks[a] != masks[b], f"{a} and {b} differ in shape with colour ignored"
    clock = masks["planned", False] - masks["planned", True]
    assert len(clock) == sum(row.count("#") for row in city_screen.CLOCK), "the later mark"
    assert lit_mask(page_for("mystery", True), box) not in masks.values(), "an unknown status"


@pytest.mark.parametrize("count", [1, 5, 7, 8])
def test_one_seven_and_eight_lines_all_fit_the_canvas(monkeypatch, count):
    names = (*SEVEN, ("BXM10+", "bus"))[:count]
    worst = tuple(LineStatus(name, kind, "planned", "x", False, 1) for name, kind in names)
    pages = Pages(monkeypatch)
    page = render_city(view_of(status(lines=worst)), NOW).frames[1]
    drawn = pages[1][2:]
    statuses = [r for r in drawn if r[0] == "WORK"]
    tight, rows = line_rows(count)
    assert not tight and len(rows) == min(count, 7), "one row form, the name always inside"
    if count == 8:
        assert len(statuses) == 6 and texts(drawn)[-1] == "+2 MORE"
        names, rows = names[:6], rows[:6]
    assert len(statuses) == len(rows)
    height = 7
    assert rows[0] > HEADER_Y + SMALL.height and rows[-1] + height <= SIZE
    assert all(b - a > height - 1 for a, b in zip(rows, rows[1:], strict=False)), "rows apart"
    clock_px = sum(row.count("#") for row in city_screen.CLOCK)
    for (name, _kind), (word, wx, wy, _, _), top in zip(names, statuses, rows, strict=True):
        assert top <= wy and wy + SMALL.height <= top + height
        sign = lit_mask(page, (LEFT, top, wx - 3, top + height - 1))
        assert sign and max(px for px, _ in sign) + 2 < wx, f"{name}: sign clear of its status"
        after = wx + text_width(word) + 1
        mark = lit_mask(page, (after, wy, SIZE - 1, wy + 4))
        assert len(mark) == clock_px and max(px for px, _ in mark) <= RIGHT + 1, name
    assert page.getpixel((0, 0)) == BLACK


def test_more_than_eight_lines_ends_with_a_count_instead_of_overflowing(monkeypatch):
    many = tuple(LineStatus(str(n), "subway") for n in range(1, 8)) + lines()
    pages = Pages(monkeypatch)
    page = render_city(view_of(status(lines=many)), NOW).frames[1]
    drawn = texts(pages[1][2:])
    assert drawn == [t for n in "123456" for t in (n, "OK")] + ["+8 MORE"]
    assert page.crop((0, 63, SIZE, SIZE)).getbbox() is None


# the detail pages


def affected_five() -> tuple[LineStatus, ...]:
    return lines(
        ("N", "planned", False),
        ("W", "delays", True),
        ("M", "suspended", True),
        ("Q103", "delays", True),
        ("Q69", "delays", False),
        ("B62", "planned", True),
    )


def test_detail_pages_are_most_severe_first_then_now_before_later(monkeypatch):
    city = status(lines=affected_five())
    assert [line.line for line in affected_lines(city)] == ["M", "W", "Q103", "Q69", "B62", "N"]
    assert [line.line for line in detail_lines(city)] == ["M", "W", "Q103", "Q69"]
    recorded = record_text(monkeypatch)
    clip = render_city(view_of(city), NOW)
    assert clip.durations_ms == (WEATHER_MS, LINES_MS, DETAIL_MS, DETAIL_MS, DETAIL_MS)
    assert clip.durations_ms == (6000, 6000, 5000, 5000, 5000)
    for frame, name in zip(clip.frames[2:], ("M", "W", "Q103"), strict=True):
        line = next(item for item in city.lines if item.line == name)
        assert frame.getpixel((LEFT + 3, HEADER_Y - 1)) == line_color(line), "three, in that order"
    headers = [r for r in recorded if r[2] == HEADER_Y and r[0] in STATUS_WORDS.values()][-3:]
    assert [r[0] for r in recorded if r[0].endswith("MORE")] == ["+1 MORE"]
    more = next(r for r in recorded if r[0] == "+1 MORE")
    last_body = [r for r in recorded[recorded.index(headers[-1]) :] if r[2] >= DETAIL_TOP]
    assert last_body[-1][2] + 7 <= more[2] or last_body[0] == more, "the count has its own line"
    assert more[2] + SMALL.height - 1 <= DETAIL_BOTTOM
    words = [r[0] for r in recorded if r[2] == HEADER_Y and r[0] in STATUS_WORDS.values()]
    assert words[-3:] == ["NO SVC", "DELAYS", "DELAYS"]
    assert len({frame.tobytes() for frame in clip.frames}) == 5

    three = status(lines=lines(("N", "planned", False), ("W", "delays", True), ("M", "ok", True)))
    recorded.clear()
    assert len(render_city(view_of(three), NOW).frames) == 3, "work has no page"
    assert not [r for r in recorded if r[0].endswith("MORE")]
    odd = status(lines=(LineStatus("N", "subway", "mystery", None, True),))
    assert len(render_city(view_of(odd), NOW).frames) == 2, "an unknown status has no page"
    bare = status(lines=(LineStatus("N", "subway", "delays", None, True),))
    recorded.clear()
    assert len(render_city(view_of(bare), NOW).frames) == 3
    assert "No details" in " ".join(texts(recorded))


def test_a_headline_is_shown_whole_over_as_many_pages_as_it_needs(monkeypatch):
    room = DETAIL_BOTTOM - DETAIL_TOP + 1
    short = "Delays of 9.8 min on the N – expect a wait."
    font, pitch, pages = headline_pages(short, room)
    assert (font, pitch) == (BODY, 9)
    assert " ".join(sum(pages, [])) == mark_lines(clean_summary(short)), "whole, lines marked"
    assert any("9.8 min" in line for line in sum(pages, [])), "a number stays with its unit"
    two = "Southbound M trains are delayed while we address a signal problem at Queens Plaza."
    font, pitch, pages = headline_pages(two, room)
    assert font is BODY and len(pages) == 2 and " ".join(sum(pages, [])) == mark_lines(two)
    hyphen = "No W trains between Astoria-Ditmars Blvd and Queensboro Plaza."
    assert "Astoria-" in sum(headline_pages(hyphen, room)[2], [])
    endless = "Trains are rerouted in both directions. " * 12
    font, pitch, pages = headline_pages(endless, room)
    assert font is SMALL and len(pages) > 1
    assert " ".join(sum(pages, [])) == endless.strip(), "nothing is cut"
    assert all((len(page) - 1) * pitch + font.height <= room for page in pages)
    assert all(text_width(line, font) <= WIDTH for page in pages for line in page)
    assert "".join(sum(headline_pages("W" * 200, room)[2], [])) == "W" * 200

    recorded = record_text(monkeypatch)
    only = (LineStatus("M", "subway", "delays", endless, True, 1),)
    clip = render_city(view_of(status(lines=only)), NOW)
    assert len(clip.frames) == 2 + len(pages) and set(clip.durations_ms[2:]) == {5000}
    wrapped = " ".join(sum(pages, [])).split()
    body = [r for r in recorded if r[2] >= DETAIL_TOP and r[0] in wrapped]
    assert [r[0] for r in body[-len(wrapped) :]] == wrapped, "drawn word by word, in order"
    assert max(r[2] for r in body) + SMALL.height - 1 <= DETAIL_BOTTOM
    for page in clip.frames[2:]:
        assert page.crop((0, DETAIL_BOTTOM + 1, SIZE, SIZE)).getbbox() is None
        box = page.getbbox()
        assert box[0] >= LEFT and box[2] <= RIGHT + 1


def test_planned_work_gets_no_detail_page_and_delays_and_suspensions_do():
    work = LineStatus("W", "subway", "planned", "No W trains after 9:45 PM.", False, 1)
    late = LineStatus("M", "subway", "delays", "M trains are delayed.", True, 1)
    gone = LineStatus("N", "subway", "suspended", "No N service.", True, 1)
    assert len(render_city(view_of(status(lines=(work,))), NOW).frames) == 2
    assert [line.line for line in detail_lines(status(lines=(work, late, gone)))] == ["N", "M"]
    assert len(render_city(view_of(status(lines=(work, late, gone))), NOW).frames) == 4


def test_all_ok_and_no_alert_is_two_calm_pages():
    clip = render_city(view_of(status()), NOW)
    assert clip.durations_ms == (WEATHER_MS, LINES_MS) == (6000, 6000)
    assert [len(part.frames) for part in device_parts(clip)] == [1, 1]
    for frame in clip.frames:
        lit = SIZE * SIZE - black_count(frame)
        assert 300 < lit < SIZE * SIZE // 2, "calm, not empty and not a wall"
    assert RED not in colours(clip.frames[1]) and AMBER not in colours(clip.frames[1])


# stale, missing, none


def test_a_part_older_than_the_limit_is_headed_as_of_in_amber(monkeypatch):
    def at(minutes: float) -> str:
        return to_utc_iso(NOW - timedelta(minutes=minutes))

    assert as_of_label(at(45), NOW, "America/New_York", 45) is None
    assert as_of_label(at(46), NOW, "America/New_York", 45) == "AS OF 5:29PM"
    assert as_of_label(at(-30), NOW, "America/New_York", 45) is None, "a fetch after now"
    assert as_of_label(at(60 * 24), NOW, "America/New_York", 45) == "AS OF TUE 6:15P"
    assert as_of_label(at(60 * 24 * 8), NOW, "America/New_York", 45) == "AS OF 8D AGO"
    assert as_of_label(None, NOW, "America/New_York", 45) == "AGE UNKNOWN"
    assert as_of_label("yesterday", NOW, "America/New_York", 45) == "AGE UNKNOWN"
    assert as_of_label(at(600), None, "America/New_York", 45) is None, "no clock, no claim"
    assert as_of_label(at(50), NOW, "America/New_York", 60) is None, "the limit is the view's"

    old = status(weather=weather(fetched_at_utc=at(130)), lines=lines(("M", "delays", True)))
    recorded = record_text(monkeypatch)
    clip = render_city(view_of(old), NOW)
    assert texts(recorded, HEADER_Y)[:3] == ["AS OF 4:05PM", "TRANSIT", "WED 30"]
    assert "WEATHER" not in texts(recorded)
    header = {clip.frames[0].getpixel((x, y)) for x in range(SIZE) for y in range(HEADER_Y, 7)}
    assert header == {BLACK, AMBER}
    assert clip.frames[1].getpixel((LEFT, HEADER_Y)) == day_accent(DAY), "transit is fresh"
    assert ("57", TEMP_X, TEMP_Y, BODY.name, 2) in recorded, "the old number is still shown"

    both = replace(old, transit_fetched_at_utc=at(200))
    recorded.clear()
    clip = render_city(view_of(both), NOW)
    assert texts(recorded, HEADER_Y)[:2] == ["AS OF 4:05PM", "AS OF 2:55PM"]
    assert "TRANSIT" not in texts(recorded)
    stale_lines = [r for r in recorded if r[0] == "AS OF 2:55PM"]
    assert len(stale_lines) == 2 and stale_lines[1][2] == DETAIL_BOTTOM - 4, "the detail's foot"
    foot = clip.frames[2].crop((0, DETAIL_BOTTOM - 4, SIZE, DETAIL_BOTTOM + 1))
    assert colours(foot) == {BLACK, AMBER}
    wide = view_of(both, city_stale_minutes=240)
    recorded.clear()
    render_city(wide, NOW)
    assert not [t for t in texts(recorded) if t.startswith("AS OF")]


def test_a_part_never_fetched_says_no_data_on_its_page(monkeypatch):
    pages = Pages(monkeypatch)
    recorded = pages.recorded
    no_weather = render_city(view_of(status(weather=None)), NOW)
    assert texts(pages[0]) == ["WEATHER", "NO DATA", "--"]
    assert len(no_weather.frames) == 2 and no_weather.frames[0].getbbox() is not None
    pages.clear()

    stuck = lines(("M", "delays", True))
    no_transit = render_city(view_of(status(lines=stuck, transit_fetched_at_utc=None)), NOW)
    assert len(no_transit.frames) == 2, "no detail pages from lines nobody fetched"
    assert texts(pages[1]) == ["TRANSIT", "NO DATA"], "and no line is called OK"
    pages.clear()
    empty = render_city(view_of(status(lines=())), NOW)
    assert "NO LINES" in texts(recorded) and len(empty.frames) == 2


def test_no_city_status_is_one_still_that_says_so(monkeypatch):
    recorded = record_text(monkeypatch)
    clip = render_city(view_of(None), NOW)
    assert len(clip.frames) == 1 and not clip.animated and device_parts(clip) == [clip]
    assert texts(recorded) == ["CITY", "WED 30", "--", "NO DATA"]
    assert clip.poster.getbbox() is not None
    assert render_city(view_of(None)).poster.tobytes() == clip.poster.tobytes(), "no clock needed"
    other_day = render_city(view_of(replace(status(), day_local="2026-09-29")), NOW)
    assert other_day.poster.tobytes() == clip.poster.tobytes(), "another day's status is none"
    hollow = render_city(view_of(CityStatus(DAY)), NOW)
    assert hollow.poster.tobytes() == clip.poster.tobytes()
    assert render_city(view_of("not a status"), NOW).poster.tobytes() == clip.poster.tobytes()
    assert hold_ms("city", clip, 6) == 6000, "a still holds the dwell"


def test_odd_values_in_a_status_never_raise():
    odd_hours = (HourWeather(99, float("nan"), 500, -7, False), "junk", HourWeather(3, 1, 2, 3))
    w = Weather(None, None, None, float("inf"), None, None, odd_hours, (None, 5), "nonsense")
    odd_lines = (LineStatus("", "tram", "", None), LineStatus("TOOLONGNAME", "bus", "delays"), 7)
    clip = render_city(view_of(CityStatus(DAY, w, odd_lines, "nonsense")), NOW)
    assert len(clip.frames) >= 2 and all(frame.getbbox() for frame in clip.frames)
    assert render_city(view_of(status()), None).durations_ms == (6000, 6000)


# the frames themselves


def all_city_clips(settings) -> list:
    clips = [render_city(view_of(status(lines=affected_five())), NOW), render_city(view_of(None))]
    for combo in COMBOS:
        view, now = load(combo, settings)
        clips.append(render_city(view, now))
    return clips


def test_every_page_is_on_true_black_with_one_header_row(settings, monkeypatch):
    recorded = record_text(monkeypatch)
    for clip in all_city_clips(settings):
        assert min(clip.durations_ms) >= 1000, "paged: each page goes to the device as a still"
        for frame in clip.frames:
            assert frame.size == (SIZE, SIZE) and frame.mode == "RGB"
            assert black_count(frame) > SIZE * SIZE // 2
            edge = [frame.getpixel((x, y)) for x in range(SIZE) for y in (0, 63)]
            edge += [frame.getpixel((x, y)) for y in range(SIZE) for x in (0, 63)]
            assert set(edge) - SKIES == {BLACK}, "nothing but the sky reaches the frame's edge"
    headers = [r for r in recorded if r[2] in (HEADER_Y - 1, HEADER_Y, HEADER_Y + 1)]
    small = [r for r in headers if r[3] == SMALL.name]
    assert small and {r[2] for r in small} == {HEADER_Y}, "no header is a pixel off its row"
    titles = [r for r in small if r[0] in ("WEATHER", "TRANSIT", "CITY") or r[0].startswith("AS")]
    assert titles and {r[1] for r in titles} == {LEFT}


def test_the_fixture_samples_load_and_are_dated_to_the_fixture(settings):
    view, now = load(WEEK_41, settings)
    city = view.city
    assert city.day_local == view.day_local == "2026-09-30"
    assert [line.line for line in city.lines] == ["N", "W", "M", "Q103", "Q66", "Q69", "B62"]
    assert [(line.line, line.status, line.now) for line in affected_lines(city)] == [
        ("M", "delays", True),
        ("W", "planned", False),
    ]
    assert (city.weather.temp_f, city.weather.code) == (57.2, 3)
    assert (degrees(city.weather.high_f), degrees(city.weather.low_f)) == ("62", "51")
    assert (
        max(hour.precip_pct for hour in city.weather.hours) == 70 and len(city.weather.hours) == 6
    )
    assert city.weather.fetched_at_utc == to_utc_iso(now - timedelta(minutes=12))
    assert city.transit_fetched_at_utc == to_utc_iso(now - timedelta(minutes=3))
    assert len(render_city(view, now).frames) == 4
    calm = fixture_city(DAYS / "x.json", "all-ok", "2026-06-14", now)
    assert not affected_lines(calm) and not calm.weather.alerts
    assert len(render_city(view_of(calm, "2026-06-14"), now).frames) == 2
    part = fixture_city(DAYS / "x.json", "weather-only", DAY, now)
    assert part.transit_fetched_at_utc is None and part.lines == ()
    without = [c for c in COMBOS if load(c, settings)[0].city is None]
    assert without, "some fixtures carry no city sample and render CITY NO DATA"
    for name in ("../days/x", "nope", "", 7):
        with pytest.raises(ValueError, match="city"):
            fixture_city(DAYS / "any.json", name, DAY, now)


def test_the_stale_limit_comes_from_settings_with_a_default(settings):
    assert city_stale_minutes(SimpleNamespace()) == 45
    assert city_stale_minutes(SimpleNamespace(city=SimpleNamespace(stale_minutes=90))) == 90
    for bad in (0, -5, "45", None, True, 2.5):
        assert city_stale_minutes(SimpleNamespace(city=SimpleNamespace(stale_minutes=bad))) == 45
    assert load(WEEK_41, settings)[0].city_stale_minutes == city_stale_minutes(settings)
    assert DayView(day_local=DAY).city_stale_minutes == 45


# the view and the rotation


def stub_store(monkeypatch, load_status) -> None:
    monkeypatch.setitem(sys.modules, "app.city.store", SimpleNamespace(load_status=load_status))


def test_the_city_is_the_requested_days_even_when_today_falls_back(db, settings, monkeypatch):
    db.execute(
        "INSERT INTO daily_metrics (day_local, metrics_json) VALUES ('2026-09-29', ?)",
        ('{"sleep_hours": 7.5, "steps": 9000}',),
    )
    asked = []

    def load_status(conn, day_local):
        asked.append(day_local)
        return replace(status(), day_local=day_local)

    stub_store(monkeypatch, load_status)
    view = view_from_db(db, settings, DAY)
    assert view.day_shown == "2026-09-29" and asked == [DAY]
    assert view.city.day_local == DAY and view.city_stale_minutes == city_stale_minutes(settings)
    assert len(render_city(view, NOW).frames) == 2


def test_a_store_that_is_missing_or_fails_is_no_city_status_and_costs_nothing_else(
    db, settings, monkeypatch
):
    nothing = render_city(view_of(None, "2026-10-02"), NOW).poster.tobytes()
    stub_store(monkeypatch, lambda conn, day: None)
    assert view_from_db(db, settings, "2026-10-02").city is None, "nothing ever fetched"
    stub_store(monkeypatch, lambda conn, day: {"day_local": day})
    assert view_from_db(db, settings, "2026-10-02").city is None, "not a CityStatus"
    errors = (sqlite3.OperationalError("no such table"), ValueError("x"), TypeError("x"))
    for error in (*errors, KeyError("x"), ImportError("x")):

        def broken(conn, day, error=error):
            raise error

        stub_store(monkeypatch, broken)
        view = view_from_db(db, settings, "2026-10-02")
        assert view.city is None and view.day_local == "2026-10-02", type(error).__name__
        assert render_city(view, NOW).poster.tobytes() == nothing
    monkeypatch.setitem(sys.modules, "app.city.store", None)
    view = view_from_db(db, settings, "2026-10-02")
    assert view.city is None, "the module not there yet"
    names = [name for name, _, _ in rotation_sequence(view, NOW, 6)]
    assert names == ["today", "city", "week", "month", "books"]


def test_city_sits_between_today_and_week_and_holds_one_pass():
    plain = view_of(status(lines=affected_five()))
    assert sequence_names(plain) == ("today", "city", "week", "month", "books")
    done = replace(plain, week_dots=3, sleep_hours=7.5)
    assert sequence_names(done)[:3] == ("today", "city", "week")
    assert sequence_names(done)[-3:] == ("win-workout", "win-sleep", "party")
    slots = {name: (clip, hold) for name, clip, hold in rotation_sequence(plain, NOW, 6)}
    clip, hold = slots["city"]
    assert hold == clip.total_ms == 27000, "every page once, never looped, whatever the dwell"
    assert hold_ms("city", clip, 60) == 27000
    parts = device_parts(clip)
    assert [part.durations_ms for part in parts] == [(6000,), (6000,), (5000,), (5000,), (5000,)]
    assert [part.frames[0].tobytes() for part in parts] == [f.tobytes() for f in clip.frames]


def test_lines_named_in_a_headline_are_marked_and_drawn_as_their_signs(monkeypatch):
    """Max, 2026-11-15: line letters and numbers in the feed's text go in pills as well."""
    marks = {
        "[F] trains are late; take the [N].": "[F] trains are late; take the [N].",
        "No W trains after 9:45 PM. Take the N instead.": (
            "No [W] trains after 9:45 PM. Take the [N] instead."
        ),
        "N, Q and R trains skip 7 Av": "[N], [Q] and [R] trains skip 7 Av",
        "A signal problem delays A trains at 36 St": "A signal problem delays [A] trains at 36 St",
        "A shuttle bus runs. Take the Q69.": "A shuttle bus runs. Take the [Q69].",
        "Plan B is to walk to Avenue C": "Plan B is to walk to Avenue C",
        # the code audit's cases, 2026-11-15: addresses, counts and times are not lines
        "between Dyre Av and E 180 St": "between Dyre Av and E 180 St",
        "7 Av or 5 Av": "7 Av or 5 Av",
        "Avenue C or Avenue D and 1 Av": "Avenue C or Avenue D and 1 Av",
        "Wait 5 or 10 minutes": "Wait 5 or 10 minutes",
        "delays of 1 and 2 hours": "delays of 1 and 2 hours",
        "between 1 and 3 AM": "between 1 and 3 AM",
        "3 of 4 trains": "3 of 4 trains",
        "Track 1 service": "Track 1 service",
        "A train was removed from service": "A train was removed from service",
        # and its misses: lists, runs, glued names, a three-letter line, a bus before "buses"
        "Take the A or C": "Take the [A] or [C]",
        "the A, C and E trains": "the [A], [C] and [E] trains",
        "the 4 5 6": "the [4] [5] [6]",
        "4/5/6 trains": "[4] [5] [6] trains",
        "N/W trains": "[N] [W] trains",
        "SIR trains": "[SIR] trains",
        "M15 buses": "[M15] buses",
        "n trains are delayed": "n trains are delayed",
        # the feed runs its own bullets together; each becomes a word of its own
        "74 St [E][F][R][7] Station": "74 St [E] [F] [R] [7] Station",
        "Manhattan-bound [N][W] skip 39 Av": "Manhattan-bound [N] [W] skip 39 Av",
    }
    for text, want in marks.items():
        assert mark_lines(text, frozenset({"Q69"})) == want
    assert mark_lines("Take the Q69", frozenset()) == "Take the Q69", "only a configured bus"
    assert city_screen.sign_color("SIR") == line_color(LineStatus("SIR", "subway"))
    assert city_screen.sign_color("Q69") == city_screen.sign_color("M15") != SUBWAY_COLORS["M"]
    real = json.loads((Path(__file__).parents[2] / "fixtures/city/real/subway.json").read_text())
    headers = [
        t["text"]
        for e in real["entity"]
        for t in e.get("alert", {}).get("header_text", {}).get("translation", [])
        if t.get("language", "en") == "en" and "][" in t.get("text", "")
    ]
    assert headers, "the real feed runs bullets together"
    for header in headers:
        for font_ in (BODY, SMALL):
            lines_ = sum(headline_pages(header, DETAIL_BOTTOM - DETAIL_TOP + 1)[2], [])
            assert "][" not in " ".join(lines_), header
            for row in wrap_headline(mark_lines(clean_summary(header)), font_, WIDTH):
                assert sum(word_width(w, font_) for w in row.split()) <= WIDTH
    run_together = LineStatus("N", "subway", "delays", headers[0], True, 1)
    page = render_city(view_of(status(lines=(run_together,))), NOW).frames[2]
    recorded = record_text(monkeypatch)
    render_city(view_of(status(lines=(run_together,))), NOW)
    assert not [r for r in recorded if "[" in r[0] or "?" in r[0]], "no bracket is ever spelled out"
    assert page.getbbox() is not None
    assert sign_width("N") == sign_width("F") == 9 and sign_width("Q69") > 9 < sign_width("SIR")
    line = LineStatus(
        "W", "subway", "suspended", "W service is suspended. Take the N or R.", True, 1
    )
    page = render_city(view_of(status(lines=(line,))), NOW).frames[2]
    body = {page.getpixel((x, y)) for x in range(SIZE) for y in range(DETAIL_TOP, SIZE)}
    assert SUBWAY_COLORS["N"] in body and BLACK in body, "yellow bullets with black letters"
    wide = "Take the " + " ".join(["N"] * 12) + " trains"
    for font in (BODY, SMALL):
        for row in wrap_headline(mark_lines(wide), font, WIDTH):
            assert sum(word_width(w, font) for w in row.split()) <= WIDTH, "signs fit the line"
    assert bullet_ink(SUBWAY_COLORS["N"]) == BLACK and bullet_ink(SUBWAY_COLORS["F"]) != BLACK
    capital_n = ("#...#", "##..#", "#.#.#", "#..##", "#...#")
    assert city_screen.BULLET_GLYPHS["N"] == capital_n, "a capital N, centred in its bullet"
    bullet = render_city(view_of(status(lines=(LineStatus("N", "subway"),))), NOW).frames[1]
    span = range(LEFT + 13)
    yellow = [x for x in span for y in range(8, 15) if bullet.getpixel((x, y)) != BLACK]
    full_rows = range(10, 13)  # where the oval spans all nine columns
    ink = [x for x in range(LEFT, LEFT + 9) for y in full_rows if bullet.getpixel((x, y)) == BLACK]
    assert (min(yellow), max(yellow)) == (LEFT, LEFT + 8), "a nine-wide bullet"
    assert (min(ink), max(ink)) == (LEFT + 2, LEFT + 6), "the N sits centred, a rim each side"
