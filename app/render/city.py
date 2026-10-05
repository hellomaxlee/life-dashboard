"""The City screen: the day's weather and the state of the lines Max rides, as paged stills.

Drawn from the view's `city` (app/city/model.py), which is always the requested day's, never
`day_shown`. Pages, in order:

- weather: the condition icon, the current temperature large, high and low, one line of
  notice (the first National Weather Service alert in amber, else the condition in words), and
  the day in six 3-hour steps: temperature, a small condition icon, a rain-probability gauge
  and the hour ("6P", "12A"). Steps past midnight are in the as-of colour behind a dotted rule.
- lines: one row per line: a badge in its MTA colour (a disc for a subway, a blue pill for a
  bus), its name in white beside it, and its status as a word in a colour: green "OK", amber
  "WORK" (with a clock mark when it starts later today), red "DELAYS", red "NO SVC". The word
  carries the status; the colour only repeats it. The name sits beside the badge, not inside
  it: a 3x5 letter on a lit disc does not read at 1x under LED gamma, the same letter on
  black does. Seven lines get the body face; eight need a tighter row and the small face.
- up to DETAIL_PAGES detail pages, one per line that is not ok, most severe first, then what
  is in effect now before what starts later: that line's row as the header, and the alert's
  own headline word-wrapped under it. More affected lines than pages ends the last page
  with "+N MORE".

All ok and no alert is the common case: the weather and lines pages and nothing else.

Numbers. Every figure is a field of the CityStatus. Temperatures are rounded half up to whole
degrees for display (56.5 is drawn as 57, -0.5 as 0). A rain gauge is floored, never rounded
up: 99 % is one pixel short of full and 1 % lights nothing.

Night. The data carries no sunrise, so a clear sky is drawn as a moon from NIGHT_FROM to
DAY_FROM by the hour alone.

What is missing is said. A part never fetched: "WEATHER NO DATA" / "TRANSIT NO DATA" on its
page. A part older than the view's `city_stale_minutes`: the page's header becomes an amber
"AS OF 14:05" (weekday and clock, then whole days, for older ones, as on the Today screen);
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
    RING,
    SECONDARY,
    TEXT,
    TRACK,
    WHITE,
)
from app.render.screens import LEFT, RIGHT, clean_summary, draw_disc, fill_rect, wrap_lines
from app.render.view import DayView
from app.timeutil import from_utc_iso

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

ICON_X, ICON_Y = 2, 9
TEMP_X, TEMP_Y = 17, 9
HIGH_Y, LOW_Y = 10, 17
NOTICE_Y = 25
STEPS = 6
STEP_PITCH = 10
STEP_INNER = 9
STEP_TEMP_Y = 32
STEP_ICON_Y = 38
BAR_TOP = 45
BAR_HEIGHT = 10
BAR_WIDTH = 5
STEP_LABEL_Y = 57
NIGHT_FROM, DAY_FROM = 19, 6

ROWS_TOP = 8
MAX_ROWS = 8
ROOMY_ROWS = 7
ROW_PITCH = 8
TIGHT_PITCH = 7
BADGE = 7
TIGHT_BADGE = 5
NAME_X = 11
TIGHT_NAME_X = 9
NAME_GAP = 4
STATUS_X = 38
TIGHT_STATUS_X = 28
MARK_GAP = 2

DETAIL_TOP = 11
DETAIL_BOTTOM = 62
FOOTER_PITCH = 7
HEADLINE_FACES = ((BODY, 9), (SMALL, 7))
BULLET = re.compile(r"\[([A-Za-z0-9]{1,4})\]")
ELLIPSIS = "..."
NO_DETAILS = "No details given."

CLEAR, CLEAR_NIGHT, PARTLY, OVERCAST, FOG = "clear", "clear-night", "partly", "overcast", "fog"
RAINY, HEAVY_RAIN, SNOW, THUNDER, UNKNOWN = "rain", "heavy-rain", "snow", "thunder", "unknown"
CONDITIONS = (
    CLEAR,
    CLEAR_NIGHT,
    PARTLY,
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
        return PARTLY
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


def draw_icon(frame: Frame, x: int, y: int, rows: tuple[str, ...]) -> None:
    pixels = frame.load()
    for row_index, row in enumerate(rows):
        for col_index, cell in enumerate(row):
            px, py = x + col_index, y + row_index
            if cell in INK and 0 <= px < SIZE and 0 <= py < SIZE:
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
    clock = local.strftime("%H:%M")
    if days_back <= 0:
        return f"AS OF {clock}"
    if days_back <= 6:
        return f"AS OF {WEEKDAYS[local.weekday()]} {clock}"
    return f"AS OF {days_back}D AGO"


def _header(frame: Frame, title: str, right: str, stale: str | None = None) -> None:
    """The one header row every city page shares; a stale page's row is its amber as-of."""
    if stale is not None:
        draw_text(frame, LEFT, HEADER_Y, stale, AMBER, SMALL)
        return
    draw_text(frame, LEFT, HEADER_Y, title, LABEL, SMALL)
    draw_text_right(frame, RIGHT, HEADER_Y, right, TEXT, SMALL)


# the weather page


def _compact(text: str) -> tuple[list[tuple[str, int]], int] | None:
    """A step's label as (piece, width) runs no wider than a step: as drawn when it fits, else
    with a leading "1" as a one-pixel stroke and a minus as two pixels; None if still too wide."""
    if text_width(text) <= STEP_INNER:
        return [(text, text_width(text))], text_width(text)
    pieces: list[tuple[str, int]] = []
    rest = text
    if rest.startswith("-"):
        pieces.append(("-", 2))
        rest = rest[1:]
    if rest.startswith("1"):
        pieces.append(("1", 1))
        rest = rest[1:]
    if rest:
        pieces.append((rest, text_width(rest)))
    width = sum(w for _, w in pieces) + len(pieces) - 1
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


def _draw_strip(frame: Frame, steps: list[Step], temps: bool) -> None:
    pixels = frame.load()
    was_tomorrow = False
    for index, step in enumerate(steps):
        ink = SECONDARY if step.tomorrow else TEXT
        if step.tomorrow and not was_tomorrow and index:
            for y in range(STEP_ICON_Y, BAR_TOP + BAR_HEIGHT, 2):
                pixels[step.x - 1, y] = RING
        was_tomorrow = step.tomorrow
        if temps and step.temp is not None:
            _draw_compact(frame, step.x, STEP_TEMP_Y, step.temp, ink)
        draw_icon(frame, step.x + 1, STEP_ICON_Y, MINI_ICONS[step.kind])
        x0, y0, x1, y1 = bar_box(step)
        fill_rect(frame, x0, y0, x1, y1, TRACK)
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


def _draw_high_low(frame: Frame, weather: Weather) -> int:
    """ "H 62" over "L 51" at the right edge. Returns the x the block starts at."""
    values = [degrees(weather.high_f) or MISSING, degrees(weather.low_f) or MISSING]
    letter_x = RIGHT + 1 - max(text_width(v) for v in values) - 3 - text_width("H")
    for letter, value, y in zip("HL", values, (HIGH_Y, LOW_Y), strict=True):
        draw_text(frame, letter_x, y, letter, LABEL, SMALL)
        draw_text_right(frame, RIGHT, y, value, TEXT, SMALL)
    return letter_x


def _local_hour(now: datetime | None, home_tz: str) -> int | None:
    return None if now is None else now.astimezone(ZoneInfo(home_tz)).hour


def _weather_frame(view: DayView, city: CityStatus, now: datetime | None) -> Frame:
    frame = new_frame()
    weather = city.weather
    if not isinstance(weather, Weather):
        _header(frame, "WEATHER", NO_DATA)
        draw_text(frame, TEMP_X, TEMP_Y, MISSING, TEXT, BODY, scale=2)
        return frame
    stale = as_of_label(weather.fetched_at_utc, now, view.home_tz, view.city_stale_minutes)
    _header(frame, "WEATHER", day_stamp(view.day_local), stale)
    kind = condition(weather.code, _local_hour(now, view.home_tz))
    draw_icon(frame, ICON_X, ICON_Y, ICONS[kind])
    room_right = _draw_high_low(frame, weather)
    _draw_temperature(frame, degrees(weather.temp_f) or MISSING, room_right - 2)

    alerts = [a for a in weather.alerts if isinstance(a, str) and a.strip()]
    notice = alert_lines(alerts[0]) if alerts else []
    if notice:
        draw_text(frame, LEFT, NOTICE_Y, notice[0], AMBER, SMALL)
        if len(notice) > 1:
            draw_text(frame, LEFT, STEP_TEMP_Y, notice[1], AMBER, SMALL)
    elif CONDITION_WORDS[kind]:
        draw_text(frame, LEFT, NOTICE_Y, CONDITION_WORDS[kind], SECONDARY, SMALL)
    _draw_strip(frame, strip_steps(weather), temps=len(notice) < 2)
    return frame


# the lines page


def line_color(line: LineStatus) -> Color:
    """The badge's fill: the MTA colour for a subway letter, the bus blue for a bus; a
    subway name the table does not know is the shuttle grey."""
    if line.kind == "bus":
        return BUS_BLUE
    name = str(line.line).upper()
    return SUBWAY_COLORS.get(name) or SUBWAY_COLORS.get(name[:1], MTA_GREY)


def badge_box(y: int, tight: bool = False) -> tuple[int, int, int, int]:
    """(x0, y0, x1, y1) of the square a row's badge sits in, inclusive, for a row at `y`."""
    size = TIGHT_BADGE if tight else BADGE
    return LEFT, y, LEFT + size - 1, y + size - 1


def draw_badge(frame: Frame, y: int, line: LineStatus, tight: bool = False) -> None:
    """The line's colour as a shape of its own: a disc for a subway, a pill (two rows
    shorter) for a bus."""
    x0, y0, x1, y1 = badge_box(y, tight)
    color = line_color(line)
    if line.kind == "bus":
        fill_rect(frame, x0, y0 + 1, x1, y1 - 1, color)
        if not tight:
            for x, y in ((x0, y0 + 1), (x1, y0 + 1), (x0, y1 - 1), (x1, y1 - 1)):
                frame.putpixel((x, y), BLACK)
        return
    radius = (x1 - x0) // 2
    draw_disc(frame, x0 + radius, y0 + radius, radius, color)


def status_word(line: LineStatus) -> str:
    return STATUS_WORDS.get(line.status, OTHER_STATUS_WORD)


def status_color(line: LineStatus) -> Color:
    return STATUS_COLORS.get(line.status, AMBER)


def starts_later(line: LineStatus) -> bool:
    return line.status != "ok" and not line.now


def name_font(line: LineStatus, tight: bool = False) -> Font:
    """The body face while the name leaves the status its column; else the small face."""
    roomy = NAME_X + text_width(str(line.line), BODY) + NAME_GAP <= STATUS_X
    return BODY if roomy and not tight else SMALL


def draw_row(frame: Frame, y: int, line: LineStatus, tight: bool = False) -> None:
    """One line: its badge, its name in white beside it, then its status as a word in the
    status colour, with a clock mark when the alert starts later today (the word moves left
    to make room for it; a name so long that it still cannot fit goes without). `y` is the
    row's top; a tight row (the eight-line form) is five pixels tall, any other seven."""
    draw_badge(frame, y, line, tight)
    font = name_font(line, tight)
    text_y = y if tight or font is BODY else y + 1
    name_x = TIGHT_NAME_X if tight else NAME_X
    after = draw_text(frame, name_x, text_y, str(line.line), WHITE, font)
    column = TIGHT_STATUS_X if tight else STATUS_X
    status_y = y if tight else y + 1
    word, color = status_word(line), status_color(line)
    marked = text_width(word) + MARK_GAP + len(CLOCK[0])
    first = after - 1 + NAME_GAP
    clock = starts_later(line) and first + marked - 1 <= RIGHT
    x = max(first, min(column, RIGHT + 1 - marked)) if clock else max(first, column)
    after = draw_text(frame, x, status_y, word, color, SMALL)
    if clock:
        _draw_mark(frame, after - 1 + MARK_GAP, status_y, CLOCK, color)


def line_rows(count: int) -> tuple[bool, list[int]]:
    """(tight, the top y of each row). Up to ROOMY_ROWS lines get the seven-pixel row with the
    name in the body face; MAX_ROWS need the tighter pitch and the small face."""
    shown = min(count, MAX_ROWS)
    if shown <= ROOMY_ROWS:
        return False, [ROWS_TOP + index * ROW_PITCH for index in range(shown)]
    return True, [ROWS_TOP + 1 + index * TIGHT_PITCH for index in range(shown)]


def _lines_frame(view: DayView, city: CityStatus, now: datetime | None) -> Frame:
    frame = new_frame()
    lines = [line for line in city.lines if isinstance(line, LineStatus)]
    if city.transit_fetched_at_utc is None:
        _header(frame, "TRANSIT", NO_DATA)
        return frame
    stale = as_of_label(city.transit_fetched_at_utc, now, view.home_tz, view.city_stale_minutes)
    _header(frame, "TRANSIT", day_stamp(view.day_local), stale)
    if not lines:
        draw_text(frame, LEFT, ROWS_TOP + 1, "NO LINES", TEXT, SMALL)
        return frame
    tight, rows = line_rows(len(lines))
    hidden = len(lines) - len(rows)
    if hidden > 0:
        rows = rows[:-1]
    for line, y in zip(lines, rows, strict=False):
        draw_row(frame, y, line, tight)
    if hidden > 0:
        y = ROWS_TOP + 1 + (MAX_ROWS - 1) * TIGHT_PITCH
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


def _soften(text: str, font: Font, width: int) -> str:
    """Let a word too wide for a line break after its hyphens and slashes
    ("Astoria-Ditmars") instead of mid-syllable, and write the feed's bracketed line
    bullets ("[F] trains") as the bare line ("F trains"): the small face has no brackets."""
    words = []
    for word in BULLET.sub(r"\1", text).split():
        if text_width(word, font) > width:
            word = word.replace("-", "- ").replace("/", "/ ").strip()
        words.append(word)
    return " ".join(words)


def _wrap_small(text: str, width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if text_width(candidate, SMALL) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        while text_width(word, SMALL) > width:
            cut = len(word) - 1
            while cut > 1 and text_width(word[:cut], SMALL) > width:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        current = word
    if current:
        lines.append(current)
    return lines


def headline_layout(headline: str, room: int) -> tuple[Font, int, list[str]]:
    """(face, line pitch, lines) for a headline in `room` pixel rows: the body face with the
    Books wrap while it fits, then the small face; a headline too long for that keeps the
    lines that fit and ends the last one in "..."."""
    text = clean_summary(headline)
    for font, pitch in HEADLINE_FACES:
        if font is BODY:
            lines = wrap_lines(_soften(text, BODY, WIDTH))
        else:
            lines = _wrap_small(_soften(text, SMALL, WIDTH), WIDTH)
        if (len(lines) - 1) * pitch + font.height <= room:
            return font, pitch, lines
    kept = lines[: max(1, (room - font.height) // pitch + 1)]
    last = kept[-1].rstrip(" .,;:")
    while last and text_width(last + ELLIPSIS, font) > WIDTH:
        last = last[:-1].rstrip(" .,;:")
    kept[-1] = last + ELLIPSIS
    return font, pitch, kept


def _detail_frame(line: LineStatus, more: int, stale: str | None) -> Frame:
    frame = new_frame()
    draw_row(frame, HEADER_Y - 1, line)
    footers: list[tuple[str, Color]] = []
    if stale is not None:
        footers.append((stale, AMBER))
    if more > 0:
        footers.append((f"+{more} MORE", SECONDARY))
    bottom = DETAIL_BOTTOM - len(footers) * FOOTER_PITCH
    for index, (text, color) in enumerate(footers):
        y = DETAIL_BOTTOM - SMALL.height + 1 - (len(footers) - 1 - index) * FOOTER_PITCH
        draw_text(frame, LEFT, y, text, color, SMALL)
    headline = line.headline if isinstance(line.headline, str) and line.headline.strip() else ""
    font, pitch, lines = headline_layout(headline or NO_DETAILS, bottom - DETAIL_TOP + 1)
    for index, text in enumerate(lines):
        draw_text(frame, LEFT, DETAIL_TOP + index * pitch, text, TEXT, font)
    return frame


# the screen


def _no_data_frame(view: DayView) -> Frame:
    frame = new_frame()
    _header(frame, "CITY", day_stamp(view.day_local))
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
    """The weather page, the lines page, then a detail page for each of the first DETAIL_PAGES
    affected lines, as a paged clip; one "CITY NO DATA" still when the view has no city status
    for its day. `now` (aware UTC) dates staleness and tells day from night; without it
    nothing is called stale and a clear sky is the sun."""
    city = city_for(view)
    if city is None:
        return still(_no_data_frame(view))
    frames = [_weather_frame(view, city, now), _lines_frame(view, city, now)]
    affected = affected_lines(city)
    stale = as_of_label(city.transit_fetched_at_utc, now, view.home_tz, view.city_stale_minutes)
    shown = affected[:DETAIL_PAGES]
    for index, line in enumerate(shown):
        more = len(affected) - len(shown) if index == len(shown) - 1 else 0
        frames.append(_detail_frame(line, more, stale))
    durations = (WEATHER_MS, LINES_MS, *(DETAIL_MS,) * len(shown))
    return Clip(tuple(frames), durations)
