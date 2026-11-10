"""Colours chosen for LEDs: saturated, on true black, told apart by hue rather than by dimness.

A linear-PWM panel lifts dark tones (see gamma.py), so two greys that differ on a monitor can
look the same on the device. Hierarchy here comes from hue; dim tones are used only for
things that may fade without loss (tracks, empty rings).

The colour system (notes.txt § Design, Mei-Lin Tsao, 2026-11-09): every colour has a role.
Vibrant is for what is read first (numbers, wins, celebrations), subtle for structure and
labels. The meaning hues are one family, and `hue()` walks that family (WHEEL, interpolated in
OKLCH) instead of the raw HSV wheel, whose primaries glare and sink unevenly on the panel, so a
celebration's colours are the dashboard's own. An equal-lightness OKLCH ring was tried and
rejected: mid-lightness colours have no channel near zero and the panel washes them to pastel.
"""

from __future__ import annotations

import math

from app.render.frame import Color

BLACK: Color = (0, 0, 0)
WHITE: Color = (255, 255, 255)
TEXT: Color = (235, 235, 245)
# Field labels recede: a slate blue, quieter than SKY, which is a value (sleep under the band).
LABEL: Color = (110, 145, 215)
# When a number is from or until, not what it is: "RESETS IN 2D", "AS OF 18:10".
SECONDARY: Color = (150, 140, 190)
TRACK: Color = (34, 36, 54)
RING: Color = (72, 76, 104)

CORAL: Color = (255, 90, 80)
GOLD: Color = (255, 200, 40)
TEAL: Color = (40, 220, 200)
GREEN: Color = (60, 220, 90)
# A warning (stale, planned work, usage over 60%): orange on the panel, (255, 151, 0) through
# the LED curve at hue 64, midway between GOLD (97) and CORAL (25). The old (255, 170, 0) showed
# as (255, 212, 0), a second gold, and (255, 135, 20) still read yellow in small text.
AMBER: Color = (255, 80, 0)
# A night over the sleep band: plainly yellow, greener than GOLD so it is not the workout's.
YELLOW: Color = (255, 235, 0)
# Trouble (delays, usage over 85%): pure red, the one red the panel does not lift; CORAL shows
# as a light salmon, so the two part by chroma and lightness. Any nonzero green or blue is
# lifted hard (10 shows as 59) and turns a red pink.
RED: Color = (255, 0, 0)
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
# The family `hue()` walks, in hue order.
WHEEL: tuple[Color, ...] = (CORAL, AMBER, GOLD, GREEN, TEAL, SKY, VIOLET, PINK)

# The header word of each rotation screen, one accent each, so the rotation reads as distinct
# places. City keeps LABEL and Month its feature palette; the right-hand stamp stays TEXT.
HEADERS: dict[str, Color] = {"today": VIOLET, "week": CORAL, "books": GOLD}

# The summary's three voices (Max, 2026-10-05: the quotation in white, the author in gold, his
# own clause in a third colour): a quotation, marks included, in TEXT; its "- Author" in GOLD;
# the words around it in VOICE, a cream that through the panel curve sits at (255, 234, 181),
# midway in blue between TEXT's (246, 246, 250) and GOLD's (255, 228, 110), told apart by
# warmth, not by dimness. (255, 230, 170) was tried first and merged with TEXT at 1x. A
# summary with no quotation is all TEXT.
VOICE: Color = (255, 212, 120)
ATTRIBUTION: Color = GOLD

# An unread shelf slot is this fraction of the spine colour it will take when read. Through
# the panel curve 0.18 keeps every tint under half the lit spine's luma (worst 0.46) with its
# hue intact (HSV saturation at least 0.32), at about the weight the old grey track had.
SHELF_TINT = 0.18


def dim(color: Color, factor: float) -> Color:
    return (int(color[0] * factor), int(color[1] * factor), int(color[2] * factor))


def shelf_tint(color: Color) -> Color:
    return dim(color, SHELF_TINT)


def _to_linear(channel: int) -> float:
    c = channel / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _to_srgb(x: float) -> int:
    x = min(1.0, max(0.0, x))
    return round(255 * (12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055))


def to_oklch(color: Color) -> tuple[float, float, float]:
    r, g, b = (_to_linear(c) for c in color)
    l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    lightness = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    b2 = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return lightness, math.hypot(a, b2), math.atan2(b2, a)


def from_oklch(lightness: float, chroma: float, angle: float) -> Color:
    a, b = chroma * math.cos(angle), chroma * math.sin(angle)
    l_ = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (
        _to_srgb(4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_),
        _to_srgb(-1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_),
        _to_srgb(-0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_),
    )


def hue(position: float, value: float = 1.0) -> Color:
    """A colour from the dashboard's own family; position wraps at 1.0. Between two WHEEL
    stops the colour is interpolated in OKLCH, the hue the short way round, so a midpoint
    keeps its neighbours' chroma instead of greying out as an RGB blend would."""
    x = (position % 1.0) * len(WHEEL)
    index, t = int(x) % len(WHEEL), x - int(x)
    la, ca, ha = to_oklch(WHEEL[index])
    lb, cb, hb = to_oklch(WHEEL[(index + 1) % len(WHEEL)])
    turn = (hb - ha + math.pi) % (2 * math.pi) - math.pi
    color = from_oklch(la + (lb - la) * t, ca + (cb - ca) * t, ha + turn * t)
    return dim(color, value) if value != 1.0 else color
