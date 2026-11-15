from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.config import Settings
from app.render.frame import Clip
from app.render.rotation import rotation_clips
from app.render.view import DayView, load_fixture

DAYS = Path(__file__).resolve().parent.parent.parent / "fixtures" / "days"
SNAPSHOTS = Path(__file__).resolve().parent / "snapshots"
MONTHS = DAYS.parent / "month"
SAMPLE_FEATURE = "sample-2026-10"
ORDER = ["today", "city", "week", "month", "books"]

WEEK_41 = "train__all-sources__alive__base"
NO_READING = "rest__sleep-missing__broken-last-week__base"
STALE = "travel__health-delayed__alive__off"
RED_91 = "race-week__workout-without-hr__never-started__peak"
WEEK_COMPLETE = "train__all-sources__alive__peak"
REQUIRED_COMBOS = (WEEK_41, NO_READING, STALE, RED_91, WEEK_COMPLETE)
COMBOS = tuple(sorted(path.stem for path in DAYS.glob("*.json")))


def load(combo: str, settings: Settings) -> tuple[DayView, datetime]:
    return load_fixture(DAYS / f"{combo}.json", settings)


def rotation(view: DayView, now: datetime) -> dict[str, Clip]:
    return dict(rotation_clips(view, now))


def without(frame, tones):
    """A copy of `frame` with every pixel of the scenery `tones` put back to black, for a test
    that is about the words and marks, not the scene behind them."""
    out = frame.copy()
    pixels = out.load()
    for y in range(out.height):
        for x in range(out.width):
            if pixels[x, y] in tones:
                pixels[x, y] = (0, 0, 0)
    return out


def record_text(monkeypatch) -> list[tuple[str, int, int, str, int]]:
    """Record every string a renderer draws as (text, x, y, font name, scale), still drawing it."""
    from app.render import celebrate, city, font, month, screens, usage

    drawn: list[tuple[str, int, int, str, int]] = []
    original = font.draw_text

    def recording(frame, x, y, text, color, font_=font.SMALL, scale=1, exact=False):
        drawn.append((text, x, y, font_.name, scale))
        return original(frame, x, y, text, color, font_, scale, exact)

    for module in (font, screens, usage, celebrate, month, city):
        monkeypatch.setattr(module, "draw_text", recording)
    return drawn
