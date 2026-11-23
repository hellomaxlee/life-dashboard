"""The GIF the panel fetches: read back block by block here, by a parser that shares no code
with the writer, and decoded by Pillow. The panel fetched Pillow-written GIFs (a colour table
per frame, interlaced stills) and showed its cloud channel instead of any of them."""

from __future__ import annotations

import io
import random
from dataclasses import dataclass

import pytest
from PIL import Image

from app.render.adapters import panelgif
from app.render.adapters.panelgif import panel_gif_bytes
from app.render.celebrate import party_clip, sparkle_clip
from app.render.frame import SIZE, Clip, new_frame, still
from app.render.rotation import device_parts, rotation_sequence
from tests.render import COMBOS, load


@dataclass(frozen=True)
class Image_:
    box: tuple[int, int, int, int]
    local_table: bool
    interlaced: bool
    transparent: bool
    disposal: int
    delay_cs: int


@dataclass(frozen=True)
class Parsed:
    version: bytes
    screen: tuple[int, int]
    table_size: int
    loops_forever: bool
    images: tuple[Image_, ...]
    ended: bool


def _skip_blocks(data: bytes, at: int) -> tuple[list[bytes], int]:
    blocks = []
    while data[at]:
        blocks.append(data[at + 1 : at + 1 + data[at]])
        at += data[at] + 1
    return blocks, at + 1


def parse(data: bytes) -> Parsed:
    flags = data[10]
    table_size = 2 << (flags & 7) if flags & 0x80 else 0
    at = 13 + 3 * table_size
    loops, images, control, ended = False, [], (0, False, 0), False
    while at < len(data):
        kind = data[at]
        if kind == 0x3B:
            ended = at == len(data) - 1
            break
        if kind == 0x21:
            label = data[at + 1]
            blocks, at = _skip_blocks(data, at + 2)
            if label == 0xF9:
                packed = blocks[0][0]
                delay = int.from_bytes(blocks[0][1:3], "little")
                control = ((packed >> 2) & 7, bool(packed & 1), delay)
            elif label == 0xFF and blocks[0] == b"NETSCAPE2.0":
                loops = blocks[1] == b"\x01\x00\x00"
            continue
        assert kind == 0x2C, f"unknown block {kind:#x} at {at}"
        left, top, width, height = (
            int.from_bytes(data[at + 1 + 2 * n : at + 3 + 2 * n], "little") for n in range(4)
        )
        packed = data[at + 9]
        at += 10 + (3 * (2 << (packed & 7)) if packed & 0x80 else 0) + 1
        _, at = _skip_blocks(data, at)
        images.append(
            Image_(
                (left, top, width, height), bool(packed & 0x80), bool(packed & 0x40), control[1],
                control[0], control[2],
            )
        )  # fmt: skip
    size = (int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little"))
    return Parsed(data[:6], size, table_size, loops, tuple(images), ended)


def decoded(data: bytes) -> list[Image.Image]:
    gif = Image.open(io.BytesIO(data))
    frames = []
    for number in range(gif.n_frames):
        gif.seek(number)
        frames.append(gif.convert("RGB"))
    return frames


def colours(clip: Clip) -> int:
    seen = set()
    for frame in clip.frames:
        seen |= {color for _, color in frame.getcolors(SIZE * SIZE)}
    return len(seen)


def assert_panel_form(clip: Clip, data: bytes) -> None:
    gif = parse(data)
    assert gif.version == b"GIF89a" and gif.screen == (SIZE, SIZE)
    assert gif.table_size == 256, "one global table, full size, as Divoom's own sample has"
    assert gif.loops_forever and gif.ended
    assert len(gif.images) == len(clip.frames), "one image per frame, none merged or dropped"
    assert gif.images[0].box == (0, 0, SIZE, SIZE), "the first frame is the whole canvas"
    for image, duration_ms in zip(gif.images, clip.durations_ms, strict=True):
        assert not image.local_table, "a per-frame table is what the panel refused"
        assert not image.interlaced, "an interlaced still is what the panel refused"
        assert not image.transparent and image.disposal == 1
        assert image.delay_cs == round(duration_ms / 10)
        left, top, width, height = image.box
        assert width >= 1 and height >= 1 and left + width <= SIZE and top + height <= SIZE


@pytest.mark.parametrize("combo", COMBOS)
def test_every_part_the_rotation_sends_is_in_the_panels_form_and_decodes_exactly(combo, settings):
    view, now = load(combo, settings)
    parts = [
        part
        for _, clip, _ in rotation_sequence(view, now, settings.device.screen_seconds)
        for part in device_parts(clip)
    ]
    assert len(parts) >= 5
    exact = 0
    for part in parts:
        data = panel_gif_bytes(part)
        assert_panel_form(part, data)
        if colours(part) <= panelgif.TABLE:
            exact += 1
            got = decoded(data)
            assert [frame.tobytes() for frame in got] == [f.tobytes() for f in part.frames]
    assert exact >= 5, "every still has few enough colours to come back pixel for pixel"


def test_a_clip_over_256_colours_keeps_all_but_a_few_pixels_near_their_colour():
    """255 core colours (a red ramp) drawn twice, so they rank among the 256 commonest with
    black, and 645 rarer colours each a step or three away from a core one: the rule's job is
    to send each rare colour to its own core colour, not to any other."""
    core = new_frame()
    for red in range(255):
        core.putpixel((red % SIZE, red // SIZE), (red, 40, 40))
        core.putpixel((red % SIZE, 8 + red // SIZE), (red, 40, 40))
    rare = [new_frame(), new_frame()]
    for n in range(645):
        rare[n % 2].putpixel(((n // 2) % SIZE, (n // 2) // SIZE), (n % 215, 41 + n // 215, 40))
    frames = [core, *rare]
    clip = Clip(tuple(frames), (70, 70, 70))
    assert colours(clip) > panelgif.TABLE, "the case the nearest-colour rule exists for"
    data = panel_gif_bytes(clip)
    assert_panel_form(clip, data)
    off, worst = 0, 0
    for got, want in zip(decoded(data), clip.frames, strict=True):
        pairs = zip(got.get_flattened_data(), want.get_flattened_data(), strict=True)
        for a, b in pairs:
            if a != b:
                off += 1
                worst = max(worst, *(abs(x - y) for x, y in zip(a, b, strict=True)))
    assert 0 < off <= 901 - panelgif.TABLE, "only colours past the 256 commonest (black too) move"
    assert worst <= 4, "and each only to its near neighbour"


def test_the_party_decodes_exactly_and_its_cropped_frames_are_smaller_than_the_canvas():
    clip = party_clip()
    data = panel_gif_bytes(clip)
    assert_panel_form(clip, data)
    assert [f.tobytes() for f in decoded(data)] == [f.tobytes() for f in clip.frames]
    assert any(image.box != (0, 0, SIZE, SIZE) for image in parse(data).images[1:])


def test_a_repeated_frame_is_still_its_own_image():
    red, blue = new_frame((200, 0, 0)), new_frame((0, 0, 200))
    clip = Clip((red, red, blue), (70, 70, 70))
    data = panel_gif_bytes(clip)
    assert_panel_form(clip, data)
    assert parse(data).images[1].box == (0, 0, 1, 1)
    assert [f.getpixel((5, 5)) for f in decoded(data)] == [(200, 0, 0), (200, 0, 0), (0, 0, 200)]


def test_a_frame_of_noise_survives_the_code_table_filling_up():
    rng = random.Random(3)
    table = [(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(256)]
    raw = b"".join(bytes(table[rng.randrange(256)]) for _ in range(SIZE * SIZE))
    noise = Image.frombytes("RGB", (SIZE, SIZE), raw)
    data = panel_gif_bytes(still(noise))
    assert len(data) > 5_500, "over 3838 codes: the 12-bit table filled and was cleared"
    assert decoded(data)[0].tobytes() == noise.tobytes()


def test_the_published_clip_is_the_panel_form(settings):
    from app.render.adapters import served

    served.clear()
    clip = sparkle_clip("sleep")
    assert served.served(served.publish(clip)) == panel_gif_bytes(clip)
    one = still(clip.poster)
    assert_panel_form(one, served.served(served.publish(one)))
