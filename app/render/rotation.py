"""The one entry point a scheduler needs: the rotation, in order, ready for any adapter.

No adapter, no database. Build the DayView elsewhere (view_db.view_from_db or
view.load_fixture) and pass "now" explicitly. Every clip returned holds at most
frame.MAX_CLIP_FRAMES frames (a test holds every fixture to it), so any of them can go to
the device as one animation.

The sequence is Week, Today, one sparkle per small win the day earned, Books, repeated.
A still screen holds for the dwell (`device.screen_seconds`). Anything animated holds for
whole plays: Books until its summary has paged through (again, if one pass is shorter than
the dwell), a sparkle until it has been up for WIN_HOLD_MS.
"""

from __future__ import annotations

from datetime import datetime

from app.render.celebrate import WIN_ORDER, earned_wins, sparkle_clip
from app.render.frame import Clip
from app.render.screens import render_books, render_today, render_week
from app.render.view import DayView

ROTATION_ORDER = ("week", "today", "books")
WIN_PREFIX = "win-"
WIN_NAMES = tuple(f"{WIN_PREFIX}{win}" for win in WIN_ORDER)
WIN_HOLD_MS = 4000


def sequence_names(view: DayView) -> tuple[str, ...]:
    """Week, Today, the day's earned wins, Books."""
    wins = tuple(f"{WIN_PREFIX}{win}" for win in earned_wins(view))
    return ("week", "today", *wins, "books")


def render_screen(name: str, view: DayView, now: datetime) -> Clip:
    """One screen by name, so a renderer that raises costs the rotation only its own slot."""
    if name == "week":
        return render_week(view, now)
    if name == "today":
        return render_today(view, now)
    if name == "books":
        return render_books(view)
    if name in WIN_NAMES:
        return sparkle_clip(name.removeprefix(WIN_PREFIX))
    raise KeyError(name)


def hold_ms(name: str, clip: Clip, dwell_s: int) -> int:
    """How long a slot keeps the display. A still: the dwell. Anything animated: whole plays,
    as many as it takes to cover WIN_HOLD_MS for a sparkle or the dwell for a screen, so a
    clip is never replaced part way through."""
    if not clip.animated:
        return dwell_s * 1000
    cover_ms = WIN_HOLD_MS if name in WIN_NAMES else dwell_s * 1000
    return max(1, -(-cover_ms // clip.total_ms)) * clip.total_ms


def rotation_clips(view: DayView, now: datetime) -> list[tuple[str, Clip]]:
    """Week, Today, Books as (name, clip) pairs in rotation order. `now` is timezone-aware UTC."""
    return [(name, render_screen(name, view, now)) for name in ROTATION_ORDER]


def rotation_sequence(view: DayView, now: datetime, dwell_s: int) -> list[tuple[str, Clip, int]]:
    """The whole repeating sequence as (name, clip, hold in ms)."""
    slots = []
    for name in sequence_names(view):
        clip = render_screen(name, view, now)
        slots.append((name, clip, hold_ms(name, clip, dwell_s)))
    return slots
