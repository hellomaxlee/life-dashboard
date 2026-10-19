"""The month's quiet carry-through: the feature's hue on the structural tones of every screen.

Max (2026-10-19): each month has "a general theme that is subtle but carries through",
for variety against sensory adaptation, while "the more directly apparent monthly change is
the monthly feature". So the Month screen stays the loud change and the other screens take
one accent from the stored feature's palette on the tones that may fade without loss
(palette.py): the sleep bar's track, the empty rings, the inactive page pip, the rain gauges,
plus one more colour in the confetti. Text, headers, numbers and the plate are untouched.

The accent is the first palette entry with a hue worth naming (HSV saturation at least
MIN_ACCENT_SATURATION), so it is the title's colour unless the title is near-white. A tinted
tone takes that hue at ACCENT_SATURATION and the stock tone's luma as the panel shows it:
`tint` walks HSV value up until the tone, through the LED curve at every day brightness in
BRIGHTNESSES, would read brighter than the stock tone, and stops one step short. So a tint is
never brighter than what it replaces and stays as far under the text as the stock tones do.
With no feature, or a palette of greys, `theme_for` returns STOCK: the very palette tones, so
every frame is byte-identical to a render that never heard of a theme.
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass
from functools import lru_cache

from app.render.frame import Color
from app.render.gamma import led_lut
from app.render.palette import CONFETTI, RING, TRACK
from app.render.view import DayView

ACCENT_SATURATION = 0.5
MIN_ACCENT_SATURATION = 0.2
BRIGHTNESSES = (1.0, 0.5, 0.25)


@dataclass(frozen=True)
class MonthTheme:
    accent: Color | None
    track: Color
    ring: Color
    pip: Color
    confetti: tuple[Color, ...]


STOCK = MonthTheme(None, TRACK, RING, RING, CONFETTI)


def luma(color: Color) -> float:
    return 0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2]


@lru_cache(maxsize=8)
def _lut(brightness: float) -> list[int]:
    return led_lut(brightness=brightness)


def on_panel(color: Color, brightness: float = 1.0) -> Color:
    lut = _lut(brightness)
    return (lut[color[0]], lut[color[1]], lut[color[2]])


def saturation(color: Color) -> float:
    return colorsys.rgb_to_hsv(*(channel / 255 for channel in color))[1]


def accent_of(palette: tuple[Color, ...]) -> Color | None:
    """The first palette colour with a hue worth naming, or None for a palette of greys."""
    for color in palette:
        if saturation(color) >= MIN_ACCENT_SATURATION:
            return color
    return None


def _hsv(hue: float, sat: float, value: int) -> Color:
    r, g, b = colorsys.hsv_to_rgb(hue, sat, value / 255)
    return (round(r * 255), round(g * 255), round(b * 255))


def _under(candidate: Color, stock: Color) -> bool:
    return all(
        luma(on_panel(candidate, brightness)) <= luma(on_panel(stock, brightness))
        for brightness in BRIGHTNESSES
    )


def tint(accent: Color, stock: Color) -> Color:
    """`stock` recoloured to the accent's hue at ACCENT_SATURATION, as bright as it can be
    without reading brighter than `stock` through the LED curve at any day brightness."""
    hue = colorsys.rgb_to_hsv(*(channel / 255 for channel in accent))[0]
    tone = _hsv(hue, ACCENT_SATURATION, 0)
    for value in range(1, 256):
        candidate = _hsv(hue, ACCENT_SATURATION, value)
        if not _under(candidate, stock):
            break
        tone = candidate
    return tone


@lru_cache(maxsize=16)
def theme_of(palette: tuple[Color, ...]) -> MonthTheme:
    accent = accent_of(palette)
    if accent is None:
        return STOCK
    ring = tint(accent, RING)
    return MonthTheme(accent, tint(accent, TRACK), ring, ring, (*CONFETTI, accent))


def theme_for(view: DayView) -> MonthTheme:
    """The view's month theme; STOCK when the view carries no feature."""
    if view.month_feature is None:
        return STOCK
    return theme_of(tuple(view.month_feature.palette))
