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
# When a number is from or until, not what it is: "RESETS IN 2D", "AS OF 18:10".
SECONDARY: Color = (150, 140, 190)
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

# The city panel. Weather: rain is the one blue that is a quantity, clouds are light and dark.
RAIN: Color = (40, 120, 255)
CLOUD: Color = (205, 210, 225)
CLOUD_DARK: Color = (120, 126, 150)
MOONLIGHT: Color = (235, 225, 160)
# MTA bullet colours. The brown (#996633) is lifted so it reads on LEDs; the rest are the
# standard values. The bus blue is brighter than the A/C/E blue so a bus is never an A train.
MTA_BLUE: Color = (0, 57, 166)
MTA_ORANGE: Color = (255, 99, 25)
MTA_LIGHT_GREEN: Color = (108, 190, 69)
MTA_BROWN: Color = (190, 128, 64)
MTA_GREY: Color = (167, 169, 172)
MTA_YELLOW: Color = (252, 204, 10)
MTA_RED: Color = (238, 53, 46)
MTA_GREEN: Color = (0, 147, 60)
MTA_PURPLE: Color = (185, 51, 173)
BUS_BLUE: Color = (20, 110, 235)
BULLET_INK: Color = (0, 0, 0)

DOTS: tuple[Color, ...] = (CORAL, GOLD, TEAL)
SPINES: tuple[Color, ...] = (CORAL, GOLD, TEAL, VIOLET, PINK, SKY, GREEN)
CONFETTI: tuple[Color, ...] = (CORAL, GOLD, TEAL, VIOLET, PINK, SKY, GREEN, WHITE)


def dim(color: Color, factor: float) -> Color:
    return (int(color[0] * factor), int(color[1] * factor), int(color[2] * factor))


def hue(position: float, value: float = 1.0) -> Color:
    """A fully saturated colour from the wheel; position wraps at 1.0."""
    r, g, b = colorsys.hsv_to_rgb(position % 1.0, 1.0, value)
    return (int(r * 255), int(g * 255), int(b * 255))
