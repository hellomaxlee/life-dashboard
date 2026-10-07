"""The one entry point a scheduler needs: the rotation, in order, ready for any adapter.

No adapter, no database. Build the DayView elsewhere (view_db.view_from_db or
view.load_fixture) and pass "now" explicitly. Every clip returned holds at most
frame.MAX_CLIP_FRAMES frames (a test holds every fixture to it), so any of them can go to
the device as one animation.

The sequence is day, the day's city, week, month, year, then the wins: Today, City, Week,
Month, Books, one sparkle per small win the day earned, and the week-complete party once the
week's target is met, repeated. `sequence_names` is the one definition; the device job,
/pixoo, /preview and the tools all read it.
A still screen holds for the dwell (`device.screen_seconds`). A paged screen (City's weather,
lines and alert pages, Books' summary, Month's plate and note, and both celebrations, which
are steps of a second each) holds until it has paged through exactly once, however long or
short that is. Anything animated faster than a page holds for whole plays: as many as cover
WIN_HOLD_MS for a win or the dwell for a screen.
"""

from __future__ import annotations

from datetime import datetime

from app.render.celebrate import WIN_ORDER, earned_wins, party_clip, sparkle_clip, week_complete
from app.render.city import render_city
from app.render.frame import Clip, still
from app.render.month import render_month
from app.render.screens import render_books, render_today, render_week
from app.render.view import DayView

ROTATION_ORDER = ("today", "city", "week", "month", "books")
WIN_PREFIX = "win-"
WIN_NAMES = tuple(f"{WIN_PREFIX}{win}" for win in WIN_ORDER)
PARTY = "party"
SCREEN_NAMES = (*ROTATION_ORDER, *WIN_NAMES, PARTY)
ONE_PASS = ("city", "month", "books", PARTY)
WIN_HOLD_MS = 4000
PAGED_FRAME_MS = 1000


def sequence_names(view: DayView) -> tuple[str, ...]:
    """Today, City, Week, Month, Books, the day's earned small wins, the party if the week is
    done."""
    wins = tuple(f"{WIN_PREFIX}{win}" for win in earned_wins(view))
    party = (PARTY,) if week_complete(view) else ()
    return (*ROTATION_ORDER, *wins, *party)


def party_for(view: DayView) -> Clip:
    """The party with the week's stored count; for a week not yet done, the target twice,
    which is the sample `celebrate.celebrations_for` shows."""
    count = view.week_dots if week_complete(view) else view.week_target
    return party_clip(count, view.week_target)


def render_screen(name: str, view: DayView, now: datetime) -> Clip:
    """One screen by name, so a renderer that raises costs the rotation only its own slot."""
    if name == "today":
        return render_today(view, now)
    if name == "city":
        return render_city(view, now)
    if name == "week":
        return render_week(view, now)
    if name == "month":
        return render_month(view, now)
    if name == "books":
        return render_books(view)
    if name in WIN_NAMES:
        win = name.removeprefix(WIN_PREFIX)
        return sparkle_clip(win, dot=view.week_dots if win == "workout" else None)
    if name == PARTY:
        return party_for(view)
    raise KeyError(name)


def hold_ms(name: str, clip: Clip, dwell_s: int) -> int:
    """How long a slot keeps the display. A still: the dwell. City, Books, Month and the party:
    one pass through. Anything else animated: whole plays, as many as it takes to cover
    WIN_HOLD_MS for a sparkle or the dwell for a screen, so a clip is never replaced part way
    through."""
    if not clip.animated:
        return dwell_s * 1000
    if name in ONE_PASS:
        return clip.total_ms
    cover_ms = WIN_HOLD_MS if name in WIN_NAMES else dwell_s * 1000
    return max(1, -(-cover_ms // clip.total_ms)) * clip.total_ms


def device_parts(clip: Clip) -> list[Clip]:
    """What to send for one slot. A paged clip (every frame up for PAGED_FRAME_MS or more)
    goes as one still per page, each held for its own time, so the panel shows every page
    exactly once and never loops them; anything faster goes whole, as an animation."""
    if clip.animated and min(clip.durations_ms) >= PAGED_FRAME_MS:
        return [still(frame, ms) for frame, ms in zip(clip.frames, clip.durations_ms, strict=True)]
    return [clip]


def rotation_clips(view: DayView, now: datetime) -> list[tuple[str, Clip]]:
    """Today, City, Week, Month, Books as (name, clip) pairs in rotation order. `now` is aware
    UTC."""
    return [(name, render_screen(name, view, now)) for name in ROTATION_ORDER]


def rotation_sequence(view: DayView, now: datetime, dwell_s: int) -> list[tuple[str, Clip, int]]:
    """The whole repeating sequence as (name, clip, hold in ms)."""
    slots = []
    for name in sequence_names(view):
        clip = render_screen(name, view, now)
        slots.append((name, clip, hold_ms(name, clip, dwell_s)))
    return slots
