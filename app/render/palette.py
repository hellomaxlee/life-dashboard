"""Colours chosen for LEDs: saturated, on true black, told apart by hue rather than by dimness.

A linear-PWM panel lifts dark tones (see gamma.py), so two greys that differ on a monitor can
look the same on the device. Hierarchy here comes from hue; dim tones are used only for
things that may fade without loss (tracks, empty rings).
"""

from __future__ import annotations

import colorsys

from app.render.frame import Color

BLACK: Color = (0, 0, 0)
WHITE: Color = (255, 255, 255)
TEXT: Color = (235, 235, 245)
LABEL: Color = (90, 150, 255)
TRACK: Color = (34, 36, 54)
RING: Color = (72, 76, 104)

CORAL: Color = (255, 90, 80)
GOLD: Color = (255, 200, 40)
TEAL: Color = (40, 220, 200)
GREEN: Color = (60, 220, 90)
AMBER: Color = (255, 170, 0)
RED: Color = (255, 60, 50)
VIOLET: Color = (170, 110, 255)
PINK: Color = (255, 90, 190)
SKY: Color = (80, 170, 255)
WOOD: Color = (150, 100, 50)

DOTS: tuple[Color, ...] = (CORAL, GOLD, TEAL)
SPINES: tuple[Color, ...] = (CORAL, GOLD, TEAL, VIOLET, PINK, SKY, GREEN)
CONFETTI: tuple[Color, ...] = (CORAL, GOLD, TEAL, VIOLET, PINK, SKY, GREEN, WHITE)


def dim(color: Color, factor: float) -> Color:
    return (int(color[0] * factor), int(color[1] * factor), int(color[2] * factor))


def hue(position: float, value: float = 1.0) -> Color:
    """A fully saturated colour from the wheel; position wraps at 1.0."""
    r, g, b = colorsys.hsv_to_rgb(position % 1.0, 1.0, value)
    return (int(r * 255), int(g * 255), int(b * 255))
