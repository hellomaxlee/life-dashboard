"""The GIF the panel is given to fetch, written in the form of Divoom's own sample.

Pillow's writer (adapters/file.gif_bytes, fine for a browser) gives every frame after the
first its own colour table and interlaces a one-frame file. The panel fetched those files and
showed its cloud channel instead (Max, 2026-10-06: "heart HOT"). Divoom's own sample
(f.divoom-gz.com/64_64.gif, read 2026-10-06: one 256-entry global table, no local table, no
interlacing) and the form another Pixoo project found its panel plays
(gickowtf/pixoo-homeassistant PR 158: cropped later frames, no transparency) are what this
writer emits exactly that and nothing else. It did not make the panel show a fetched GIF (Max,
2026-10-06, heart again), so the form is ruled out as the cause, not proven as the cure:

- GIF89a, one global colour table of 256 entries, no local table on any frame;
- no interlacing and no transparency flag (a transparent pixel is drawn as a white speck);
- the first frame the full canvas, later frames cropped to what changed, disposal 1;
- one image per clip frame, each with its own delay, looping forever.

A clip of at most 256 colours (every still) decodes back pixel for pixel. A clip with more
(a sparkle has about 265, from the twinkles' drifting hue) keeps its 256 commonest colours
exactly and draws each rarer one in the nearest colour kept.
"""

from __future__ import annotations

import math
from collections import Counter

from PIL import Image, ImageChops

from app.render.frame import SIZE, Clip, Color, Frame

TABLE = 256
_CODE_BITS = 8
_MAX_CODES = 4096
_DISPOSE_KEEP = 1 << 2


def shared_table(frames: tuple[Frame, ...]) -> tuple[list[Color], dict[Color, int]]:
    """The clip's colour table and each of its colours' index in it: the TABLE commonest
    colours (ties by value, so the table is deterministic), every other colour mapped to the
    nearest of those."""
    counts: Counter[Color] = Counter()
    for frame in frames:
        for count, color in frame.getcolors(SIZE * SIZE):
            counts[color] += count
    ranked = sorted(counts, key=lambda color: (-counts[color], color))
    table = ranked[:TABLE]
    index = {color: slot for slot, color in enumerate(table)}
    for color in ranked[TABLE:]:
        nearest = min(table, key=lambda kept: math.dist(kept, color))
        index[color] = index[nearest]
    return table, index


def _lzw(pixels: bytes) -> bytes:
    """GIF's variable-width LZW over 8-bit indices, as the data bytes before sub-blocking."""
    clear, end = 1 << _CODE_BITS, (1 << _CODE_BITS) + 1
    out = bytearray()
    acc = bits = 0
    width = _CODE_BITS + 1

    def emit(code: int) -> None:
        nonlocal acc, bits
        acc |= code << bits
        bits += width
        while bits >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            bits -= 8

    codes: dict[tuple[int, int], int] = {}
    next_code = end + 1
    emit(clear)
    prefix = pixels[0]
    for value in pixels[1:]:
        code = codes.get((prefix, value))
        if code is not None:
            prefix = code
            continue
        emit(prefix)
        if next_code < _MAX_CODES:
            codes[(prefix, value)] = next_code
            if next_code == 1 << width:
                width += 1
            next_code += 1
        else:
            emit(clear)
            codes.clear()
            next_code, width = end + 1, _CODE_BITS + 1
        prefix = value
    emit(prefix)
    emit(end)
    if bits:
        out.append(acc & 0xFF)
    return bytes(out)


def _blocks(data: bytes) -> bytes:
    out = bytearray()
    for start in range(0, len(data), 255):
        chunk = data[start : start + 255]
        out.append(len(chunk))
        out += chunk
    out.append(0)
    return bytes(out)


def _u16(value: int) -> bytes:
    return value.to_bytes(2, "little")


def panel_gif_bytes(clip: Clip) -> bytes:
    """The clip as a GIF in the form the module docstring lists."""
    table, index = shared_table(clip.frames)
    out = bytearray(b"GIF89a")
    out += _u16(SIZE) + _u16(SIZE) + bytes([0xF7, 0, 0])
    for color in table:
        out += bytes(color)
    out += bytes(3 * (TABLE - len(table)))
    out += b"\x21\xff\x0bNETSCAPE2.0\x03\x01" + _u16(0) + b"\x00"
    previous: Image.Image | None = None
    for frame, duration_ms in zip(clip.frames, clip.durations_ms, strict=True):
        raw = frame.tobytes()
        slots = bytes(index[tuple(raw[at : at + 3])] for at in range(0, len(raw), 3))
        indexed = Image.frombytes("L", (SIZE, SIZE), slots)
        if previous is None:
            box = (0, 0, SIZE, SIZE)
        else:
            box = ImageChops.difference(previous, indexed).getbbox() or (0, 0, 1, 1)
        previous = indexed
        left, top, right, bottom = box
        delay_cs = min(0xFFFF, max(1, round(duration_ms / 10)))
        out += b"\x21\xf9\x04" + bytes([_DISPOSE_KEEP]) + _u16(delay_cs) + b"\x00\x00"
        out += b"\x2c" + _u16(left) + _u16(top) + _u16(right - left) + _u16(bottom - top) + b"\x00"
        out += bytes([_CODE_BITS]) + _blocks(_lzw(indexed.crop(box).tobytes()))
    out += b"\x3b"
    return bytes(out)
