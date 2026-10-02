"""The one entry point a scheduler needs: the rotation, in order, ready for any adapter.

No celebrations, no adapter, no database. Build the DayView elsewhere (view_db.view_from_db
or view.load_fixture) and pass "now" explicitly. Every clip returned holds at most
frame.MAX_CLIP_FRAMES frames (a test holds every fixture to it), so any of them can go to
the device as one animation.
"""

from __future__ import annotations

from datetime import datetime

from app.render.frame import Clip
from app.render.screens import render_books, render_today, render_week
from app.render.view import DayView

ROTATION_ORDER = ("week", "today", "books")


def render_screen(name: str, view: DayView, now: datetime) -> Clip:
    """One screen by name, so a renderer that raises costs the rotation only its own slot."""
    if name == "week":
        return render_week(view, now)
    if name == "today":
        return render_today(view, now)
    if name == "books":
        return render_books(view)
    raise KeyError(name)


def rotation_clips(view: DayView, now: datetime) -> list[tuple[str, Clip]]:
    """Week, Today, Books as (name, clip) pairs in rotation order. `now` is timezone-aware UTC."""
    return [(name, render_screen(name, view, now)) for name in ROTATION_ORDER]
