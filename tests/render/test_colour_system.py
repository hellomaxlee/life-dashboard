"""The colour system (notes.txt § Design, Mei-Lin Tsao, 2026-11-09): one family of meaning hues
that `hue()` walks, roles that stay apart on the panel, reference text in the secondary tone."""

from __future__ import annotations

import math

from app.render.gamma import led_lut
from app.render.palette import (
    AMBER,
    CORAL,
    GOLD,
    LABEL,
    RED,
    SECONDARY,
    SKY,
    WHEEL,
    YELLOW,
    hue,
    to_oklch,
)
from app.render.screens import render_today
from app.render.view import DayView

LUT = led_lut()


def on_panel(color):
    return tuple(LUT[channel] for channel in color)


def panel_hue(color) -> float:
    return math.degrees(to_oklch(on_panel(color))[2]) % 360


def hue_gap(a, b) -> float:
    gap = abs(panel_hue(a) - panel_hue(b)) % 360
    return min(gap, 360 - gap)


def test_hue_walks_the_dashboards_own_family():
    stops = [hue(index / len(WHEEL)) for index in range(len(WHEEL))]
    assert stops == list(WHEEL), "every stop is a palette colour, exactly"
    assert hue(1.25) == hue(0.25), "position wraps"
    for index in range(len(WHEEL)):
        a, b = WHEEL[index], WHEEL[(index + 1) % len(WHEEL)]
        middle = hue((index + 0.5) / len(WHEEL))
        floor = min(to_oklch(a)[1], to_oklch(b)[1])
        assert to_oklch(middle)[1] >= floor - 0.002, (a, b, middle, "no dip below a neighbour")
    assert all(to_oklch(hue(step / 96))[1] >= 0.1 for step in range(96)), "no step greys out"


def test_warning_and_trouble_stay_apart_from_the_meaning_hues_on_the_panel():
    assert hue_gap(AMBER, GOLD) >= 25, "the warning is orange, not a second gold"
    assert hue_gap(AMBER, CORAL) >= 25
    assert hue_gap(AMBER, YELLOW) >= 35
    coral, red = to_oklch(on_panel(CORAL)), to_oklch(on_panel(RED))
    assert red[1] - coral[1] >= 0.1 and coral[0] - red[0] >= 0.1, "red is deeper than coral"


def test_labels_recede_behind_values():
    label, sky = to_oklch(LABEL), to_oklch(SKY)
    assert label[1] < sky[1] - 0.03, "a label is quieter than the sleep value's blue"


def test_the_sleep_goal_is_reference_text():
    frame = render_today(DayView(day_local="2026-10-02", sleep_hours=8.0)).poster
    goal = {frame.getpixel((x, y)) for x in range(28, 64) for y in range(32, 37)} - {(0, 0, 0)}
    assert goal == {SECONDARY}
