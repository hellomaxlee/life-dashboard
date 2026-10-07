"""The Claude usage bar on the Week screen (issue #2).

A thin bar under the dots: width proportional to the seven-day used percent, colour stepping
green, amber, red at 60 and 85. Two one-pixel ticks above and below the track mark those
steps by position, so the level does not rest on hue alone. The window is rolling, so the
label says when it resets and never "this week". An absent reading is drawn as "NO DATA" on a
dashed track. Not a target, not a win.

Stale means the percent is known to be out of date: the reading is older than `stale_hours`,
or it has no capture time, or its reset time has already passed. A stale reading gets an
amber dot and an amber second line with its age. It is a still: the panel's loading cycle
(workflows/run-service.md section 15) made the old blink cost more than it told.

Nothing overstates. The label is the percent truncated and the fill is floored
(floor(0.412 * 60) = 24 px), with one pixel for any use above zero and the last pixel only
at 100. So 60.0 and 85.0 are the first values whose fill reaches their tick column, the
same values at which the colour steps.

Times that cannot be true are not printed: a reset more than seven days away (the window
is seven days long) is shown as no reset time, and a capture time in the future is an
unknown age, which is stale.

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
from app.render.palette import AMBER, GREEN, LABEL, RED, SECONDARY, TEXT, TRACK
from app.render.view import ClaudeUsage, valid_percent
from app.timeutil import from_utc_iso

BAR_LEFT = 2
BAR_WIDTH = 60
BAR_TOP = 42
BAR_HEIGHT = 3
LINE_1_Y = 47
LINE_2_Y = 54
STALE_DOT_X = 59
AMBER_FROM_PCT = 60.0
RED_FROM_PCT = 85.0
MAX_RESET_DAYS = 7


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
    """Pixels to fill, floored; at least one above zero, the last one only at 100."""
    clamped = min(max(used_pct, 0.0), 100.0)
    if clamped <= 0:
        return 0
    if clamped >= 100:
        return track_px
    return max(1, min(track_px - 1, int(clamped * track_px / 100 + 1e-9)))


def level_color(used_pct: float) -> Color:
    if used_pct >= RED_FROM_PCT:
        return RED
    if used_pct >= AMBER_FROM_PCT:
        return AMBER
    return GREEN


def tick_columns() -> tuple[int, int]:
    """The last column filled at exactly 60 and at exactly 85: where amber and red begin."""
    return (
        BAR_LEFT + fill_width(AMBER_FROM_PCT) - 1,
        BAR_LEFT + fill_width(RED_FROM_PCT) - 1,
    )


def reset_passed(resets_at_utc: str | None, now: datetime) -> bool:
    return resets_at_utc is not None and from_utc_iso(resets_at_utc) <= now


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
    if hours > MAX_RESET_DAYS * 24:
        return "NO RESET TIME"
    return f"RESETS IN {int(hours / 24 + 0.5)}D"


def age_label(age_hours: float) -> str:
    if age_hours < 1:
        return "SEEN <1H AGO"
    if age_hours < 48:
        return f"SEEN {int(age_hours)}H AGO"
    return f"SEEN {int(age_hours // 24)}D AGO"


def usage_state(reading: ClaudeUsage, now: datetime, stale_hours: int) -> UsageState:
    if not valid_percent(reading.used_pct):
        return UsageState(has_data=False)
    shown = 100.0 - reading.used_pct if mutant_remaining() else reading.used_pct
    stale, age = True, "AGE UNKNOWN"
    if reading.captured_at_utc is not None:
        age_hours = (now - from_utc_iso(reading.captured_at_utc)).total_seconds() / 3600
        if age_hours >= 0:
            stale = age_hours > stale_hours or reset_passed(reading.resets_at_utc, now)
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
    """Truncated, so 84.9 reads 84% on an amber bar. A trace of use reads <1%, not 0%."""
    clamped = min(max(used_pct, 0.0), 100.0)
    if 0 < clamped < 1:
        return "<1%"
    return f"{int(clamped)}%"


def draw_usage(frame: Frame, state: UsageState) -> None:
    """Draw the bar and its two label lines."""
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
    for x in tick_columns():
        pixels[x, BAR_TOP - 1] = TEXT
        pixels[x, BAR_TOP + BAR_HEIGHT] = TEXT
    draw_text(frame, after + 2, LINE_1_Y, percent_text(state.used_pct or 0.0), TEXT, SMALL)

    line_2 = state.reset_label
    if state.stale:
        line_2 = state.age_label
        for dy in range(3):
            for dx in range(3):
                pixels[STALE_DOT_X + dx, LINE_1_Y + 1 + dy] = AMBER
    draw_text(frame, BAR_LEFT, LINE_2_Y, line_2, AMBER if state.stale else SECONDARY, SMALL)
