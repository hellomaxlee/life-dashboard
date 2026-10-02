"""The Claude usage bar on the Week screen (issue #2).

A thin bar under the dots: width proportional to the seven-day used percent, colour stepping
green, amber, red. The window is rolling, so the label says when it resets and never "this
week". A reading older than `stale_hours` gets a pulsing dot and its age; an absent reading
is drawn as "NO DATA" on a dashed track. Not a target, not a win.

"Now" is always a parameter. Setting USAGE_BAR_MUTANT_REMAINING=1 fills the bar from the
percent remaining instead of the percent used; it is the mutant that proves the pixel-width
test is a real gate.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import datetime

from app.render.font import SMALL, draw_text
from app.render.frame import Color, Frame
from app.render.palette import AMBER, GREEN, LABEL, RED, TEXT, TRACK, dim
from app.render.view import ClaudeUsage
from app.timeutil import from_utc_iso

BAR_LEFT = 2
BAR_WIDTH = 60
BAR_TOP = 28
BAR_HEIGHT = 3
LINE_1_Y = 33
LINE_2_Y = 40
STALE_DOT_X = 59
AMBER_FROM_PCT = 60.0
RED_FROM_PCT = 85.0
STALE_FRAMES = 16
STALE_FRAME_MS = 250
_PULSE = (1.0, 0.6, 0.25, 0.6)
_TEXT_SWAP_EVERY = 8


@dataclass(frozen=True)
class UsageState:
    has_data: bool
    used_pct: float | None = None
    fill_px: int = 0
    color: Color = GREEN
    reset_label: str = ""
    stale: bool = False
    age_label: str = ""


def mutant_remaining() -> bool:
    return os.environ.get("USAGE_BAR_MUTANT_REMAINING", "") == "1"


def fill_width(used_pct: float, track_px: int = BAR_WIDTH) -> int:
    """Pixels to fill, rounded half up. Any use above zero shows at least one pixel."""
    clamped = min(max(used_pct, 0.0), 100.0)
    if clamped <= 0:
        return 0
    return max(1, min(track_px, int(clamped / 100 * track_px + 0.5)))


def level_color(used_pct: float) -> Color:
    if used_pct >= RED_FROM_PCT:
        return RED
    if used_pct >= AMBER_FROM_PCT:
        return AMBER
    return GREEN


def reset_label(resets_at_utc: str | None, now: datetime) -> str:
    if resets_at_utc is None:
        return "NO RESET TIME"
    hours = (from_utc_iso(resets_at_utc) - now).total_seconds() / 3600
    if hours <= 0:
        return "RESET PASSED"
    if hours < 1:
        return "RESETS IN <1H"
    if hours < 24:
        return f"RESETS IN {math.ceil(hours)}H"
    return f"RESETS IN {int(hours / 24 + 0.5)}D"


def age_label(age_hours: float) -> str:
    if age_hours < 48:
        return f"SEEN {int(age_hours)}H AGO"
    return f"SEEN {int(age_hours // 24)}D AGO"


def usage_state(reading: ClaudeUsage, now: datetime, stale_hours: int) -> UsageState:
    if reading.used_pct is None:
        return UsageState(has_data=False)
    shown = 100.0 - reading.used_pct if mutant_remaining() else reading.used_pct
    stale, age = True, "AGE UNKNOWN"
    if reading.captured_at_utc is not None:
        age_hours = (now - from_utc_iso(reading.captured_at_utc)).total_seconds() / 3600
        stale = age_hours > stale_hours
        age = age_label(age_hours) if stale else ""
    return UsageState(
        has_data=True,
        used_pct=reading.used_pct,
        fill_px=fill_width(shown),
        color=level_color(reading.used_pct),
        reset_label=reset_label(reading.resets_at_utc, now),
        stale=stale,
        age_label=age,
    )


def percent_text(used_pct: float) -> str:
    return f"{int(min(max(used_pct, 0.0), 999.0) + 0.5)}%"


def draw_usage(frame: Frame, state: UsageState, tick: int = 0) -> None:
    """Draw the bar and its two label lines. `tick` is the frame number of a stale clip."""
    pixels = frame.load()
    for y in range(BAR_TOP, BAR_TOP + BAR_HEIGHT):
        for offset in range(BAR_WIDTH):
            x = BAR_LEFT + offset
            if not state.has_data:
                pixels[x, y] = TRACK if offset % 4 < 2 else (0, 0, 0)
            else:
                pixels[x, y] = state.color if offset < state.fill_px else TRACK

    after = draw_text(frame, BAR_LEFT, LINE_1_Y, "CLAUDE", LABEL, SMALL)
    if not state.has_data:
        draw_text(frame, after + 2, LINE_1_Y, "NO DATA", TEXT, SMALL)
        return
    draw_text(frame, after + 2, LINE_1_Y, percent_text(state.used_pct or 0.0), TEXT, SMALL)

    line_2 = state.reset_label
    if state.stale:
        if (tick // _TEXT_SWAP_EVERY) % 2 == 1:
            line_2 = state.age_label
        dot = dim(AMBER, _PULSE[tick % len(_PULSE)])
        for dy in range(3):
            for dx in range(3):
                pixels[STALE_DOT_X + dx, LINE_1_Y + 1 + dy] = dot
    draw_text(frame, BAR_LEFT, LINE_2_Y, line_2, AMBER if state.stale else LABEL, SMALL)
