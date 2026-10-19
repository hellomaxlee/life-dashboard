"""Gates for the month's quiet carry-through (Max, 2026-10-19: a subtle theme per month, the
feature staying the loud change).

(a) no feature: every frame is byte-identical to the renderer before the theme existed,
    pinned in snapshots/no_feature_digests.json from the pre-theme tree;
(b) a feature tints exactly the structural pixels (track, ring, pip) and nothing else;
(c) every colour a feature may legally carry yields tones no brighter than the stock ones
    through the LED curve at every day brightness, and well under the text.
"""

from __future__ import annotations

import colorsys
import json
from dataclasses import replace

import pytest
from PIL import ImageChops

from app.month.spec import MIN_BRIGHT_CHANNEL
from app.render.celebrate import celebrations_for, party_clip, sparkle_clip
from app.render.frame import Color, Frame
from app.render.palette import (
    CONFETTI,
    LABEL,
    RING,
    SECONDARY,
    TEXT,
    TRACK,
)
from app.render.rotation import ROTATION_ORDER
from app.render.screens import AS_OF_Y, render_parked, render_today
from app.render.theme import (
    ACCENT_SATURATION,
    BRIGHTNESSES,
    MIN_ACCENT_SATURATION,
    STOCK,
    accent_of,
    luma,
    on_panel,
    theme_for,
    theme_of,
    tint,
)
from app.render.view import DayView
from tests.render import COMBOS, SNAPSHOTS, load, rotation
from tests.render.test_snapshots import clip_digest

NO_FEATURE_DIGESTS = SNAPSHOTS / "no_feature_digests.json"
STRUCTURAL = {TRACK, RING}
THEMED = ("today", "week", "books", "city")
BLACK = (0, 0, 0)
TEXT_TONES = (TEXT, LABEL, SECONDARY)
TEXT_HEADROOM = 0.8


def feature_combos(settings) -> list[str]:
    return [combo for combo in COMBOS if load(combo, settings)[0].month_feature is not None]


def grey_feature(view: DayView):
    """The view's own feature with every palette entry a grey, so its plates still draw."""
    greys = ((255, 255, 255), (140, 140, 140)) * 4
    return replace(view.month_feature, palette=greys[: len(view.month_feature.palette)])


def pixels(frame: Frame) -> dict[tuple[int, int], Color]:
    data = list(frame.get_flattened_data())
    return {(x, y): data[y * 64 + x] for y in range(64) for x in range(64)}


# (a) no feature, no change


def test_a_view_without_a_feature_gets_the_very_palette_tones():
    theme = theme_for(DayView(day_local="2026-10-19"))
    assert theme is STOCK
    assert theme.track is TRACK and theme.ring is RING and theme.pip is RING
    assert theme.confetti is CONFETTI and theme.accent is None


def test_a_palette_of_greys_has_no_accent_and_keeps_the_stock_tones(settings):
    combo = feature_combos(settings)[0]
    view, now = load(combo, settings)
    grey = replace(view, month_feature=grey_feature(view))
    assert accent_of(grey.month_feature.palette) is None
    assert theme_for(grey) is STOCK
    bare = rotation(replace(view, month_feature=None), now)
    for screen in THEMED:
        assert clip_digest(rotation(grey, now)[screen]) == clip_digest(bare[screen]), screen


def test_feature_fixtures_without_their_feature_render_exactly_as_before_the_theme(settings):
    """Digests of the five feature fixtures rendered with month_feature=None, taken from the
    tree before theme.py existed. The five no-feature fixtures and the empty view are held by
    test_snapshots on their untouched snapshots."""
    pinned = json.loads(NO_FEATURE_DIGESTS.read_text())
    combos = feature_combos(settings)
    assert combos, "no fixture carries a month feature"
    seen = set()
    for combo in combos:
        view, now = load(combo, settings)
        bare = replace(view, month_feature=None)
        clips = rotation(bare, now)
        for screen in ROTATION_ORDER:
            assert clip_digest(clips[screen]) == pinned[f"{combo}/{screen}"], (combo, screen)
            seen.add(f"{combo}/{screen}")
        for celebration in celebrations_for(bare):
            key = f"{combo}/{celebration.name}"
            assert clip_digest(celebration.clip) == pinned[key], key
            seen.add(key)
    assert seen == set(pinned)


def test_stock_confetti_clips_are_the_clips_of_old():
    sleep = sparkle_clip("sleep")
    assert clip_digest(sparkle_clip("sleep", confetti=CONFETTI)) == clip_digest(sleep)
    assert clip_digest(party_clip(3, 3, confetti=CONFETTI)) == clip_digest(party_clip(3, 3))


# (b) a feature tints the structure and nothing else


@pytest.mark.parametrize("screen", THEMED)
def test_a_feature_recolours_exactly_the_track_ring_and_pip_pixels(screen, settings):
    for combo in feature_combos(settings):
        view, now = load(combo, settings)
        theme = theme_for(view)
        assert theme.accent == (255, 240, 180), "the sample's title cream"
        assert theme.track != TRACK and theme.ring != RING and theme.pip == theme.ring
        themed = rotation(view, now)[screen]
        bare = rotation(replace(view, month_feature=None), now)[screen]
        assert themed.durations_ms == bare.durations_ms
        changed_any = False
        for after, before in zip(themed.frames, bare.frames, strict=True):
            was, now_is = pixels(before), pixels(after)
            structural = {at for at, color in was.items() if color in STRUCTURAL}
            changed = {at for at in was if was[at] != now_is[at]}
            assert changed == structural, (combo, screen)
            for at in structural:
                want = theme.track if was[at] == TRACK else theme.ring
                assert now_is[at] == want, (combo, screen, at)
            changed_any |= bool(changed)
        if screen in ("today", "week"):
            assert changed_any, (combo, screen)


def test_the_parked_zz_is_untouched_by_the_theme(settings):
    """The parked mark and the as-of row it may reprint are SECONDARY whatever the month's
    accent, and nothing above the as-of row differs from Today."""
    for combo in feature_combos(settings):
        view, now = load(combo, settings)
        today = render_today(view, now).poster
        parked = render_parked(view, now).poster
        box = ImageChops.difference(today, parked).getbbox()
        assert box is not None and box[1] >= AS_OF_Y, (combo, box)
        was, now_is = pixels(today), pixels(parked)
        marks = {at for at in was if was[at] != now_is[at]}
        assert {now_is[at] for at in marks} <= {SECONDARY, BLACK}
        assert {was[at] for at in marks} <= {BLACK, SECONDARY}
        assert SECONDARY in {now_is[at] for at in marks}


def test_a_feature_adds_its_accent_to_the_confetti_and_nothing_else(settings):
    combo = feature_combos(settings)[0]
    view, _ = load(combo, settings)
    theme = theme_for(view)
    assert theme.confetti == (*CONFETTI, theme.accent)
    for celebration in celebrations_for(view):
        lit = {pixel for frame in celebration.clip.frames for pixel in frame.get_flattened_data()}
        assert theme.accent in lit, celebration.name
    stock = {c.name: c.clip for c in celebrations_for(replace(view, month_feature=None))}
    for celebration in celebrations_for(view):
        assert clip_digest(celebration.clip) != clip_digest(stock[celebration.name])


# (c) the luma ceiling, over every colour a feature may carry


def legal_accents() -> list[Color]:
    """One colour per degree of hue at several saturations and values, each bright enough
    for spec.MIN_BRIGHT_CHANNEL, plus the dimmest legal greys and white."""
    colours: list[Color] = []
    for degrees in range(360):
        for sat in (MIN_ACCENT_SATURATION, 0.5, 1.0):
            for top in (MIN_BRIGHT_CHANNEL, 255):
                r, g, b = colorsys.hsv_to_rgb(degrees / 360, sat, top / 255)
                colours.append((round(r * 255), round(g * 255), round(b * 255)))
    colours += [(MIN_BRIGHT_CHANNEL,) * 3, (255, 255, 255)]
    assert all(max(c) >= MIN_BRIGHT_CHANNEL for c in colours)
    return colours


def test_every_legal_accent_tints_under_the_stock_tone_at_every_day_brightness():
    dimmest_text = min(luma(on_panel(tone)) for tone in TEXT_TONES)
    for accent in legal_accents():
        hue, sat, _ = colorsys.rgb_to_hsv(*(c / 255 for c in accent))
        theme = theme_of((accent,))
        if sat < MIN_ACCENT_SATURATION:
            assert theme is STOCK, accent
            continue
        for tone, stock in ((theme.track, TRACK), (theme.ring, RING)):
            assert tone != BLACK, accent
            for brightness in BRIGHTNESSES:
                assert luma(on_panel(tone, brightness)) <= luma(on_panel(stock, brightness)), (
                    accent,
                    tone,
                    brightness,
                )
            assert luma(on_panel(tone)) <= TEXT_HEADROOM * dimmest_text, (accent, tone)
            tone_hue, tone_sat, _ = colorsys.rgb_to_hsv(*(c / 255 for c in tone))
            assert min(abs(tone_hue - hue), 1 - abs(tone_hue - hue)) < 0.02, (accent, tone)
            assert abs(tone_sat - ACCENT_SATURATION) < 0.06, (accent, tone)
        assert theme.track == tint(accent, TRACK) and theme.ring == tint(accent, RING)


def test_the_stock_tones_themselves_sit_under_the_text_by_the_same_margin():
    dimmest_text = min(luma(on_panel(tone)) for tone in TEXT_TONES)
    assert luma(on_panel(RING)) <= TEXT_HEADROOM * dimmest_text
    assert luma(on_panel(TRACK)) < luma(on_panel(RING))


def test_the_accent_is_the_first_palette_entry_with_a_nameable_hue():
    cream, blue, white = (255, 240, 180), (30, 80, 180), (255, 255, 255)
    assert accent_of((cream, blue)) == cream
    assert accent_of((white, blue)) == blue
    assert accent_of((white, (140, 140, 140))) is None
    assert theme_of((white, blue)) == theme_of((blue,))
