"""The three rotation screens: Week, Today, Books + summary.

Each takes a DayView and returns a Clip. Every None in the view is drawn as a stated
fallback, so a view with nothing in it still yields three full frames. Rest is drawn plainly
and never as a miss: an empty dot is a quiet ring, a short night is a blue number, not a red one.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.render.font import (
    BODY,
    SMALL,
    draw_text,
    draw_text_centered,
    draw_text_right,
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
from app.render.view import DayView
from app.timeutil import from_utc_iso

LEFT = 2
RIGHT = 61
DOT_ROW_Y = 17
SLEEP_BAR_HOURS = 10
SCROLL_STEP_PX = 2
SCROLL_FRAME_MS = 40
SCROLL_HOLD_MS = 1200
SCROLL_GAP_PX = 24
SUMMARY_Y = 51
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


def _week_frame(view: DayView, now: datetime, tick: int) -> Frame:
    frame = new_frame()
    draw_text(frame, LEFT, 2, "WEEK", LABEL, SMALL)
    centres, radius = dot_layout(view.week_target)
    if view.week_dots is None:
        draw_text_right(frame, RIGHT, 2, "NO DATA", TEXT, SMALL)
    else:
        draw_text_right(frame, RIGHT, 2, f"{view.week_dots} OF {view.week_target}", TEXT, SMALL)
    for index, cx in enumerate(centres):
        if view.week_dots is None:
            draw_ring(frame, cx, DOT_ROW_Y, radius, RING, dashed=True)
        elif index < view.week_dots:
            draw_disc(frame, cx, DOT_ROW_Y, radius, dot_color(index))
        else:
            draw_ring(frame, cx, DOT_ROW_Y, radius, RING)

    draw_usage(frame, usage_state(view.claude, now, view.stale_hours), tick)

    if view.streak_weeks is None:
        draw_text_centered(frame, 52, "STREAK NO DATA", TEXT, SMALL)
    else:
        number = str(view.streak_weeks)
        tail = "WK STREAK"
        total = text_width(number, BODY) + 3 + text_width(tail, SMALL)
        x = (SIZE - total) // 2
        after = draw_text(frame, x, 50, number, GOLD if view.streak_weeks else TEXT, BODY)
        draw_text(frame, after + 2, 52, tail, LABEL, SMALL)
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


def _hours_label(hours: float) -> str:
    return f"{int(hours)}H" if hours == int(hours) else f"{hours:.1f}H"


def as_of_label(view: DayView) -> str:
    if view.as_of_utc is None:
        return "NO PUSH YET"
    local = from_utc_iso(view.as_of_utc).astimezone(ZoneInfo(view.home_tz))
    clock = local.strftime("%H:%M")
    if local.date().isoformat() != view.day_local:
        return f"AS OF {_WEEKDAYS[local.weekday()]} {clock}"
    return f"AS OF {clock}"


def render_today(view: DayView) -> Clip:
    """Last night's sleep against the target, today's dot, steps, and when the data is from."""
    frame = new_frame()
    day = date.fromisoformat(view.day_local)
    draw_text(frame, LEFT, 2, "TODAY", LABEL, SMALL)
    draw_text_right(frame, RIGHT, 2, f"{_WEEKDAYS[day.weekday()]} {day.day}", TEXT, SMALL)

    draw_bitmap(frame, LEFT, 11, MOON, VIOLET)
    target_x = LEFT + int(view.sleep_target_hours / SLEEP_BAR_HOURS * 60)
    fill_rect(frame, LEFT, 26, RIGHT, 28, TRACK)
    if view.sleep_hours is None:
        draw_text(frame, 16, 9, "--", TEXT, BODY, scale=2)
        draw_text(frame, LEFT, 32, "SLEEP", LABEL, SMALL)
        draw_text_right(frame, RIGHT, 32, "NO DATA", TEXT, SMALL)
    else:
        met = float(sleep_text(view.sleep_hours)) >= view.sleep_target_hours
        color = GREEN if met else SKY
        after = draw_text(frame, 16, 9, sleep_text(view.sleep_hours), color, BODY, scale=2)
        draw_text(frame, after + 1, 16, "h", color, BODY)
        filled = min(60, int(view.sleep_hours / SLEEP_BAR_HOURS * 60 + 0.5))
        if filled:
            fill_rect(frame, LEFT, 26, LEFT + filled - 1, 28, color)
        draw_text(frame, LEFT, 32, "SLEEP", LABEL, SMALL)
        draw_text_right(
            frame, RIGHT, 32, f"GOAL {_hours_label(view.sleep_target_hours)}", TEXT, SMALL
        )
    fill_rect(frame, target_x, 25, target_x, 29, WHITE)

    if view.today_dot is None:
        draw_ring(frame, 6, 44, 4, RING, dashed=True)
        draw_text(frame, 14, 42, "DOT NO DATA", TEXT, SMALL)
    elif view.today_dot:
        draw_disc(frame, 6, 44, 4, GOLD)
        draw_text(frame, 14, 42, "WORKOUT DONE", GOLD, SMALL)
    else:
        draw_ring(frame, 6, 44, 4, RING)
        draw_text(frame, 14, 42, "REST SO FAR", TEXT, SMALL)

    draw_text(frame, LEFT, 51, "STEPS", LABEL, SMALL)
    steps = "NO DATA" if view.steps is None else str(view.steps)
    draw_text_right(frame, RIGHT, 51, steps, TEXT, SMALL)
    draw_text(frame, LEFT, 58, as_of_label(view), LABEL, SMALL)
    return still(frame)


def _books_base(view: DayView) -> Frame:
    frame = new_frame()
    draw_text(frame, LEFT, 2, "BOOKS", LABEL, SMALL)
    draw_text_right(frame, RIGHT, 2, view.day_local[:4], TEXT, SMALL)
    if view.books_ytd is None:
        draw_text(frame, LEFT, 9, "--", TEXT, BODY, scale=2)
        draw_text_right(frame, RIGHT, 16, "NO DATA", TEXT, SMALL)
    else:
        after = draw_text(frame, LEFT, 9, str(view.books_ytd), GOLD, BODY, scale=2)
        draw_text(frame, after + 2, 16, f"of {view.books_target}", TEXT, BODY)

    slots = max(view.books_target, 1)
    spine = max(1, (60 - (slots - 1)) // slots)
    read = view.books_ytd or 0
    for index in range(slots):
        x0 = LEFT + index * (spine + 1)
        if x0 + spine - 1 > RIGHT:
            break
        top = 41 - _SPINE_HEIGHTS[index % len(_SPINE_HEIGHTS)]
        if index < read:
            color = SPINES[index % len(SPINES)]
            fill_rect(frame, x0, top, x0 + spine - 1, 40, color)
            fill_rect(frame, x0, top + 2, x0 + spine - 1, top + 2, dim(color, 0.45))
        else:
            fill_rect(frame, x0, top, x0 + spine - 1, 40, TRACK)
    fill_rect(frame, LEFT, 41, RIGHT, 42, WOOD)
    return frame


def render_books(view: DayView) -> Clip:
    """Books this year on a shelf, and the day's one-line summary scrolling underneath."""
    base = _books_base(view)
    line = view.summary_line or NO_SUMMARY
    width = text_width(line, BODY)
    if width <= SIZE - 2 * LEFT:
        frame = base.copy()
        draw_text_centered(frame, SUMMARY_Y, line, TEXT, BODY)
        return still(frame)

    period = width + SCROLL_GAP_PX
    frames: list[Frame] = []
    for offset in range(0, period, SCROLL_STEP_PX):
        frame = base.copy()
        draw_text(frame, LEFT - offset, SUMMARY_Y, line, TEXT, BODY)
        draw_text(frame, LEFT - offset + period, SUMMARY_Y, line, TEXT, BODY)
        frames.append(frame)
    durations = (SCROLL_HOLD_MS,) + (SCROLL_FRAME_MS,) * (len(frames) - 1)
    return Clip(tuple(frames), durations)


SCREEN_ORDER = ("week", "today", "books")


def render_rotation(view: DayView, now: datetime) -> dict[str, Clip]:
    """Every rotation screen, in rotation order."""
    return {
        "week": render_week(view, now),
        "today": render_today(view),
        "books": render_books(view),
    }
