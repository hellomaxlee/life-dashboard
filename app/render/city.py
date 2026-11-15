"""The City screen: the day's weather and the state of the lines Max rides, as paged stills.

Drawn from the view's `city` (app/city/model.py), which is always the requested day's, never
`day_shown`. Pages, in order:

- weather: the page is its sky, in the condition's tone, over a deeper floor. On the sky: the
  current temperature large, the condition drawn as the page's one picture, high and low. An
  active National Weather Service alert takes a band across the foot of the sky, in amber on
  one or two lines, and the hero above it shrinks to its compact form (small icon, high and
  low stacked). On the floor: the day in six 3-hour steps: temperature, a small condition
  icon, a rain-probability gauge and the hour ("6P", "12A"). Steps past midnight are in the
  as-of colour behind a dotted rule. An alert never takes the steps' temperatures.
- lines: one row per line: its sign in its MTA colour with the name INSIDE it (a nine-wide
  oval for a subway letter, a pill for a bus route; Max, 2026-11-15: "like true NYC subway
  artistic style", which overrides the earlier ruling that a letter on a lit disc does not
  read at 1x), and its status as a word on the status board: green "OK", amber "WORK" (with a
  clock mark when it starts later today), red "DELAYS", red "NO SVC". The word carries the
  status; the colour only repeats it. Seven rows at most; more lines end in "+N MORE".
- detail pages for up to DETAIL_PAGES lines that are delayed or suspended (planned work gets
  none), most severe first, then what is in effect now before what starts later: that
  line's row as the header, and the alert's own headline under it, whole, over as many
  pages as it needs. More such lines than DETAIL_PAGES ends the last page with "+N MORE".

All ok and no alert is the common case: the weather and lines pages and nothing else.

Numbers. Every figure is a field of the CityStatus. Temperatures are rounded half up to whole
degrees for display (56.5 is drawn as 57, -0.5 as 0). A rain gauge is floored, never rounded
up: 99 % is one pixel short of full and 1 % lights nothing.

Night. The data carries no sunrise, so a clear sky is drawn as a moon from NIGHT_FROM to
DAY_FROM by the hour alone.

What is missing is said. A part never fetched: "WEATHER NO DATA" / "TRANSIT NO DATA" on its
page. A part older than the view's `city_stale_minutes`: the page's header becomes an amber
"AS OF 2:05PM" (weekday and clock, then whole days, for older ones, as on the Today screen);
detail pages carry the same line at the bottom. A fetch time that cannot be read is "AGE
UNKNOWN", as on the Claude bar. No city status at all, or one for another day: a single still,
"CITY NO DATA" with the weekday and date. Never a blank frame.

Every page keeps the header on one row in one face (HEADER_Y, the small face, from LEFT), so
paging does not jump.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.city.model import STATUS_ORDER, CityStatus, HourWeather, LineStatus, Weather
from app.render.font import BODY, SMALL, Font, draw_text, draw_text_right, text_width
from app.render.frame import SIZE, Clip, Color, Frame, new_frame, still
from app.render.palette import (
    AMBER,
    BLACK,
    BULLET_INK,
    BUS_BLUE,
    CLOUD,
    CLOUD_DARK,
    GOLD,
    GREEN,
    LABEL,
    MOONLIGHT,
    MTA_BLUE,
    MTA_BROWN,
    MTA_GREEN,
    MTA_GREY,
    MTA_LIGHT_GREEN,
    MTA_ORANGE,
    MTA_PURPLE,
    MTA_RED,
    MTA_YELLOW,
    RAIN,
    RED,
    SECONDARY,
    TEXT,
    WHITE,
    day_accent,
    dim,
    legible_on,
)
from app.render.screens import (
    LEFT,
    RIGHT,
    band,
    clean_summary,
    draw_disc,
    fill_rect,
    page_ground,
    slot,
)
from app.render.screens import (
    _glue_numbers as glue_numbers,
)
from app.render.theme import STOCK, MonthTheme, theme_for
from app.render.view import DayView
from app.timeutil import clock_12h, from_utc_iso

WEATHER_MS = 6000
LINES_MS = 6000
DETAIL_MS = 5000
DETAIL_PAGES = 3

HEADER_Y = 2
WIDTH = RIGHT - LEFT + 1
WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
NO_DATA = "NO DATA"
AGE_UNKNOWN = "AGE UNKNOWN"
MISSING = "--"

ICON_X, ICON_Y = 38, 9
COMPACT_ICON_X = 31
STACK_YS = (10, 17)
TEMP_X, TEMP_Y = 2, 9
HIGH_LOW_Y = 25
NOTICE_Y = 31
STEPS = 6
STEP_PITCH = 10
STEP_INNER = 9
STEP_TEMP_Y = 38
STEP_ICON_Y = 44
BAR_TOP = 51
BAR_HEIGHT = 5
BAR_WIDTH = 5
STEP_LABEL_Y = 57
NIGHT_FROM, DAY_FROM = 19, 6

ROWS_TOP = 8
MAX_ROWS = 7
ROOMY_ROWS = 7
ROW_PITCH = 8
BADGE = 7
NAME_X = 11
NAME_GAP = 4
STATUS_X = 38
MARK_GAP = 2

DETAIL_TOP = 11
DETAIL_BOTTOM = 62
FOOTER_PITCH = 7
HEADLINE_FACES = ((BODY, 9), (SMALL, 7))
BODY_PAGES = 2
DETAIL_STATUSES = ("delays", "suspended")
BULLET = re.compile(r"\[([A-Za-z0-9]{1,4})\]")
ELLIPSIS = "..."
NO_DETAILS = "No details given."

CLEAR, CLEAR_NIGHT, PARTLY, OVERCAST, FOG = "clear", "clear-night", "partly", "overcast", "fog"
PARTLY_NIGHT = "partly-night"
RAINY, HEAVY_RAIN, SNOW, THUNDER, UNKNOWN = "rain", "heavy-rain", "snow", "thunder", "unknown"
CONDITIONS = (
    CLEAR,
    CLEAR_NIGHT,
    PARTLY,
    PARTLY_NIGHT,
    OVERCAST,
    FOG,
    RAINY,
    HEAVY_RAIN,
    SNOW,
    THUNDER,
    UNKNOWN,
)
CONDITION_WORDS = {
    CLEAR: "CLEAR",
    CLEAR_NIGHT: "CLEAR",
    PARTLY: "PARTLY CLOUDY",
    PARTLY_NIGHT: "PARTLY CLOUDY",
    OVERCAST: "OVERCAST",
    FOG: "FOG",
    RAINY: "RAIN",
    HEAVY_RAIN: "HEAVY RAIN",
    SNOW: "SNOW",
    THUNDER: "THUNDERSTORM",
    UNKNOWN: "",
}
_HEAVY_CODES = frozenset({55, 57, 65, 67, 82})
ALERT_SHORT = {"THUNDERSTORM": "T-STORM", "THUNDERSTORMS": "T-STORMS"}

INK: dict[str, Color] = {
    "y": GOLD,
    "w": CLOUD,
    "g": CLOUD_DARK,
    "b": RAIN,
    "s": WHITE,
    "m": MOONLIGHT,
}

_CLOUD = (
    "....www.....",
    "...wwwww.ww.",
    "..wwwwwwwwww",
    ".wwwwwwwwwww",
    "wwwwwwwwwwww",
    "gwwwwwwwwwwg",
    ".gggggggggg.",
)
_STORM_CLOUD = tuple(row.replace("w", "g") for row in _CLOUD)
_BLANK = "............"

ICONS: dict[str, tuple[str, ...]] = {
    CLEAR: (
        ".....yy.....",
        ".y........y.",
        "..y......y..",
        "....yyyy....",
        "...yyyyyy...",
        "y..yyyyyy..y",
        "y..yyyyyy..y",
        "...yyyyyy...",
        "....yyyy....",
        "..y......y..",
        ".y........y.",
        ".....yy.....",
    ),
    CLEAR_NIGHT: (
        ".....mmmm...",
        "...mmmm.....",
        "..mmm.....s.",
        ".mmm........",
        ".mmm....s...",
        ".mmm........",
        ".mmm........",
        ".mmmm......m",
        "..mmmm....mm",
        "...mmmmmmmm.",
        ".....mmmmm..",
        _BLANK,
    ),
    PARTLY: (
        "....y.......",
        ".y.....y....",
        "...yyy......",
        "..yyyyy.y...",
        "y.yyyywww...",
        "..yyywwwww..",
        "...wwwwwwwww",
        "..wwwwwwwwww",
        ".wwwwwwwwwww",
        ".gwwwwwwwwwg",
        "..ggggggggg.",
        _BLANK,
    ),
    PARTLY_NIGHT: (
        "...mmm......",
        "..mmm.......",
        ".mmm........",
        ".mmm........",
        ".mmmm.www...",
        "..mmmwwwww..",
        "...wwwwwwwww",
        "..wwwwwwwwww",
        ".wwwwwwwwwww",
        ".gwwwwwwwwwg",
        "..ggggggggg.",
        _BLANK,
    ),
    OVERCAST: (_BLANK, _BLANK, *_CLOUD, _BLANK, _BLANK, _BLANK),
    FOG: (
        _BLANK,
        "..gggggggg..",
        _BLANK,
        "wwwwwwwwww..",
        _BLANK,
        "..wwwwwwwwww",
        _BLANK,
        "gggggggg....",
        _BLANK,
        "..gggggggg..",
        _BLANK,
        _BLANK,
    ),
    RAINY: (*_CLOUD, _BLANK, "...b....b...", "..b....b....", _BLANK, _BLANK),
    HEAVY_RAIN: (
        *_STORM_CLOUD,
        _BLANK,
        ".b..b..b..b.",
        "b..b..b..b..",
        "..b..b..b..b",
        ".b..b..b..b.",
    ),
    SNOW: (*_CLOUD, _BLANK, "..s...s...s.", _BLANK, "....s...s...", _BLANK),
    THUNDER: (
        *_STORM_CLOUD,
        ".....yyy....",
        ".b..yyy...b.",
        "b..yyyyy.b..",
        ".....yy.....",
        "....yy......",
    ),
    UNKNOWN: (_BLANK,) * 5 + ("..ggg..ggg..", "..ggg..ggg..") + (_BLANK,) * 5,
}

_MINI_CLOUD = (".wwwww.", "wwwwwww", "ggggggg")
_MINI_STORM = (".ggggg.", "ggggggg", "ggggggg")
_MINI_BLANK = "......."

MINI_ICONS: dict[str, tuple[str, ...]] = {
    CLEAR: ("...y...", ".y.y.y.", "..yyy..", ".y.y.y.", "...y...", _MINI_BLANK),
    CLEAR_NIGHT: ("..mmm..", ".mm....", ".mm....", ".mmm.m.", "..mmm..", _MINI_BLANK),
    PARTLY: (".y.y...", "..yyy..", ".yywww.", "..wwwww", ".wwwwww", ".gggggg"),
    PARTLY_NIGHT: ("..mm...", ".mm....", ".mmwww.", "..wwwww", ".wwwwww", ".gggggg"),
    OVERCAST: (_MINI_BLANK, "..www..", ".wwwww.", "wwwwwww", "ggggggg", _MINI_BLANK),
    FOG: (_MINI_BLANK, ".ggggg.", _MINI_BLANK, "wwwwwww", _MINI_BLANK, ".ggggg."),
    RAINY: (*_MINI_CLOUD, _MINI_BLANK, "..b..b.", ".b..b.."),
    HEAVY_RAIN: (*_MINI_STORM, "b.b.b.b", ".b.b.b.", "b.b.b.b"),
    SNOW: (*_MINI_CLOUD, "s..s..s", _MINI_BLANK, "..s..s."),
    THUNDER: (*_MINI_STORM, "...yy..", "..yy...", "...y..."),
    UNKNOWN: (_MINI_BLANK, _MINI_BLANK, "..ggg..", _MINI_BLANK, _MINI_BLANK, _MINI_BLANK),
}

DEGREE = (".##.", "#..#", "#..#", ".##.")
SMALL_DEGREE = (".#.", "#.#", ".#.")
CLOCK = (".###.", "#.#.#", "#.###", "#...#", ".###.")

SUBWAY_COLORS: dict[str, Color] = {
    **dict.fromkeys("ACE", MTA_BLUE),
    **dict.fromkeys("BDFM", MTA_ORANGE),
    "G": MTA_LIGHT_GREEN,
    **dict.fromkeys("JZ", MTA_BROWN),
    "L": MTA_GREY,
    **dict.fromkeys("NQRW", MTA_YELLOW),
    **dict.fromkeys("123", MTA_RED),
    **dict.fromkeys("456", MTA_GREEN),
    "7": MTA_PURPLE,
    "S": MTA_GREY,
}
STATUS_WORDS = {"ok": "OK", "planned": "WORK", "delays": "DELAYS", "suspended": "NO SVC"}
STATUS_COLORS = {"ok": GREEN, "planned": AMBER, "delays": RED, "suspended": RED}
OTHER_STATUS_WORD = "ALERT"


def _finite(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def degrees(value: object) -> str | None:
    """A temperature as whole degrees, rounded half up; None for anything not a finite number."""
    return str(math.floor(value + 0.5)) if _finite(value) else None


def precip_px(percent: object, height: int = BAR_HEIGHT) -> int:
    """Gauge pixels for a rain probability, floored; out-of-range values are held to 0..100."""
    if not _finite(percent):
        return 0
    return int(min(max(percent, 0), 100) * height // 100)


def hour_label(hour: object) -> str:
    """ "12A" for midnight, "9A", "12P" for noon, "11P"; "--" for anything not an hour."""
    if isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23:
        return MISSING
    return f"{hour % 12 or 12}{'A' if hour < 12 else 'P'}"


def is_night(hour: object) -> bool:
    return isinstance(hour, int) and (hour >= NIGHT_FROM or hour < DAY_FROM)


def condition(code: object, hour: object = None) -> str:
    """The icon group for a WMO weather code; UNKNOWN for a code outside the table."""
    if isinstance(code, bool) or not isinstance(code, int):
        return UNKNOWN
    if code == 0:
        return CLEAR_NIGHT if is_night(hour) else CLEAR
    if code in (1, 2):
        return PARTLY_NIGHT if is_night(hour) else PARTLY
    if code == 3:
        return OVERCAST
    if code in (45, 48):
        return FOG
    if 51 <= code <= 67 or 80 <= code <= 82:
        return HEAVY_RAIN if code in _HEAVY_CODES else RAINY
    if 71 <= code <= 77 or code in (85, 86):
        return SNOW
    if 95 <= code <= 99:
        return THUNDER
    return UNKNOWN


def draw_icon(frame: Frame, x: int, y: int, rows: tuple[str, ...], scale: int = 1) -> None:
    """An icon from its ink rows, each cell a `scale` by `scale` block."""
    pixels = frame.load()
    for row_index, row in enumerate(rows):
        for col_index, cell in enumerate(row):
            if cell not in INK:
                continue
            for dy in range(scale):
                for dx in range(scale):
                    px, py = x + col_index * scale + dx, y + row_index * scale + dy
                    if 0 <= px < SIZE and 0 <= py < SIZE:
                        pixels[px, py] = INK[cell]


def _draw_mark(frame: Frame, x: int, y: int, rows: tuple[str, ...], color: Color) -> None:
    pixels = frame.load()
    for row_index, row in enumerate(rows):
        for col_index, cell in enumerate(row):
            px, py = x + col_index, y + row_index
            if cell == "#" and 0 <= px < SIZE and 0 <= py < SIZE:
                pixels[px, py] = color


def ellipsize(text: str, width: int, font: Font = SMALL) -> str:
    """The text if it fits `width` pixels, else its longest whole-glyph start ending "..."."""
    text = " ".join(text.split())
    if text_width(text, font) <= width:
        return text
    while text and text_width(text + ELLIPSIS, font) > width:
        text = text[:-1].rstrip()
    return text + ELLIPSIS


def day_stamp(day_local: str) -> str:
    day = date.fromisoformat(day_local)
    return f"{WEEKDAYS[day.weekday()]} {day.day}"


def as_of_label(
    fetched_at_utc: str | None, now: datetime | None, home_tz: str, stale_minutes: int
) -> str | None:
    """None while the fetch is within `stale_minutes` of `now` (or `now` is not given); else
    "AS OF 14:05" for one made today, "AS OF SAT 14:05" up to six days back, "AS OF 8D AGO"
    beyond, and "AGE UNKNOWN" when the fetch time cannot be read."""
    if now is None:
        return None
    try:
        moment = from_utc_iso(fetched_at_utc)
    except (TypeError, ValueError):
        return AGE_UNKNOWN
    if (now - moment).total_seconds() <= stale_minutes * 60:
        return None
    zone = ZoneInfo(home_tz)
    local = moment.astimezone(zone)
    days_back = (now.astimezone(zone).date() - local.date()).days
    if days_back <= 0:
        return f"AS OF {clock_12h(local)}"
    if days_back <= 6:
        return f"AS OF {WEEKDAYS[local.weekday()]} {clock_12h(local, compact=True)}"
    return f"AS OF {days_back}D AGO"


def _header(
    frame: Frame, title: str, right: str, stale: str | None = None, accent: Color = LABEL
) -> None:
    """The one header row every city page shares; a stale page's row is its amber as-of, on a
    band a shade deeper than the page so the amber is drawn as itself. The title wears
    `accent`: the weekday's colour on a page that has data."""
    if stale is not None:
        ground = frame.getpixel((0, 0))
        fill_rect(frame, 0, 0, SIZE - 1, HEADER_Y + SMALL.height + 1, band(ground))
        draw_text(frame, LEFT, HEADER_Y, stale, AMBER, SMALL, exact=True)
        return
    draw_text(frame, LEFT, HEADER_Y, title, accent, SMALL)
    draw_text_right(frame, RIGHT, HEADER_Y, right, TEXT, SMALL)


# the weather page


NARROW_ONE = (".#", "##", ".#", ".#", ".#")


def _pieces_width(pieces: list[tuple[str, int]]) -> int:
    return sum(width for _, width in pieces) + len(pieces) - 1


def _narrowed(text: str) -> list[tuple[str, int]]:
    """Every "1" as the two-pixel NARROW_ONE and a leading minus as two pixels."""
    pieces: list[tuple[str, int]] = []
    run = ""
    for index, char in enumerate(text):
        if char == "1" or (char == "-" and index == 0):
            if run:
                pieces.append((run, text_width(run)))
                run = ""
            pieces.append((char, 2))
        else:
            run += char
    if run:
        pieces.append((run, text_width(run)))
    return pieces


def _compact(text: str) -> tuple[list[tuple[str, int]], int] | None:
    """A step's label as (piece, width) runs no wider than a step: as drawn when it fits; else
    with every "1" two pixels wide, so "11A" keeps two ones that match (Max, 2026-10-09: a
    stroke beside a full 1 read as "I1"); else with a leading "1" as a one-pixel stroke
    ("12A", "100"); a minus is two pixels in both. None if still too wide."""
    if text_width(text) <= STEP_INNER:
        return [(text, text_width(text))], text_width(text)
    pieces = _narrowed(text)
    if _pieces_width(pieces) <= STEP_INNER:
        return pieces, _pieces_width(pieces)
    pieces = []
    rest = text
    if rest.startswith("-"):
        pieces.append(("-", 2))
        rest = rest[1:]
    if rest.startswith("1"):
        pieces.append(("1", 1))
        rest = rest[1:]
    if rest:
        pieces.append((rest, text_width(rest)))
    width = _pieces_width(pieces)
    return (pieces, width) if width <= STEP_INNER else None


def _draw_compact(frame: Frame, x0: int, y: int, text: str, color: Color) -> bool:
    """Centre a label in the step that starts at `x0`. False, drawing nothing, if it cannot fit."""
    fitted = _compact(text)
    if fitted is None:
        return False
    pieces, width = fitted
    x = x0 + (STEP_INNER - width) // 2
    for piece, piece_width in pieces:
        if piece == "-" and piece_width == 2:
            fill_rect(frame, x, y + 2, x + 1, y + 2, color)
        elif piece == "1" and piece_width == 1:
            fill_rect(frame, x, y, x, y + SMALL.height - 1, color)
        elif piece == "1" and piece_width == 2:
            _draw_mark(frame, x, y, NARROW_ONE, color)
        else:
            draw_text(frame, x, y, piece, color, SMALL)
        x += piece_width + 1
    return True


@dataclass(frozen=True)
class Step:
    """One column of the day strip, as it is drawn."""

    x: int
    label: str
    temp: str | None
    fill_px: int
    kind: str
    tomorrow: bool


def strip_steps(weather: Weather) -> list[Step]:
    """The first STEPS forecast steps, left to right, in the order given."""
    hours = [h for h in weather.hours if isinstance(h, HourWeather)][:STEPS]
    return [
        Step(
            x=LEFT + index * STEP_PITCH,
            label=hour_label(hour.hour_local),
            temp=degrees(hour.temp_f),
            fill_px=precip_px(hour.precip_pct),
            kind=condition(hour.code, hour.hour_local),
            tomorrow=bool(hour.tomorrow),
        )
        for index, hour in enumerate(hours)
    ]


def bar_box(step: Step) -> tuple[int, int, int, int]:
    """(x0, y0, x1, y1) of a step's rain gauge, inclusive."""
    x0 = step.x + (STEP_INNER - BAR_WIDTH) // 2
    return x0, BAR_TOP, x0 + BAR_WIDTH - 1, BAR_TOP + BAR_HEIGHT - 1


def _draw_strip(
    frame: Frame,
    steps: list[Step],
    temps: bool,
    theme: MonthTheme = STOCK,
    ground: Color = BLACK,
) -> None:
    pixels = frame.load()
    was_tomorrow = False
    later = legible_on(SECONDARY, ground)
    for index, step in enumerate(steps):
        ink = later if step.tomorrow else TEXT
        if step.tomorrow and not was_tomorrow and index:
            for y in range(STEP_ICON_Y, BAR_TOP + BAR_HEIGHT, 2):
                pixels[step.x - 1, y] = theme.ring if ground == BLACK else later
        was_tomorrow = step.tomorrow
        if temps and step.temp is not None:
            _draw_compact(frame, step.x, STEP_TEMP_Y, step.temp, ink)
        draw_icon(frame, step.x + 1, STEP_ICON_Y, MINI_ICONS[step.kind])
        x0, y0, x1, y1 = bar_box(step)
        fill_rect(frame, x0, y0, x1, y1, slot(ground, theme.track))
        if step.fill_px:
            fill_rect(frame, x0, y1 - step.fill_px + 1, x1, y1, RAIN)
        _draw_compact(frame, step.x, STEP_LABEL_Y, step.label, ink)


def alert_lines(event: str) -> list[str]:
    """An alert's event name in capitals on at most two small lines, long words shortened
    ("T-STORM"); what still does not fit ends in "..."."""
    cleaned = "".join(ch if ch.upper() in SMALL.glyphs else " " for ch in str(event).upper())
    words = [ALERT_SHORT.get(word, word) for word in cleaned.split()]
    lines: list[str] = []
    for word in words:
        candidate = f"{lines[-1]} {word}" if lines else word
        if lines and text_width(candidate) <= WIDTH:
            lines[-1] = candidate
        else:
            lines.append(word)
    if len(lines) > 2:
        lines = [lines[0], " ".join(lines[1:])]
    return [ellipsize(line, WIDTH) for line in lines]


def _draw_temperature(frame: Frame, text: str, room_right: int) -> None:
    """The hero number and its degree mark: double size, or single if that would reach the
    high and low."""
    big = text_width(text, BODY, 2) + 1 + len(DEGREE[0])
    if TEMP_X + big - 1 < room_right:
        after = draw_text(frame, TEMP_X, TEMP_Y, text, WHITE, BODY, scale=2)
        _draw_mark(frame, after - 1, TEMP_Y, DEGREE, WHITE)
        return
    after = draw_text(frame, TEMP_X, TEMP_Y + 4, text, WHITE, BODY)
    _draw_mark(frame, after, TEMP_Y + 4, SMALL_DEGREE, WHITE)


def _draw_high_low(frame: Frame, weather: Weather, label: Color = LABEL) -> None:
    """ "H 62  L 51" on one line under the temperature."""
    x = LEFT
    for letter, value in (("H", weather.high_f), ("L", weather.low_f)):
        after = draw_text(frame, x, HIGH_LOW_Y, letter, label, SMALL)
        after = draw_text(frame, after + 1, HIGH_LOW_Y, degrees(value) or MISSING, TEXT, SMALL)
        x = after + 3


def _draw_high_low_stacked(frame: Frame, weather: Weather, label: Color = LABEL) -> None:
    """ "H 62" over "L 51" at the right edge: the compact hero's form, beside the small icon."""
    values = [degrees(weather.high_f) or MISSING, degrees(weather.low_f) or MISSING]
    letter_x = RIGHT + 1 - max(text_width(v) for v in values) - 3 - text_width("H")
    for letter, value, y in zip("HL", values, STACK_YS, strict=True):
        draw_text(frame, letter_x, y, letter, label, SMALL)
        draw_text_right(frame, RIGHT, y, value, TEXT, SMALL)


def _local_hour(now: datetime | None, home_tz: str) -> int | None:
    return None if now is None else now.astimezone(ZoneInfo(home_tz)).hour


FLOOR_TOP = 37
FLOOR_SHADE = 0.5
NIGHT_SKY = 0.5
OPEN_NIGHT: Color = (0, 8, 36)
# The weather page's ground is its sky, by condition, edge to edge; under the hero band it
# deepens to a floor the hour-by-hour strip stands on. One channel at or near zero so the
# panel keeps each a colour. None is an open sky: OPEN_NIGHT, with stars at night.
SKY_TONES: dict[str, Color | None] = {
    CLEAR: (0, 60, 150),
    CLEAR_NIGHT: None,
    PARTLY: (0, 54, 130),
    PARTLY_NIGHT: None,
    OVERCAST: (38, 46, 66),
    FOG: (50, 54, 62),
    RAINY: (0, 30, 110),
    HEAVY_RAIN: (18, 0, 96),
    SNOW: (36, 66, 104),
    THUNDER: (44, 0, 90),
    UNKNOWN: None,
}
DROP: Color = (150, 200, 255)


def weather_grounds(kind: str, night: bool) -> tuple[Color, Color]:
    """(sky, floor) for a condition and hour: the page's two tones, the floor a deeper shade
    of the sky. Night halves the sky; a clear or partly clear night is the open night."""
    tone = SKY_TONES.get(kind)
    if tone is None:
        tone = OPEN_NIGHT
    elif night:
        tone = dim(tone, NIGHT_SKY)
    return tone, dim(tone, FLOOR_SHADE)


def _cloud(frame: Frame, cx: int, cy: int, body: Color, shade: Color) -> None:
    """A cloud about 20 wide and 11 tall centred on (cx, cy): three overlapping discs on a
    flat base, its underside one row of `shade`."""
    for dx, dy, radius in ((-6, 1, 3), (-1, -2, 5), (5, 0, 4)):
        draw_disc(frame, cx + dx, cy + dy, radius, body)
    fill_rect(frame, cx - 8, cy + 1, cx + 8, cy + 4, body)
    fill_rect(frame, cx - 7, cy + 5, cx + 7, cy + 5, shade)


def _sun(frame: Frame, cx: int, cy: int, radius: int) -> None:
    """A sun: a gold disc and eight short rays set one pixel off it."""
    draw_disc(frame, cx, cy, radius, GOLD)
    reach = radius + 2
    for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
        for step in (0, 1):
            frame.putpixel((cx + dx * (reach + step), cy + dy * (reach + step)), GOLD)
    slant = round(reach * 0.72)
    for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        frame.putpixel((cx + dx * slant, cy + dy * slant), GOLD)
        frame.putpixel((cx + dx * (slant + 1), cy + dy * (slant + 1)), GOLD)


def draw_hero_icon(frame: Frame, kind: str, sky: Color) -> None:
    """The condition as the page's one picture, drawn at the panel's own resolution in the
    box ICON_X..RIGHT, ICON_Y..ICON_Y+23 (a doubled small icon looked blocky; Max,
    2026-11-15: "the sun also kinda looks weird")."""
    cx, cy = ICON_X + 11, ICON_Y + 11
    if kind == CLEAR:
        _sun(frame, cx, cy, 6)
    elif kind == CLEAR_NIGHT:
        draw_disc(frame, cx, cy, 8, MOONLIGHT)
        draw_disc(frame, cx + 4, cy - 3, 7, sky)
    elif kind == PARTLY:
        _sun(frame, cx + 4, cy - 4, 4)
        _cloud(frame, cx - 1, cy + 4, CLOUD, CLOUD_DARK)
    elif kind == PARTLY_NIGHT:
        draw_disc(frame, cx + 4, cy - 4, 5, MOONLIGHT)
        draw_disc(frame, cx + 7, cy - 6, 4, sky)
        _cloud(frame, cx - 1, cy + 4, CLOUD, CLOUD_DARK)
    elif kind == FOG:
        for row, (x0, x1) in enumerate(((-9, 5), (-5, 9), (-9, 3), (-3, 9))):
            fill_rect(frame, cx + x0, cy - 7 + row * 4, cx + x1, cy - 6 + row * 4, CLOUD)
    else:
        dark = kind in (HEAVY_RAIN, THUNDER, UNKNOWN)
        if kind == OVERCAST:
            _cloud(frame, cx + 2, cy - 4, CLOUD_DARK, CLOUD_DARK)
        _cloud(
            frame, cx, cy if kind == OVERCAST else cy - 3, CLOUD_DARK if dark else CLOUD, CLOUD_DARK
        )
        if kind in (RAINY, HEAVY_RAIN):
            drops = (-6, -2, 2, 6) if kind == HEAVY_RAIN else (-5, 0, 5)
            for dx in drops:
                for step in range(3 if kind == HEAVY_RAIN else 2):
                    frame.putpixel((cx + dx - step, cy + 5 + step * 2), DROP)
                    frame.putpixel((cx + dx - step, cy + 6 + step * 2), DROP)
        elif kind == SNOW:
            for dx, dy in ((-6, 6), (0, 8), (6, 6), (-3, 10), (3, 10)):
                frame.putpixel((cx + dx, cy + dy), WHITE)
        elif kind == THUNDER:
            for dx, dy in (
                (1, 4),
                (0, 5),
                (-1, 6),
                (0, 6),
                (1, 6),
                (2, 6),
                (1, 7),
                (0, 8),
                (-1, 9),
            ):
                frame.putpixel((cx + dx, cy + dy), GOLD)


def _weather_frame(view: DayView, city: CityStatus, now: datetime | None) -> Frame:
    weather = city.weather
    if not isinstance(weather, Weather):
        frame = new_frame(page_ground(view))
        _header(frame, "WEATHER", NO_DATA, accent=day_accent(view.day_local))
        draw_text(frame, TEMP_X, TEMP_Y, MISSING, TEXT, BODY, scale=2)
        return frame
    hour = _local_hour(now, view.home_tz)
    kind = condition(weather.code, hour)
    night = is_night(hour)
    sky, floor = weather_grounds(kind, night)
    frame = new_frame(sky)
    if floor != sky:
        fill_rect(frame, 0, FLOOR_TOP, SIZE - 1, SIZE - 1, floor)
    stale = as_of_label(weather.fetched_at_utc, now, view.home_tz, view.city_stale_minutes)
    accent = legible_on(day_accent(view.day_local), sky)
    _header(frame, "WEATHER", day_stamp(view.day_local), stale, accent)
    alerts = [a for a in weather.alerts if isinstance(a, str) and a.strip()]
    notice = alert_lines(alerts[0]) if alerts else []
    label = legible_on(LABEL, sky)
    temp = degrees(weather.temp_f) or MISSING
    if notice:
        # an alert is a band across the foot of the sky, a shade deeper so its amber is
        # drawn as itself; the hero above it shrinks to the compact form to stay clear
        rows = (HIGH_LOW_Y, NOTICE_Y)[2 - len(notice) :]
        fill_rect(frame, 0, rows[0] - 2, SIZE - 1, FLOOR_TOP - 1, band(sky))
        draw_icon(frame, COMPACT_ICON_X, ICON_Y + 1, ICONS[kind])
        _draw_temperature(frame, temp, COMPACT_ICON_X - 2)
        _draw_high_low_stacked(frame, weather, label)
        for line, y in zip(notice, rows, strict=True):
            draw_text(frame, LEFT, y, line, AMBER, SMALL, exact=True)
    else:
        draw_hero_icon(frame, kind, sky)
        _draw_temperature(frame, temp, ICON_X - 2)
        _draw_high_low(frame, weather, label)
    _draw_strip(frame, strip_steps(weather), True, theme_for(view), floor)
    return frame


# the lines page


def line_color(line: LineStatus) -> Color:
    """The badge's fill: the MTA colour for a subway letter, the bus blue for a bus; a
    subway name the table does not know is the shuttle grey."""
    if line.kind == "bus":
        return BUS_BLUE
    name = str(line.line).upper()
    return SUBWAY_COLORS.get(name) or SUBWAY_COLORS.get(name[:1], MTA_GREY)


BULLET_W = 9


def badge_box(y: int, tight: bool = False) -> tuple[int, int, int, int]:
    """(x0, y0, x1, y1) of a subway bullet for a row at `y`, inclusive. `tight` is accepted
    and ignored: there is one row form (the five-pixel form had no room for a letter)."""
    return LEFT, y, LEFT + BULLET_W - 1, y + BADGE - 1


def status_word(line: LineStatus) -> str:
    return STATUS_WORDS.get(line.status, OTHER_STATUS_WORD)


def status_color(line: LineStatus) -> Color:
    return STATUS_COLORS.get(line.status, AMBER)


def starts_later(line: LineStatus) -> bool:
    return line.status != "ok" and not line.now


def _pill(frame: Frame, x0: int, y: int, x1: int, color: Color) -> None:
    """A rounded bar seven rows tall: its four corner pixels stay whatever the page is."""
    corners = [(x, cy) for x in (x0, x1) for cy in (y, y + BADGE - 1)]
    corners = [(x, cy) for x, cy in corners if 0 <= x < SIZE and 0 <= cy < SIZE]
    under = [frame.getpixel(at) for at in corners]
    fill_rect(frame, x0, y, x1, y + BADGE - 1, color)
    for at, was in zip(corners, under, strict=True):
        frame.putpixel(at, was)


BULLET_STATUS_X = 24
PILL_PAD = 2
# A subway bullet is an oval nine wide and seven tall: wide enough for a five-column capital
# with a clear rim all round (in a seven-wide disc N sat off centre and W read as H; display
# audit, 2026-11-15). M and W are the small face's own five-column forms; N gets one too, so
# all three sit centred.
BULLET_ROWS = ((2, 6), (1, 7), (0, 8), (0, 8), (0, 8), (1, 7), (2, 6))
BULLET_GLYPHS: dict[str, tuple[str, ...]] = {
    "N": ("#...#", "##..#", "#.#.#", "#..##", "#...#"),
}


def _oval(frame: Frame, x: int, y: int, color: Color) -> None:
    for row, (first, last) in enumerate(BULLET_ROWS):
        fill_rect(frame, x + first, y + row, x + last, y + row, color)


def draw_bullet_name(frame: Frame, x: int, y: int, name: str, ink: Color) -> None:
    """A one-letter line name centred in the bullet whose left column is `x`."""
    if name in BULLET_GLYPHS:
        rows = BULLET_GLYPHS[name]
        _draw_mark(frame, x + (BULLET_W - len(rows[0])) // 2, y + 1, rows, ink)
        return
    at = x + (BULLET_W - text_width(name, SMALL)) // 2
    draw_text(frame, at, y + 1, name, ink, SMALL, exact=True)


def fits_a_bullet(name: str) -> bool:
    return len(name) == 1 and text_width(name, SMALL) <= BULLET_W - 4


def bullet_ink(color: Color) -> Color:
    """The letter's colour inside a bullet, as the MTA prints it: black on the yellow lines,
    white on every other."""
    return BULLET_INK if color == MTA_YELLOW else WHITE


def draw_bullet(frame: Frame, y: int, line: LineStatus) -> int:
    """The line as its own sign, the name INSIDE it (Max, 2026-11-15: "like true NYC subway
    artistic style"): a subway letter in an oval of the line's colour, a bus route (or a
    subway name longer than a letter) in a pill. Seven rows tall from `y`. Returns the x of
    its last column."""
    name = str(line.line).upper()
    color = line_color(line)
    ink = bullet_ink(color)
    if line.kind != "bus" and fits_a_bullet(name):
        _oval(frame, LEFT, y, color)
        draw_bullet_name(frame, LEFT, y, name, ink)
        return LEFT + BULLET_W - 1
    right = min(LEFT + text_width(name, SMALL) + 2 * PILL_PAD - 1, RIGHT)
    _pill(frame, LEFT, y, right, color)
    shown = ellipsize(name, right - LEFT + 1 - 2 * PILL_PAD)
    draw_text(frame, LEFT + PILL_PAD, y + 1, shown, ink, SMALL, exact=True)
    return right


BOARD_X = BULLET_STATUS_X - 2


def draw_board(frame: Frame) -> None:
    """The lines page's status board: the right-hand column a shade deeper than the page,
    from under the header to the foot. The status words sit on it, so green, amber and red
    are drawn as themselves (a red lightened to read on the page drifted toward coral; code
    audit, 2026-11-15)."""
    ground = frame.getpixel((0, 0))
    fill_rect(frame, BOARD_X, ROWS_TOP - 1, SIZE - 1, SIZE - 1, band(ground))


def draw_row(frame: Frame, y: int, line: LineStatus, tight: bool = False) -> None:
    """One line: its bullet with the name inside (`draw_bullet`), then its status as a word
    in the status colour, exactly, with a clock mark when the alert starts later today. The
    word starts at the board's column, later if a long pill needs the room, and is pulled
    left again so it (and its mark) end inside the right margin. `y` is the row's top;
    `tight` is accepted and ignored."""
    first = draw_bullet(frame, y, line) + NAME_GAP
    status_y = y + 1
    word, color = status_word(line), status_color(line)
    clock = starts_later(line)
    width = text_width(word) + (MARK_GAP + len(CLOCK[0]) if clock else 0)
    x = max(first, min(BULLET_STATUS_X, RIGHT + 1 - width))
    if x + width - 1 > RIGHT and clock:
        clock, width = False, text_width(word)
    x = max(first, min(x, RIGHT + 1 - width))
    after = draw_text(frame, x, status_y, word, color, SMALL, exact=True)
    if clock:
        _draw_mark(frame, after - 1 + MARK_GAP, status_y, CLOCK, color)


def line_rows(count: int) -> tuple[bool, list[int]]:
    """(False, the top y of each row): at most ROOMY_ROWS seven-pixel rows. There is no
    tighter form; more lines than rows end in a count (`_lines_frame`)."""
    shown = min(count, ROOMY_ROWS)
    return False, [ROWS_TOP + index * ROW_PITCH for index in range(shown)]


def _lines_frame(view: DayView, city: CityStatus, now: datetime | None) -> Frame:
    frame = new_frame(page_ground(view))
    lines = [line for line in city.lines if isinstance(line, LineStatus)]
    if city.transit_fetched_at_utc is None:
        _header(frame, "TRANSIT", NO_DATA, accent=day_accent(view.day_local))
        return frame
    stale = as_of_label(city.transit_fetched_at_utc, now, view.home_tz, view.city_stale_minutes)
    _header(frame, "TRANSIT", day_stamp(view.day_local), stale, day_accent(view.day_local))
    if not lines:
        draw_text(frame, LEFT, ROWS_TOP + 1, "NO LINES", TEXT, SMALL)
        return frame
    draw_board(frame)
    _, rows = line_rows(len(lines))
    hidden = len(lines) - len(rows)
    if hidden > 0:
        rows = rows[:-1]
    for line, y in zip(lines, rows, strict=False):
        draw_row(frame, y, line)
    if hidden > 0:
        y = ROWS_TOP + (ROOMY_ROWS - 1) * ROW_PITCH + 1
        draw_text(frame, LEFT, y, f"+{hidden + 1} MORE", SECONDARY, SMALL)
    return frame


# the detail pages


def severity(line: LineStatus) -> int:
    """The status's place in STATUS_ORDER; a status the model does not name ranks as planned."""
    return STATUS_ORDER.index(line.status) if line.status in STATUS_ORDER else 1


def affected_lines(city: CityStatus) -> list[LineStatus]:
    """Lines that are not ok: most severe first, then in effect now before later, then as
    configured."""
    if city.transit_fetched_at_utc is None:
        return []
    lines = [line for line in city.lines if isinstance(line, LineStatus) and line.status != "ok"]
    return sorted(lines, key=lambda line: (-severity(line), not line.now))


def detail_lines(city: CityStatus) -> list[LineStatus]:
    """The affected lines that get detail pages: delays and suspensions only. Planned work
    shows as WORK on the lines page and has no page of its own (Max, 2026-10-05)."""
    return [line for line in affected_lines(city) if line.status in DETAIL_STATUSES]


LINE_NAMES = frozenset("1234567ABCDEFGJLMNQRSWZ") | {"SIR"}
_NOUNS = frozenset({"train", "trains", "service", "line", "lines", "shuttle"})
_JOINERS = frozenset({"or", "and"})
# A capital or a digit before or after one of these is an address, a count or a time, not a
# line: "Avenue C", "Track 1", "3 of 4", "7 Av", "E 180 St", "5 minutes", "3 AM".
_NOT_A_LINE_AFTER = frozenset({"avenue", "av", "ave", "track", "tracks", "platform", "exit"})
_NOT_A_LINE_AFTER |= frozenset({"plan", "of", "gate", "level", "lot", "terminal", "pier"})
_NOT_A_LINE_BEFORE = frozenset({"av", "ave", "avenue", "st", "street", "pl", "rd", "blvd"})
_NOT_A_LINE_BEFORE |= frozenset({"am", "pm", "min", "mins", "minute", "minutes", "hour", "hours"})
_NOT_A_LINE_BEFORE |= frozenset({"pct", "percent", "stops", "stop", "cars", "car", "days", "day"})
_BUS_ROUTE = re.compile(r"^(?:BXM|BX|BM|QM|SIM|X|[BMQS])\d{1,3}[A-Z]?(?:-SBS)?$")
_EDGES = ".,;:!?()\"'"
_MARKED = re.compile(r"^(\W*)\[([A-Za-z0-9]{1,4})\](\W*)$")
_ADJACENT = re.compile(r"\]\s*\[")
_GLUED = re.compile(r"^[A-Z0-9]{1,3}(?:[/,][A-Z0-9]{1,3})+$")


def _core(word: str) -> str:
    return word.strip(_EDGES)


def _line_parts(core: str) -> list[str]:
    """The line names a word stands for: itself, or the members of "4/5/6" and "N,W"; an
    empty list when it is not made of line names only."""
    parts = re.split(r"[/,]", core) if _GLUED.match(core) else [core]
    return parts if all(part in LINE_NAMES for part in parts) else []


def mark_lines(text: str, buses: frozenset[str] = frozenset()) -> str:
    """The headline with every line it names written as the feed's own bullet, "[N]", each
    its own word, so it can be drawn as a sign (Max, 2026-11-15). The feed brackets some
    lines and not others, and runs brackets together ("[E][F][R]"): those are split apart.

    A bare capital or digit is taken for a line only where the sentence says so. Words are
    read in runs of line names joined by "or", "and", commas, slashes or nothing ("the A, C
    and E trains", "the 4 5 6", "N/W trains"). A run is a list of lines when it is followed
    by "train(s)", "service", "line(s)" or "shuttle", or follows "the"; a run of two or more
    letters is one without either ("Take the N or R instead"). It never is next to a word
    that makes it an address, a count or a time ("Avenue C", "E 180 St", "7 Av", "Track 1",
    "3 of 4 trains", "5 or 10 minutes"). A lone "A" is the article unless "trains" follows.
    A configured bus route is marked wherever it stands, and any route-shaped word before
    "bus" or "buses"."""
    words = _ADJACENT.sub("] [", text).split()
    cores = [_core(word) for word in words]
    lower = [core.lower() for core in cores]
    parts = [_line_parts(core) for core in cores]
    named = [False] * len(words)
    index = 0
    while index < len(words):
        if not parts[index]:
            index += 1
            continue
        end = index
        while True:
            after_joiner = end + 2 < len(words) and lower[end + 1] in _JOINERS and parts[end + 2]
            if end + 1 < len(words) and parts[end + 1]:
                end += 1
            elif after_joiner:
                end += 2
            else:
                break
        members = [k for k in range(index, end + 1) if parts[k]]
        before = lower[index - 1] if index else ""
        after = lower[end + 1] if end + 1 < len(words) else ""
        names = [name for k in members for name in parts[k]]
        blocked = (
            before in _NOT_A_LINE_AFTER
            or after in _NOT_A_LINE_BEFORE
            or (after[:1].isdigit() and not _line_parts(cores[end + 1]))
        )
        said = after in _NOUNS or before == "the"
        letters = all(not name.isdigit() for name in names)
        if names == ["A"]:
            is_list = after == "trains"
        elif len(names) > 1 and letters:
            is_list = True
        else:
            is_list = said
        if is_list and not blocked:
            for k in members:
                named[k] = True
        index = end + 1
    out = []
    for position, (word, core) in enumerate(zip(words, cores, strict=True)):
        following = lower[position + 1] if position + 1 < len(words) else ""
        bus = core.upper() in buses or (
            bool(_BUS_ROUTE.match(core.upper())) and following.startswith("bus")
        )
        if core and (named[position] or bus):
            at = word.index(core)
            signs = " ".join(f"[{name}]" for name in (parts[position] or [core]))
            word = f"{word[:at]}{signs}{word[at + len(core) :]}"
        out.append(word)
    return " ".join(out)


def sign_color(name: str) -> Color:
    """A sign's fill in running text: the bus blue for a route-shaped name, else the line's
    own colour as the lines page draws it (a subway name the table lacks is the grey)."""
    name = name.upper()
    if name not in SUBWAY_COLORS and _BUS_ROUTE.match(name):
        return BUS_BLUE
    return SUBWAY_COLORS.get(name) or SUBWAY_COLORS.get(name[:1], MTA_GREY)


def sign_width(name: str) -> int:
    """Pixels a line's sign takes in running text: a disc for a letter, a pill for a route."""
    name = name.upper()
    return BULLET_W if fits_a_bullet(name) else text_width(name, SMALL) + 2 * PILL_PAD


GLUE = "\u00a0"


def word_width(word: str, font: Font) -> int:
    marked = _MARKED.match(word)
    if marked is None:
        return text_width(word.replace(GLUE, " "), font)
    lead, name, tail = marked.groups()
    extra = sum(text_width(part, font) + font.spacing for part in (lead, tail) if part)
    return sign_width(name) + extra


def _soften(text: str, font: Font, width: int) -> str:
    """Let a word too wide for a line break after its hyphens and slashes
    ("Astoria-Ditmars") instead of mid-syllable. A marked line ("[F]") is left whole."""
    words = []
    for word in text.split():
        if _MARKED.match(word) is None and text_width(word, font) > width:
            word = word.replace("-", "- ").replace("/", "/ ").strip()
        words.append(word)
    return " ".join(words)


def wrap_headline(text: str, font: Font, width: int) -> list[str]:
    """Greedy wrap by drawn width, a line's sign counted at its own width; a plain word wider
    than a line is cut to fit."""
    gap = text_width("a a", font) - 2 * text_width("a", font)
    lines: list[str] = []
    current, used = "", 0
    for word in glue_numbers(text.split(" ")):
        size = word_width(word, font)
        if current and used + gap + size <= width:
            current, used = f"{current} {word}", used + gap + size
            continue
        if current:
            lines.append(current)
        while _MARKED.match(word) is None and text_width(word, font) > width:
            cut = len(word) - 1
            while cut > 1 and text_width(word[:cut], font) > width:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        current, used = word, word_width(word, font)
    if current:
        lines.append(current)
    return [line.replace(GLUE, " ") for line in lines]


def draw_sign(frame: Frame, x: int, y: int, name: str) -> int:
    """A line's sign in running text, seven rows tall from `y`. Returns its last column."""
    name = name.upper()
    color = sign_color(name)
    ink = bullet_ink(color)
    if fits_a_bullet(name):
        _oval(frame, x, y, color)
        draw_bullet_name(frame, x, y, name, ink)
        return x + BULLET_W - 1
    right = x + sign_width(name) - 1
    _pill(frame, x, y, right, color)
    draw_text(frame, x + PILL_PAD, y + 1, name, ink, SMALL, exact=True)
    return right


def draw_headline_line(frame: Frame, x: int, y: int, line: str, font: Font) -> None:
    """One wrapped line, word by word, each marked line as its sign."""
    gap = text_width("a a", font) - 2 * text_width("a", font)
    sign_y = y if font is BODY else y - 1
    for word in line.split():
        marked = _MARKED.match(word)
        if marked is None:
            x = draw_text(frame, x, y, word, TEXT, font) - font.spacing + gap
            continue
        lead, name, tail = marked.groups()
        if lead:
            x = draw_text(frame, x, y, lead, TEXT, font)
        x = draw_sign(frame, x, sign_y, name) + 1 + font.spacing
        if tail:
            x = draw_text(frame, x, y, tail, TEXT, font)
        x += gap - font.spacing


def headline_pages(
    headline: str, room: int, buses: frozenset[str] = frozenset()
) -> tuple[Font, int, list[list[str]]]:
    """(face, line pitch, pages of lines) for a whole headline in `room` pixel rows a page.
    Nothing is cut: the text runs over as many pages as it needs, in the body face while that
    takes at most BODY_PAGES pages, else in the small face. Lines it names are marked
    (`mark_lines`) and wrapped at their signs' width."""
    text = mark_lines(clean_summary(headline), buses)
    for font, pitch in HEADLINE_FACES:
        lines = wrap_headline(_soften(text, font, WIDTH), font, WIDTH)
        per_page = max(1, (room - font.height) // pitch + 1)
        pages = [lines[i : i + per_page] for i in range(0, len(lines), per_page)] or [[]]
        if font is SMALL or len(pages) <= BODY_PAGES:
            break
    return font, pitch, pages


def _detail_frames(
    line: LineStatus,
    more: int,
    stale: str | None,
    ground: Color = BLACK,
    buses: frozenset[str] = frozenset(),
) -> list[Frame]:
    """The line's whole headline, on as many pages as it takes; "+N MORE" on its last."""
    headline = line.headline if isinstance(line.headline, str) and line.headline.strip() else ""
    room = DETAIL_BOTTOM - (FOOTER_PITCH if stale is not None else 0) - DETAIL_TOP + 1
    if more > 0:
        room -= FOOTER_PITCH
    font, pitch, pages = headline_pages(headline or NO_DETAILS, room, buses)
    frames = []
    for number, lines in enumerate(pages):
        frame = new_frame(ground)
        fill_rect(frame, 0, 0, SIZE - 1, HEADER_Y + BADGE, band(ground))
        draw_row(frame, HEADER_Y - 1, line)
        footers: list[tuple[str, Color]] = []
        if stale is not None:
            footers.append((stale, AMBER))
        if more > 0 and number == len(pages) - 1:
            footers.append((f"+{more} MORE", SECONDARY))
        for index, (text, color) in enumerate(footers):
            y = DETAIL_BOTTOM - SMALL.height + 1 - (len(footers) - 1 - index) * FOOTER_PITCH
            draw_text(frame, LEFT, y, text, color, SMALL)
        for index, text in enumerate(lines):
            draw_headline_line(frame, LEFT, DETAIL_TOP + index * pitch, text, font)
        frames.append(frame)
    return frames


# the screen


def _no_data_frame(view: DayView) -> Frame:
    frame = new_frame(page_ground(view))
    _header(frame, "CITY", day_stamp(view.day_local), accent=day_accent(view.day_local))
    draw_text(frame, LEFT, TEMP_Y, MISSING, TEXT, BODY, scale=2)
    draw_text(frame, LEFT, NOTICE_Y, NO_DATA, TEXT, SMALL)
    return frame


def city_for(view: DayView) -> CityStatus | None:
    """The view's city status when it is for the requested day, else None."""
    city = view.city
    if not isinstance(city, CityStatus) or city.day_local != view.day_local:
        return None
    if city.weather is None and city.transit_fetched_at_utc is None and not city.lines:
        return None
    return city


def render_city(view: DayView, now: datetime | None = None) -> Clip:
    """The weather page, the lines page, then the whole headline, on as many pages as it
    needs, of each of the first DETAIL_PAGES delayed or suspended lines, as a paged clip;
    one "CITY NO DATA" still when the view has no city status for its day. `now` (aware UTC)
    dates staleness and tells day from night; without it nothing is called stale and a clear
    sky is the sun."""
    city = city_for(view)
    if city is None:
        return still(_no_data_frame(view))
    frames = [_weather_frame(view, city, now), _lines_frame(view, city, now)]
    affected = detail_lines(city)
    stale = as_of_label(city.transit_fetched_at_utc, now, view.home_tz, view.city_stale_minutes)
    shown = affected[:DETAIL_PAGES]
    buses = frozenset(
        str(line.line).upper()
        for line in city.lines
        if isinstance(line, LineStatus) and line.kind == "bus"
    )
    for index, line in enumerate(shown):
        more = len(affected) - len(shown) if index == len(shown) - 1 else 0
        frames.extend(_detail_frames(line, more, stale, page_ground(view), buses))
    durations = (WEATHER_MS, LINES_MS, *(DETAIL_MS,) * (len(frames) - 2))
    return Clip(tuple(frames), durations)
