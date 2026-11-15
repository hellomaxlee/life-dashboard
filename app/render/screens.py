"""The three rotation screens: Week, Today, Books + summary.

Each takes a DayView and returns a Clip. Every None in the view is drawn as a stated
fallback, so a view with nothing in it still yields three full frames. Rest is drawn plainly
and never as a miss: an empty dot is a quiet ring, a short night is a blue number, not a red one.

The summary is shown as word-wrapped pages, two lines at a time, about two seconds each. A
pixel scroll of a 110-character line needs some 300 frames and the device holds fewer than
60, so pages are the form that reaches the panel as drawn. Its three voices are told apart by
colour: a quotation in VOICE, its "- Author" in ATTRIBUTION, Max's own words in TEXT.

Each screen's header word wears the weekday's accent (palette.day_accent), so the panel is
not one colour seven days running; the stamp on the right and the field labels keep TEXT and
LABEL. Week is a small scene: each earned workout is a planet in a night sky whose stars are
seeded by the day, and a star never lands within a pixel of anything else (nothing bleeds).
The structural tones (the sleep bar's track, the empty rings, the inactive page pip) come
from the month's theme (theme.py): the stock palette tones unless the month has a feature.
"""

from __future__ import annotations

import math
import random
import re
import unicodedata
import zlib
from collections.abc import Callable
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.metrics.wins import overslept
from app.render.font import (
    BODY,
    SMALL,
    draw_text,
    draw_text_centered,
    draw_text_right,
    normalize,
    text_width,
)
from app.render.frame import SIZE, Clip, Color, Frame, new_frame, still
from app.render.palette import (
    ATTRIBUTION,
    BLACK,
    CORAL,
    DOTS,
    GOLD,
    GREEN,
    LABEL,
    RING,
    SECONDARY,
    SKY,
    SPINES,
    TEXT,
    VIOLET,
    VOICE,
    WHEEL,
    WHITE,
    WOOD,
    YELLOW,
    day_accent,
    day_ground,
    dim,
)
from app.render.theme import theme_for
from app.render.usage import draw_usage, usage_state
from app.render.view import DayView, has_health_data, valid_count, valid_sleep_hours
from app.timeutil import clock_12h, from_utc_iso

LEFT = 2
RIGHT = 61
DOT_LABEL_X = 13
DOT_ROW_Y = 17
STREAK_Y = 28
SLEEP_BAR_HOURS = 10
LINE_WIDTH = 60
WORD_WIDTH = SIZE - LEFT
PAGE_MS = 2000
TITLE_GAP = 3
AS_OF_Y = 58
SUMMARY_MAX_CHARS = 220
SUMMARY_LINE_YS = (45, 54)
PAGE_PIP_Y = SIZE - 1
NO_SUMMARY = "No summary yet."
_WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
_SPINE_HEIGHTS = (13, 11, 14, 12, 13, 10, 14, 12, 11, 13, 12, 14)
MOON = (
    "...####..",
    "..###....",
    ".###.....",
    "###......",
    "###......",
    "###......",
    "####....#",
    ".####..##",
    "..######.",
    "...####..",
)
ZZ = (
    ".....###",
    "......#.",
    ".....###",
    "###.....",
    ".#......",
    "###.....",
)
ZZ_X = RIGHT - len(ZZ[0]) + 1
ZZ_GAP = 2


def _in_disc(dx: int, dy: int, radius: int) -> bool:
    return dx * dx + dy * dy <= radius * radius + radius


def draw_disc(frame: Frame, cx: int, cy: int, radius: int, color: Color) -> None:
    pixels = frame.load()
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            x, y = cx + dx, cy + dy
            if _in_disc(dx, dy, radius) and 0 <= x < SIZE and 0 <= y < SIZE:
                pixels[x, y] = color


def draw_ring(
    frame: Frame, cx: int, cy: int, radius: int, color: Color, dashed: bool = False
) -> None:
    pixels = frame.load()
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            x, y = cx + dx, cy + dy
            on_edge = _in_disc(dx, dy, radius) and not _in_disc(dx, dy, radius - 1)
            if not on_edge or not (0 <= x < SIZE and 0 <= y < SIZE):
                continue
            if dashed and (dx + dy) % 3 == 0:
                continue
            pixels[x, y] = color


PLANET_MIN_RADIUS = 5
SKY_TOP = 9
SKY_BOTTOM = 39
SKY_STARS = 5
PLANET_GROW = 1


def draw_planet(frame: Frame, cx: int, cy: int, radius: int, color: Color, index: int) -> None:
    """A weekly dot as a small world: the dot's own colour, a night side to the lower right,
    a glint to the upper left, and either two bands in the next colour of the family or (every
    second planet) a ring. Under PLANET_MIN_RADIUS it is the plain disc: no room for detail."""
    draw_disc(frame, cx, cy, radius, color)
    if radius < PLANET_MIN_RADIUS:
        return
    pixels = frame.load()
    band = WHEEL[(WHEEL.index(color) + 1) % len(WHEEL)] if color in WHEEL else WHITE
    ringed = index % 2 == 1
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            x, y = cx + dx, cy + dy
            if not _in_disc(dx, dy, radius) or not (0 <= x < SIZE and 0 <= y < SIZE):
                continue
            tone = band if not ringed and dy in (-2, 2) else color
            if dx * 0.8 + dy > radius * 0.55:
                tone = dim(tone, 0.5)
            pixels[x, y] = tone
    glint = radius // 2
    for dx in (0, 1):
        pixels[cx - glint + dx, cy - glint] = WHITE
    if ringed:
        for dx in range(-radius - 2, radius + 3):
            x, y = cx + dx, cy + round(dx * 0.25)
            behind = _in_disc(dx, y - cy, radius) and y <= cy
            if not behind and 0 <= x < SIZE and 0 <= y < SIZE:
                pixels[x, y] = TEXT


def scatter_stars(
    frame: Frame,
    seed: str,
    colors: tuple[Color, ...],
    keep_out: tuple[tuple[int, int, int], ...] = (),
    count: int = SKY_STARS,
    rows: tuple[int, int] = (SKY_TOP, SKY_BOTTOM),
) -> None:
    """Plus-shaped stars (`draw_star`) between `rows` (SKY_TOP to SKY_BOTTOM unless given),
    placed from `seed` so each day has its own sky. A star is drawn only where the 7x7 block
    around it is unlit, so none crowds a word, a planet or another star, and never inside a
    `keep_out` disc (cx, cy, radius): an empty orbit stays empty."""
    rng = random.Random(zlib.crc32(seed.encode()))
    pixels = frame.load()
    placed = 0
    for _ in range(count * 12):
        if placed == count:
            break
        x, y = rng.randrange(3, SIZE - 3), rng.randrange(rows[0] + 2, rows[1] - 1)
        color = colors[rng.randrange(len(colors))]
        clear = all(
            pixels[x + dx, y + dy] == BLACK
            for dy in range(-3, 4)
            for dx in range(-3, 4)
            if 0 <= x + dx < SIZE and 0 <= y + dy < SIZE
        ) and not any(_in_disc(x - cx, y - cy, radius + 3) for cx, cy, radius in keep_out)
        if clear:
            draw_star(frame, x, y, color)
            placed += 1


Layer = tuple[Callable[[int, int], bool], Color]


def paint_behind(frame: Frame, layers: tuple[Layer, ...]) -> None:
    """Scenery behind what is already drawn. Call it last. Each layer is (covers(x, y), tone);
    a later layer paints over an earlier one. A pixel is lit only where nothing drawn before
    the call is within one pixel of it, so every word, number and mark keeps a black edge and
    none touches the scenery (Max, 2026-11-15: nothing bleeds)."""
    pixels = frame.load()
    drawn = {(x, y) for y in range(SIZE) for x in range(SIZE) if pixels[x, y] != BLACK}
    for y in range(SIZE):
        for x in range(SIZE):
            if any((x + dx, y + dy) in drawn for dy in (-1, 0, 1) for dx in (-1, 0, 1)):
                continue
            for covers, tone in layers:
                if covers(x, y):
                    pixels[x, y] = tone


def rows_between(top: int, bottom: int) -> Callable[[int, int], bool]:
    return lambda x, y: top <= y <= bottom


def draw_field(frame: Frame, rows: tuple[int, int], tone: Color) -> None:
    """A flat field of `tone` over `rows`, inclusive, behind what is already drawn."""
    paint_behind(frame, ((rows_between(*rows), tone),))


def draw_star(frame: Frame, x: int, y: int, color: Color) -> None:
    """A star as a small plus: a white core with four arms in `color`."""
    pixels = frame.load()
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        if 0 <= x + dx < SIZE and 0 <= y + dy < SIZE:
            pixels[x + dx, y + dy] = color
    pixels[x, y] = WHITE


def draw_bitmap(frame: Frame, x: int, y: int, rows: tuple[str, ...], color: Color) -> None:
    pixels = frame.load()
    for row_index, row in enumerate(rows):
        for col_index, cell in enumerate(row):
            px, py = x + col_index, y + row_index
            if cell == "#" and 0 <= px < SIZE and 0 <= py < SIZE:
                pixels[px, py] = color


def fill_rect(frame: Frame, x0: int, y0: int, x1: int, y1: int, color: Color) -> None:
    """Inclusive rectangle, clipped to the frame."""
    pixels = frame.load()
    for y in range(max(y0, 0), min(y1, SIZE - 1) + 1):
        for x in range(max(x0, 0), min(x1, SIZE - 1) + 1):
            pixels[x, y] = color


def dot_layout(target: int) -> tuple[list[int], int]:
    """Centre x of each weekly dot and their shared radius."""
    count = max(target, 1)
    radius = max(2, min(7, (SIZE // count - 4) // 2))
    centres = [int(SIZE * (2 * i + 1) / (2 * count) + 0.5) for i in range(count)]
    return centres, radius


def dot_color(index: int) -> Color:
    return DOTS[index % len(DOTS)]


def _count(value: int | None) -> int | None:
    return value if valid_count(value) else None


def _draw_streak(frame: Frame, streak: int) -> None:
    """ "N WK STREAK", centred. Steps down in size until the whole line fits the frame."""
    number = str(streak)
    for font, tail in ((BODY, "WK STREAK"), (BODY, "WK"), (SMALL, "WK")):
        total = text_width(number, font) + 3 + text_width(tail, SMALL)
        if total <= RIGHT - LEFT + 1:
            x = (SIZE - total) // 2
            y = STREAK_Y if font is BODY else STREAK_Y + 2
            after = draw_text(frame, x, y, number, GOLD if streak else TEXT, font)
            draw_text(frame, after + 2, STREAK_Y + 2, tail, LABEL, SMALL)
            return
    draw_text_centered(frame, STREAK_Y + 2, "MANY WK STREAK", LABEL, SMALL)


def _week_frame(view: DayView, now: datetime) -> Frame:
    frame = new_frame()
    theme = theme_for(view)
    draw_text(frame, LEFT, 2, "WEEK", day_accent(view.day_local), SMALL)
    centres, radius = dot_layout(view.week_target)
    dots = _count(view.week_dots)
    streak = _count(view.streak_weeks)
    if dots is None:
        draw_text_right(frame, RIGHT, 2, "NO DATA", TEXT, SMALL)
    else:
        draw_text_right(frame, RIGHT, 2, f"{dots} OF {view.week_target}", TEXT, SMALL)
    world = radius + PLANET_GROW if radius >= PLANET_MIN_RADIUS else radius
    for index, cx in enumerate(centres):
        if dots is None:
            draw_ring(frame, cx, DOT_ROW_Y, world, theme.ring, dashed=True)
        elif index < dots:
            draw_planet(frame, cx, DOT_ROW_Y, world, dot_color(index), index)
        else:
            draw_ring(frame, cx, DOT_ROW_Y, world, theme.ring)

    if streak is None:
        draw_text_centered(frame, STREAK_Y + 2, "STREAK NO DATA", TEXT, SMALL)
    else:
        _draw_streak(frame, streak)

    accent = day_accent(view.day_local)
    orbits = tuple((cx, DOT_ROW_Y, world) for cx in centres)
    scatter_stars(frame, view.day_local, (WHITE, accent), orbits)
    draw_usage(frame, usage_state(view.claude, now, view.stale_hours), theme.track)
    inside = lambda x, y: any(_in_disc(x - cx, y - DOT_ROW_Y, world) for cx in centres)  # noqa: E731
    sky = lambda x, y: SKY_TOP - 1 <= y <= SKY_BOTTOM + 1 and not inside(x, y)  # noqa: E731
    paint_behind(frame, ((sky, day_ground(view.day_local)),))
    return frame


def render_week(view: DayView, now: datetime) -> Clip:
    """Three planets in the day's sky (an unearned one is an empty orbit), the weeks-hit
    streak under them, the Claude usage bar at the bottom.
    A still even when the usage reading is stale: amber dot, amber age line."""
    return still(_week_frame(view, now))


def sleep_text(hours: float) -> str:
    """One decimal, truncated, so the frame never shows 7.0 for a night short of 7."""
    return f"{int(hours * 10 + 1e-6) / 10:.1f}"


def sleep_over(view: DayView) -> bool:
    """A night shown as more than the upper bound (Max, 2026-10-09: yellow over 9 hours)."""
    return valid_sleep_hours(view.sleep_hours) and overslept(view.sleep_hours, view.sleep_max_hours)


def sleep_met(view: DayView) -> bool:
    """The sleep win as the frame shows it: the displayed number inside the band, at the
    target or more and not over the upper bound."""
    if not valid_sleep_hours(view.sleep_hours) or sleep_over(view):
        return False
    return float(sleep_text(view.sleep_hours)) >= view.sleep_target_hours


def sleep_color(view: DayView) -> Color:
    """Green inside the band, yellow over it, sky short of it."""
    if sleep_over(view):
        return YELLOW
    return GREEN if sleep_met(view) else SKY


def sleep_fill(hours: float) -> int:
    """Bar pixels for a night, floored, so a night short of the goal never touches its tick."""
    return min(60, int(hours * 60 / SLEEP_BAR_HOURS + 1e-9))


def _hours_label(hours: float) -> str:
    return f"{int(hours)}H" if hours == int(hours) else f"{hours:.1f}H"


def sleep_goal_label(view: DayView) -> str:
    """The band, "GOAL 7-9H"; the target alone when the band does not fit beside "SLEEP"."""
    low, high = _hours_label(view.sleep_target_hours), _hours_label(view.sleep_max_hours)
    band = f"GOAL {low[:-1]}-{high}"
    room = RIGHT - LEFT + 1 - text_width("SLEEP", SMALL) - TITLE_GAP
    if view.sleep_max_hours > view.sleep_target_hours and text_width(band, SMALL) <= room:
        return band
    return f"GOAL {low}"


def _has_day_data(view: DayView) -> bool:
    return (
        valid_sleep_hours(view.sleep_hours)
        or _count(view.steps) is not None
        or view.today_dot is not None
    )


def as_of_label(view: DayView, now: datetime | None = None) -> str | None:
    """When the day's data is from, or None to leave the line out.

    Same day: the clock ("AS OF 1:30PM"; 12-hour, Max 2026-10-08). One to six days back:
    weekday and clock, which is unambiguous within a week, with the one-letter suffix ("AS OF
    WED 1:30P") because "AS OF WED 12:30PM" is 66 px, wider than the frame. Older: whole days
    ("AS OF 8D AGO"), so an old push never reads as recent. A push
    dated after the view's day, or after `now`, cannot have fed this day and counts as none.
    With no usable push the line says "NO PUSH YET" only when the day has no data either; a day
    whose numbers a later push back-filled gets no as-of line rather than a false one.
    """
    missing = None if _has_day_data(view) else "NO PUSH YET"
    if view.as_of_utc is None:
        return missing
    moment = from_utc_iso(view.as_of_utc)
    if now is not None and moment > now:
        return missing
    local = moment.astimezone(ZoneInfo(view.home_tz))
    days_back = (date.fromisoformat(view.day_local) - local.date()).days
    if days_back < 0:
        return missing
    if days_back == 0:
        return f"AS OF {clock_12h(local)}"
    if days_back <= 6:
        return f"AS OF {_WEEKDAYS[local.weekday()]} {clock_12h(local, compact=True)}"
    return f"AS OF {days_back}D AGO"


def no_dot_label(view: DayView) -> str:
    """Why the day has no dot, from the day's own row: no workout on a day a push has
    covered, a workout with no usable heart rate, or its load against the week's bar
    (floored, so it never reads as met). "NO DOT YET" when the row does not say."""
    if view.workout_count == 0 and has_health_data(view):
        return "NO WORKOUT"
    if not view.workout_count:
        return "NO DOT YET"
    if view.workout_load is None:
        return "NO HR DATA"
    if view.load_bar is not None and 0 <= view.workout_load < view.load_bar:
        label = f"LOAD {int(view.workout_load)}/{int(view.load_bar)}"
        if DOT_LABEL_X + text_width(label, SMALL) - 1 <= RIGHT:
            return label
    return "NO DOT YET"


TODAY_SKY = (8, 37)
HILL_BACK: Color = (0, 95, 40)
HILL_FRONT: Color = (20, 150, 45)


def hill_back(x: int) -> int:
    return 40 + round(2.5 * math.sin(x / 9 + 1.0))


def hill_front(x: int) -> int:
    return 49 + round(3 * math.sin(x / 6 + 4.0))


def _today_scene(frame: Frame, view: DayView) -> None:
    """Style B (2026-11-15): last night's sky in the weekday's colour behind the moon and the
    sleep number, and two grassy hills under the day's facts, a black line along their crest
    so a green Thursday sky does not run into them. No stars: beside a number they
    read as stray dots (Max, 2026-11-15).
    Drawn last, a pixel off every word."""
    paint_behind(
        frame,
        (
            (lambda x, y: TODAY_SKY[0] <= y < hill_back(x) - 1, day_ground(view.day_local)),
            (lambda x, y: y >= hill_back(x), HILL_BACK),
            (lambda x, y: y >= hill_front(x), HILL_FRONT),
        ),
    )


def render_today(view: DayView, now: datetime | None = None) -> Clip:
    """Last night's sleep against the target, today's dot, steps, and when the data is from,
    in the day's scene."""
    frame = today_facts(view, now)
    _today_scene(frame, view)
    return still(frame)


def today_facts(view: DayView, now: datetime | None = None) -> Frame:
    """The Today screen's words, numbers and marks on black, before its scene."""
    frame = new_frame()
    theme = theme_for(view)
    day = date.fromisoformat(view.day_shown or view.day_local)
    title = "TODAY" if view.day_shown in (None, view.day_local) else "YESTERDAY"
    draw_text(frame, LEFT, 2, title, day_accent(view.day_local), SMALL)
    weekday = _WEEKDAYS[day.weekday()]
    stamp = f"{weekday} {day.day}"
    if LEFT + text_width(title, SMALL) + TITLE_GAP > RIGHT + 1 - text_width(stamp, SMALL):
        stamp = weekday
    draw_text_right(frame, RIGHT, 2, stamp, TEXT, SMALL)

    draw_bitmap(frame, LEFT, 11, MOON, VIOLET)
    target_x = LEFT + int(view.sleep_target_hours / SLEEP_BAR_HOURS * 60)
    fill_rect(frame, LEFT, 26, RIGHT, 28, theme.track)
    if not valid_sleep_hours(view.sleep_hours):
        draw_text(frame, 16, 9, "--", TEXT, BODY, scale=2)
        draw_text(frame, LEFT, 32, "SLEEP", LABEL, SMALL)
        draw_text_right(frame, RIGHT, 32, "NO DATA", TEXT, SMALL)
    else:
        color = sleep_color(view)
        number = sleep_text(view.sleep_hours)
        unit_room = 3 + text_width("h", BODY)
        x = min(16, RIGHT + 1 - text_width(number, BODY, 2) - unit_room)
        after = draw_text(frame, x, 9, number, color, BODY, scale=2)
        draw_text(frame, after + 1, 16, "h", color, BODY)
        filled = sleep_fill(view.sleep_hours)
        if filled:
            fill_rect(frame, LEFT, 26, LEFT + filled - 1, 28, color)
        draw_text(frame, LEFT, 32, "SLEEP", LABEL, SMALL)
        draw_text_right(frame, RIGHT, 32, sleep_goal_label(view), SECONDARY, SMALL)
    fill_rect(frame, target_x, 25, target_x, 29, WHITE)
    max_x = LEFT + int(view.sleep_max_hours / SLEEP_BAR_HOURS * 60)
    if target_x < max_x <= RIGHT:
        fill_rect(frame, max_x, 25, max_x, 29, WHITE)

    if view.today_dot is None:
        draw_ring(frame, 6, 44, 4, theme.ring, dashed=True)
        draw_text(frame, 13, 42, "DOT NO DATA", TEXT, SMALL)
    elif view.today_dot:
        draw_disc(frame, 6, 44, 4, GOLD)
        draw_text(frame, 13, 42, "WORKOUT DONE", GOLD, SMALL)
    else:
        draw_ring(frame, 6, 44, 4, theme.ring)
        draw_text(frame, DOT_LABEL_X, 42, no_dot_label(view), TEXT, SMALL)

    draw_text(frame, LEFT, 51, "STEPS", LABEL, SMALL)
    steps = "NO DATA" if _count(view.steps) is None else str(view.steps)
    draw_text_right(frame, RIGHT, 51, steps, TEXT, SMALL)
    as_of = as_of_label(view, now)
    if as_of is not None:
        draw_text(frame, LEFT, AS_OF_Y, as_of, SECONDARY, SMALL)
    return frame


def parked_as_of(as_of: str | None) -> str | None:
    """The as-of line as the parked screen prints it: Today's own when the "zz" fits after
    it with ZZ_GAP columns clear, else without its AS OF prefix. The weekday form ("AS OF
    WED 12:59P") can run to the frame's edge, where the mark would overprint the time."""
    if as_of is None or LEFT + text_width(as_of, SMALL) + ZZ_GAP <= ZZ_X:
        return as_of
    return as_of.removeprefix("AS OF ")


def render_parked(view: DayView, now: datetime | None = None) -> Clip:
    """Today with a dim "zz" at the foot: the one screen the panel keeps while the host
    sleeps (Max, 2026-10-19: a fallback for the sleeping Mac, "pretty, not obvious/stark").
    A complete screen with its as-of time, so nothing half-shown is left up, and the mark is
    the as-of line's own colour, so it reads as a note, not an alarm. A long as-of line is
    reprinted without AS OF (`parked_as_of`) so the mark never sits on the time."""
    frame = today_facts(view, now)
    as_of = as_of_label(view, now)
    short = parked_as_of(as_of)
    if short != as_of:
        fill_rect(frame, 0, AS_OF_Y, SIZE - 1, SIZE - 1, BLACK)
        draw_text(frame, LEFT, AS_OF_Y, short, SECONDARY, SMALL)
    draw_bitmap(frame, ZZ_X, AS_OF_Y, ZZ, SECONDARY)
    _today_scene(frame, view)
    return still(frame)


TIER_BOARDS = (24, 41)
TIER_HEIGHTS = (13, 11, 14, 12, 13, 10, 12, 14, 11, 13, 10, 14)
TIER_WIDTH_SWING = (0, -1, 1, 0, 1, -1)
SHELF_RIGHT = 46
DECOR_X = 54
BOOKS_WALL = (8, 42)
PLANT = (
    "..g.g..",
    ".g.g.g.",
    "..ggg..",
    "...g...",
    ".ppppp.",
    ".ppppp.",
    "..ppp..",
)
LAMP = (
    "..yyy..",
    ".yyyyy.",
    "yyyyyyy",
    "...w...",
    "...w...",
    "...w...",
    "..www..",
)
DECOR_INK: dict[str, Color] = {"g": GREEN, "p": CORAL, "y": GOLD, "w": TEXT}


def books_stamp(view: DayView) -> str:
    """The header's right side: the count against the target, "10 OF 12"; the count alone
    when that would reach the header word."""
    read = _count(view.books_ytd)
    if read is None:
        return "NO DATA"
    room = RIGHT + 1 - (LEFT + text_width("BOOKS", SMALL) + TITLE_GAP)
    for stamp in (f"{read} OF {view.books_target}", str(read)):
        if text_width(stamp, SMALL) <= room:
            return stamp
    return "MANY"


def shelf_slots(target: int) -> list[tuple[int, int, int, int]]:
    """(tier, x0, width, height) of every slot, in reading order: the first half of the
    target on the top shelf, the rest on the lower. Six to a shelf get widths that vary by a
    pixel; more than fit that way get one even width, never under one pixel."""
    slots = max(target, 1)
    upper = (slots + 1) // 2
    out = []
    for tier, count in enumerate((upper, slots - upper)):
        if not count:
            continue
        room = SHELF_RIGHT - LEFT + 1
        even = max(1, (room - (count - 1)) // count)
        swing = TIER_WIDTH_SWING if even >= 5 else (0,)
        x = LEFT
        for index in range(count):
            width = even + swing[index % len(swing)]
            if x + width - 1 > SHELF_RIGHT:
                break
            number = len(out)
            out.append((tier, x, width, TIER_HEIGHTS[number % len(TIER_HEIGHTS)]))
            x += width + 1
    return out


def _draw_decor(frame: Frame, tier: int, rows: tuple[str, ...]) -> None:
    pixels = frame.load()
    top = TIER_BOARDS[tier] - len(rows)
    for dy, row in enumerate(rows):
        for dx, cell in enumerate(row):
            if cell in DECOR_INK:
                pixels[DECOR_X + dx, top + dy] = DECOR_INK[cell]


def _books_base(view: DayView) -> Frame:
    """A two-shelf bookcase (style B, 2026-11-15): a read book is its lit spine with a band,
    an unread slot the outline of the book to come in the month theme's ring (one quiet tone
    for every slot; Max, 2026-11-09), a plant on the top shelf and a lamp on the lower, and
    behind them a wall in the weekday's colour. The count is the header's stamp."""
    frame = new_frame()
    theme = theme_for(view)
    draw_text(frame, LEFT, 2, "BOOKS", day_accent(view.day_local), SMALL)
    draw_text_right(frame, RIGHT, 2, books_stamp(view), TEXT, SMALL)
    read = _count(view.books_ytd) or 0
    pixels = frame.load()
    for number, (tier, x0, width, height) in enumerate(shelf_slots(view.books_target)):
        bottom = TIER_BOARDS[tier] - 1
        top = bottom - height + 1
        x1 = x0 + width - 1
        if number < read:
            color = SPINES[number % len(SPINES)]
            fill_rect(frame, x0, top, x1, bottom, color)
            fill_rect(frame, x0, top + 2, x1, top + 2, dim(color, 0.45))
            fill_rect(frame, x0, bottom - 3, x1, bottom - 2, dim(color, 0.45))
        else:
            for x in range(x0, x1 + 1):
                pixels[x, top] = theme.ring
            for y in range(top, bottom + 1):
                pixels[x0, y] = pixels[x1, y] = theme.ring
    for board in TIER_BOARDS:
        fill_rect(frame, 0, board, SIZE - 1, board + 1, WOOD)
    _draw_decor(frame, 0, PLANT)
    _draw_decor(frame, 1, LAMP)
    draw_field(frame, BOOKS_WALL, day_ground(view.day_local))
    return frame


_MARKDOWN_PAIR = re.compile(r"(?<!\w)(\*\*|__|\*|_)(?=\S)(.+?)(?<=\S)\1(?!\w)")
_UNITS = frozenset(
    {"h", "hr", "hrs", "min", "mins", "km", "mi", "m", "bpm", "wk", "wks", "d", "kg", "lb", "%"}
)
_TRAILING = ".,;:!?)"
_GLUE = "\u00a0"


def clean_summary(text: str) -> str:
    """Fold the summary to what the 5x7 face can draw.

    Typographic quotes and dashes become ASCII, accents are folded (NFKD), paired markdown
    emphasis markers (*x*, _x_, **x**, __x__) are removed, and any character the font has no
    glyph for, emoji included, is dropped rather than drawn as "?". Whitespace is collapsed.
    """
    text = normalize(text)
    text = normalize(unicodedata.normalize("NFKD", text))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _MARKDOWN_PAIR.sub(r"\2", text)
    text = "".join(ch if ch in BODY.glyphs else " " if ch.isspace() else "" for ch in text)
    return " ".join(text.split())


def fit_summary(text: str) -> str:
    """Clean the text; past SUMMARY_MAX_CHARS, cut at a word and end with a visible "..."."""
    text = clean_summary(text)
    if len(text) <= SUMMARY_MAX_CHARS:
        return text
    room = SUMMARY_MAX_CHARS - 3
    kept = text[:room]
    if text[room] != " " and " " in kept:
        kept = kept[: kept.rindex(" ")]
    return kept.rstrip(" .,;:") + "..."


def _is_number(word: str) -> bool:
    return word.lstrip("(")[:1].isdigit()


def _glue_numbers(words: list[str]) -> list[str]:
    """Join a number to its unit ("9.8 h") or to "of N" ("(10 of 12);") so they wrap as one."""
    groups: list[str] = []
    index = 0
    while index < len(words):
        take = 1
        if _is_number(words[index]):
            following = words[index + 1 : index + 3]
            if len(following) == 2 and following[0].lower() == "of" and _is_number(following[1]):
                take = 3
            elif following and following[0].rstrip(_TRAILING).lower() in _UNITS:
                take = 2
        group = words[index : index + take]
        if text_width(" ".join(group), BODY) <= LINE_WIDTH:
            groups.append(_GLUE.join(group))
        else:
            groups.extend(group)
        index += take
    return groups


def _glue_attribution(words: list[str]) -> list[str]:
    """Keep the dash after a quotation with the author's first name when they fit. A dash in
    the middle of a sentence is left alone."""
    units: list[str] = []
    index = 0
    while index < len(words):
        word = words[index]
        after_quote = index > 0 and words[index - 1].rstrip(".,;:").endswith('"')
        if word == "-" and after_quote and index + 1 < len(words):
            joined = f"- {words[index + 1]}"
            if text_width(joined, BODY) <= WORD_WIDTH:
                units.append(joined.replace(" ", _GLUE))
                index += 2
                continue
        units.append(word)
        index += 1
    return units


def _at_hyphens(units: list[str]) -> list[str]:
    """A hyphenated word too wide for a line breaks after its hyphens ("water-" / "drops"),
    never in the middle of a part."""
    out: list[str] = []
    for unit in units:
        if "-" in unit.strip("-") and text_width(unit.replace(_GLUE, " "), BODY) > WORD_WIDTH:
            parts = unit.replace("-", "-\n").split("\n")
            out.extend(part for part in parts if part)
        else:
            out.append(unit)
    return out


def wrap_lines(text: str) -> list[str]:
    """Greedy word wrap to LINE_WIDTH pixels.

    A number stays on the same line as its unit or its "of N" whenever the group fits a
    line, and an attribution dash stays with its name. A word alone on a line may run into
    the right margin (WORD_WIDTH), so "Yesterday's" is not cut before its "s"; a wider
    hyphenated word breaks after a hyphen, and only a wider unhyphenated word is ever split.
    """
    lines: list[str] = []
    current = ""
    for unit in _at_hyphens(_glue_attribution(_glue_numbers(text.split()))):
        word = unit.replace(_GLUE, " ")
        candidate = f"{current} {word}" if current else word
        if text_width(candidate, BODY) <= LINE_WIDTH:
            current = candidate
            continue
        if current:
            lines.append(current)
        bare = word.rstrip(_TRAILING)
        if bare and text_width(bare, BODY) <= WORD_WIDTH < text_width(word, BODY):
            word = bare
        while text_width(word, BODY) > WORD_WIDTH:
            cut = len(word) - 1
            while cut > 1 and text_width(word[:cut], BODY) > LINE_WIDTH:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        current = word
    if current:
        lines.append(current)
    return lines


def wrap_pages(text: str) -> list[tuple[str, ...]]:
    """The summary as pages of at most two lines, truncated first if it is over length."""
    lines = wrap_lines(fit_summary(text))
    per_page = len(SUMMARY_LINE_YS)
    return [tuple(lines[i : i + per_page]) for i in range(0, len(lines), per_page)]


_CLAUSE_END = ".,;:!?"


def summary_colors(text: str) -> list[Color]:
    """One colour per character of a wrapped summary (its lines joined by spaces).

    Max's rule (2026-10-05): the quotation in white, the author's name in gold, his own
    clause in a third colour. So when the text holds a double-quoted quotation, the
    quotation, marks included, is TEXT; a "- Author" after a closing mark is ATTRIBUTION to
    the end of its clause (the first of .,;:!? inclusive) or of the text; everything else,
    the data clause or the reflection around the quote, is VOICE. A text with no quotation,
    or an odd number of marks, is all TEXT as before. A space takes the colour of the
    character before it, so runs stay whole across the words they join.
    """
    colors = [TEXT] * len(text)
    if text.count('"') % 2 or '"' not in text:
        return colors
    colors = [VOICE] * len(text)
    index = 0
    while index < len(text):
        if text[index] != '"':
            index += 1
            continue
        close = text.index('"', index + 1)
        colors[index : close + 1] = [TEXT] * (close + 1 - index)
        index = close + 1
        dash = index
        while dash < len(text) and text[dash] in " .,;:":
            dash += 1
        if text[dash : dash + 2] != "- ":
            continue
        end = dash
        while end < len(text) and text[end] not in _CLAUSE_END:
            end += 1
        end = min(end + 1, len(text))
        colors[dash:end] = [ATTRIBUTION] * (end - dash)
        index = end
    for position in range(1, len(text)):
        if text[position] == " ":
            colors[position] = colors[position - 1]
    return colors


def line_colors(lines: list[str]) -> list[list[Color]]:
    """The colours of every character of every wrapped line, so a quotation that crosses a
    line or a page keeps its colour on each."""
    colors = summary_colors(" ".join(lines))
    out: list[list[Color]] = []
    start = 0
    for line in lines:
        out.append(colors[start : start + len(line)])
        start += len(line) + 1
    return out


def _draw_runs(frame: Frame, x: int, y: int, line: str, colors: list[Color]) -> None:
    """Draw a line as runs of one colour, each starting where the last left off, so the
    pixels are those of one draw of the whole line and a single-colour line is one call."""
    start = 0
    for end in range(1, len(line) + 1):
        if end == len(line) or colors[end] != colors[start]:
            x = draw_text(frame, x, y, line[start:end], colors[start], BODY)
            start = end


def _draw_page_pips(frame: Frame, page: int, pages: int, pip: Color = RING) -> None:
    width = pages * 3 - 1
    x0 = (SIZE - width) // 2
    for index in range(pages):
        color = TEXT if index == page else pip
        fill_rect(frame, x0 + index * 3, PAGE_PIP_Y, x0 + index * 3 + 1, PAGE_PIP_Y, color)


def render_books(view: DayView) -> Clip:
    """Books this year on a shelf, and the day's one-line summary in pages underneath."""
    base = _books_base(view)
    pip = theme_for(view).pip
    pages = wrap_pages(view.summary_line or NO_SUMMARY) or wrap_pages(NO_SUMMARY)
    colors = iter(line_colors([line for page in pages for line in page]))
    frames: list[Frame] = []
    for number, page in enumerate(pages):
        frame = base.copy()
        for line, y in zip(page, SUMMARY_LINE_YS, strict=False):
            _draw_runs(frame, LEFT, y, line, next(colors))
        if len(pages) > 1:
            _draw_page_pips(frame, number, len(pages), pip)
        frames.append(frame)
    if len(frames) == 1:
        return still(frames[0])
    return Clip(tuple(frames), (PAGE_MS,) * len(frames))
