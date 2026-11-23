"""The native window's LED look (tools.pixoo_window), tested without opening a window."""

from __future__ import annotations

from app.render.frame import new_frame
from app.render.gamma import led_lut
from tests.render import WEEK_41, load, rotation
from tools.pixoo_window import DOT, UNLIT, led_image, led_mask, parse_args


def test_led_image_draws_discs_with_gaps_and_faint_unlit_dots(settings):
    size = 256
    mask = led_mask(size)
    view, now = load(WEEK_41, settings)
    week = rotation(view, now)["week"].poster
    image = led_image(week, size, led_lut(), mask)
    assert image.size == (size, size) and image.mode == "RGB"
    cell = size / 64
    lit = next((x, y) for y in range(64) for x in range(64) if week.getpixel((x, y)) != (0, 0, 0))
    unlit = next((x, y) for y in range(64) for x in range(64) if week.getpixel((x, y)) == (0, 0, 0))
    centre = tuple(int((c + 0.5) * cell) for c in lit)
    corner = tuple(int(c * cell) for c in lit)
    mapped = tuple(led_lut()[c] for c in week.getpixel(lit))
    assert all(abs(a - b) <= 2 for a, b in zip(image.getpixel(centre), mapped, strict=True))
    assert max(image.getpixel(corner)) < 64, "the gap at the cell corner stays dark"
    unlit_centre = tuple(int((c + 0.5) * cell) for c in unlit)
    assert max(image.getpixel(unlit_centre)) <= max(UNLIT) + 40, "unlit dots are faint"
    assert DOT == 0.70
    blank = led_image(new_frame(), size, led_lut(), mask)
    assert blank.getpixel((int(cell / 2), int(cell / 2))) == UNLIT
    assert blank.getpixel((0, 0)) == (0, 0, 0)


def test_window_args_default_to_the_local_service():
    args = parse_args(["--fixture", WEEK_41, "--brightness", "40"])
    assert args.url == "http://127.0.0.1:8080" and args.size == 512 and args.brightness == 40
    assert not args.raw and args.date is None
