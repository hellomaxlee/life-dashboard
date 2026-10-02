"""The three rotation screens: Week, Today, Books + summary.

Each takes a DayView and returns a Clip. Every None in the view is drawn as a stated
fallback, so a view with nothing in it still yields three full frames. Rest is drawn plainly
and never as a miss: an empty dot is a quiet ring, a short night is a blue number, not a red one.

The summary is shown as word-wrapped pages, two lines at a time, about two seconds each. A
pixel scroll of a 110-character line needs some 300 frames and the device holds fewer than
60, so pages are the form that reaches the panel as drawn.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from zoneinfo import ZoneInfo

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
    DOTS,
    GOLD,
    GREEN,
    LABEL,
    RING,
    SKY,
    SPINES,
    TEXT,
    TRACK,
    VIOLET,
    WHITE,
    WOOD,
    dim,
)
from app.render.usage import STALE_FRAME_MS, STALE_FRAMES, draw_usage, usage_state
from app.render.view import DayView, valid_count, valid_sleep_hours
from app.timeutil import from_utc_iso

LEFT = 2
RIGHT = 61
DOT_ROW_Y = 17
SLEEP_BAR_HOURS = 10
LINE_WIDTH = 60
PAGE_MS = 2000
SUMMARY_MAX_CHARS = 110
SUMMARY_LINE_YS = (45, 54)
PAGE_PIP_Y = 63
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
            y = 50 if font is BODY else 52
            after = draw_text(frame, x, y, number, GOLD if streak else TEXT, font)
            draw_text(frame, after + 2, 52, tail, LABEL, SMALL)
            return
    draw_text_centered(frame, 52, "MANY WK STREAK", LABEL, SMALL)


def _week_frame(view: DayView, now: datetime, tick: int) -> Frame:
    frame = new_frame()
    draw_text(frame, LEFT, 2, "WEEK", LABEL, SMALL)
    centres, radius = dot_layout(view.week_target)
    dots = _count(view.week_dots)
    streak = _count(view.streak_weeks)
    if dots is None:
        draw_text_right(frame, RIGHT, 2, "NO DATA", TEXT, SMALL)
    else:
        draw_text_right(frame, RIGHT, 2, f"{dots} OF {view.week_target}", TEXT, SMALL)
    for index, cx in enumerate(centres):
        if dots is None:
            draw_ring(frame, cx, DOT_ROW_Y, radius, RING, dashed=True)
        elif index < dots:
            draw_disc(frame, cx, DOT_ROW_Y, radius, dot_color(index))
        else:
            draw_ring(frame, cx, DOT_ROW_Y, radius, RING)

    draw_usage(frame, usage_state(view.claude, now, view.stale_hours), tick)

    if streak is None:
        draw_text_centered(frame, 52, "STREAK NO DATA", TEXT, SMALL)
    else:
        _draw_streak(frame, streak)
    return frame


def render_week(view: DayView, now: datetime) -> Clip:
    """Three dots, the Claude usage bar, the weeks-hit streak. Animated only when stale."""
    state = usage_state(view.claude, now, view.stale_hours)
    if not state.stale:
        return still(_week_frame(view, now, 0))
    frames = tuple(_week_frame(view, now, tick) for tick in range(STALE_FRAMES))
    return Clip(frames, (STALE_FRAME_MS,) * STALE_FRAMES)


def sleep_text(hours: float) -> str:
    """One decimal, truncated, so the frame never shows 7.0 for a night short of 7."""
    return f"{int(hours * 10 + 1e-6) / 10:.1f}"


def sleep_met(view: DayView) -> bool:
    """The sleep win as the frame shows it: the displayed number against the target."""
    if not valid_sleep_hours(view.sleep_hours):
        return False
    return float(sleep_text(view.sleep_hours)) >= view.sleep_target_hours


def sleep_fill(hours: float) -> int:
    """Bar pixels for a night, floored, so a night short of the goal never touches its tick."""
    return min(60, int(hours * 60 / SLEEP_BAR_HOURS + 1e-9))


def _hours_label(hours: float) -> str:
    return f"{int(hours)}H" if hours == int(hours) else f"{hours:.1f}H"


def _has_day_data(view: DayView) -> bool:
    return (
        valid_sleep_hours(view.sleep_hours)
        or _count(view.steps) is not None
        or view.today_dot is not None
    )


def as_of_label(view: DayView, now: datetime | None = None) -> str | None:
    """When the day's data is from, or None to leave the line out.

    Same day: the clock. One to six days back: weekday and clock, which is unambiguous within
    a week. Older: whole days ("AS OF 8D AGO"), so an old push never reads as recent. A push
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
    clock = local.strftime("%H:%M")
    if days_back == 0:
        return f"AS OF {clock}"
    if days_back <= 6:
        return f"AS OF {_WEEKDAYS[local.weekday()]} {clock}"
    return f"AS OF {days_back}D AGO"


def render_today(view: DayView, now: datetime | None = None) -> Clip:
    """Last night's sleep against the target, today's dot, steps, and when the data is from."""
    frame = new_frame()
    day = date.fromisoformat(view.day_local)
    draw_text(frame, LEFT, 2, "TODAY", LABEL, SMALL)
    draw_text_right(frame, RIGHT, 2, f"{_WEEKDAYS[day.weekday()]} {day.day}", TEXT, SMALL)

    draw_bitmap(frame, LEFT, 11, MOON, VIOLET)
    target_x = LEFT + int(view.sleep_target_hours / SLEEP_BAR_HOURS * 60)
    fill_rect(frame, LEFT, 26, RIGHT, 28, TRACK)
    if not valid_sleep_hours(view.sleep_hours):
        draw_text(frame, 16, 9, "--", TEXT, BODY, scale=2)
        draw_text(frame, LEFT, 32, "SLEEP", LABEL, SMALL)
        draw_text_right(frame, RIGHT, 32, "NO DATA", TEXT, SMALL)
    else:
        color = GREEN if sleep_met(view) else SKY
        number = sleep_text(view.sleep_hours)
        unit_room = 3 + text_width("h", BODY)
        x = min(16, RIGHT + 1 - text_width(number, BODY, 2) - unit_room)
        after = draw_text(frame, x, 9, number, color, BODY, scale=2)
        draw_text(frame, after + 1, 16, "h", color, BODY)
        filled = sleep_fill(view.sleep_hours)
        if filled:
            fill_rect(frame, LEFT, 26, LEFT + filled - 1, 28, color)
        draw_text(frame, LEFT, 32, "SLEEP", LABEL, SMALL)
        draw_text_right(
            frame, RIGHT, 32, f"GOAL {_hours_label(view.sleep_target_hours)}", TEXT, SMALL
        )
    fill_rect(frame, target_x, 25, target_x, 29, WHITE)

    if view.today_dot is None:
        draw_ring(frame, 6, 44, 4, RING, dashed=True)
        draw_text(frame, 13, 42, "DOT NO DATA", TEXT, SMALL)
    elif view.today_dot:
        draw_disc(frame, 6, 44, 4, GOLD)
        draw_text(frame, 13, 42, "WORKOUT DONE", GOLD, SMALL)
    else:
        draw_ring(frame, 6, 44, 4, RING)
        draw_text(frame, 13, 42, "NO DOT YET", TEXT, SMALL)

    draw_text(frame, LEFT, 51, "STEPS", LABEL, SMALL)
    steps = "NO DATA" if _count(view.steps) is None else str(view.steps)
    draw_text_right(frame, RIGHT, 51, steps, TEXT, SMALL)
    as_of = as_of_label(view, now)
    if as_of is not None:
        draw_text(frame, LEFT, 58, as_of, LABEL, SMALL)
    return still(frame)


def _draw_book_count(frame: Frame, count: int, target: int) -> None:
    """The big count and "of N". Steps down in size until the whole line fits the frame."""
    number, tail = str(count), f"of {target}"
    room = RIGHT - LEFT + 1
    big = text_width(number, BODY, 2)
    if big + 4 + text_width(tail, BODY) <= room:
        after = draw_text(frame, LEFT, 9, number, GOLD, BODY, scale=2)
        draw_text(frame, after + 2, 16, tail, TEXT, BODY)
    elif big + 2 + text_width(tail, SMALL) <= room:
        after = draw_text(frame, LEFT, 9, number, GOLD, BODY, scale=2)
        draw_text(frame, after, 18, tail, TEXT, SMALL)
    elif text_width(number, BODY) + 2 + text_width(tail, SMALL) <= room:
        after = draw_text(frame, LEFT, 16, number, GOLD, BODY)
        draw_text(frame, after + 1, 18, tail, TEXT, SMALL)
    else:
        draw_text(frame, LEFT, 16, "MANY", GOLD, BODY)


def _books_base(view: DayView) -> Frame:
    frame = new_frame()
    draw_text(frame, LEFT, 2, "BOOKS", LABEL, SMALL)
    draw_text_right(frame, RIGHT, 2, view.day_local[:4], TEXT, SMALL)
    read = _count(view.books_ytd)
    if read is None:
        draw_text(frame, LEFT, 9, "--", TEXT, BODY, scale=2)
        draw_text_right(frame, RIGHT, 16, "NO DATA", TEXT, SMALL)
    else:
        _draw_book_count(frame, read, view.books_target)

    slots = max(view.books_target, 1)
    spine = max(1, (60 - (slots - 1)) // slots)
    for index in range(slots):
        x0 = LEFT + index * (spine + 1)
        if x0 + spine - 1 > RIGHT:
            break
        top = 41 - _SPINE_HEIGHTS[index % len(_SPINE_HEIGHTS)]
        if index < (read or 0):
            color = SPINES[index % len(SPINES)]
            fill_rect(frame, x0, top, x0 + spine - 1, 40, color)
            fill_rect(frame, x0, top + 2, x0 + spine - 1, top + 2, dim(color, 0.45))
        else:
            fill_rect(frame, x0, top, x0 + spine - 1, 40, TRACK)
    fill_rect(frame, LEFT, 41, RIGHT, 42, WOOD)
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
    """Clean the text; past 110 characters, cut at a word and end with a visible "..."."""
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


def wrap_lines(text: str) -> list[str]:
    """Greedy word wrap to LINE_WIDTH pixels.

    Only a word wider than a line is ever split, and a number stays on the same line as its
    unit or its "of N" whenever the group fits a line.
    """
    lines: list[str] = []
    current = ""
    for unit in _glue_numbers(text.split()):
        word = unit.replace(_GLUE, " ")
        candidate = f"{current} {word}" if current else word
        if text_width(candidate, BODY) <= LINE_WIDTH:
            current = candidate
            continue
        if current:
            lines.append(current)
        while text_width(word, BODY) > LINE_WIDTH:
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


def _draw_page_pips(frame: Frame, page: int, pages: int) -> None:
    width = pages * 3 - 1
    x0 = (SIZE - width) // 2
    for index in range(pages):
        color = TEXT if index == page else RING
        fill_rect(frame, x0 + index * 3, PAGE_PIP_Y, x0 + index * 3 + 1, PAGE_PIP_Y, color)


def render_books(view: DayView) -> Clip:
    """Books this year on a shelf, and the day's one-line summary in pages underneath."""
    base = _books_base(view)
    pages = wrap_pages(view.summary_line or NO_SUMMARY) or [(NO_SUMMARY,)]
    frames: list[Frame] = []
    for number, page in enumerate(pages):
        frame = base.copy()
        for line, y in zip(page, SUMMARY_LINE_YS, strict=False):
            draw_text(frame, LEFT, y, line, TEXT, BODY)
        if len(pages) > 1:
            _draw_page_pips(frame, number, len(pages))
        frames.append(frame)
    if len(frames) == 1:
        return still(frames[0])
    return Clip(tuple(frames), (PAGE_MS,) * len(frames))
