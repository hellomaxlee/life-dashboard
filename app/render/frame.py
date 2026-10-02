"""The frame contract every renderer and adapter shares.

A Frame is a 64x64 RGB Pillow image. A Clip is one or more frames, each with its own
duration in milliseconds; a still screen is a one-frame clip. Renderers return clips and
adapters only transport them, so nothing in this package knows which device is attached.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PIL import Image

SIZE = 64
STILL_MS = 8000

Frame = Image.Image
Color = tuple[int, int, int]


def new_frame(background: Color = (0, 0, 0)) -> Frame:
    return Image.new("RGB", (SIZE, SIZE), background)


def upscale(frame: Frame, scale: int) -> Image.Image:
    """Nearest-neighbour enlargement for previews; the device always gets the 1x frame."""
    if scale < 1:
        raise ValueError("scale must be at least 1")
    if scale == 1:
        return frame.copy()
    return frame.resize((frame.width * scale, frame.height * scale), Image.Resampling.NEAREST)


@dataclass(frozen=True)
class Clip:
    frames: tuple[Frame, ...]
    durations_ms: tuple[int, ...]
    poster_index: int = 0

    def __post_init__(self) -> None:
        if not self.frames:
            raise ValueError("a clip needs at least one frame")
        if len(self.frames) != len(self.durations_ms):
            raise ValueError("one duration per frame")
        for frame in self.frames:
            if frame.size != (SIZE, SIZE) or frame.mode != "RGB":
                raise ValueError(f"frame must be {SIZE}x{SIZE} RGB, got {frame.size} {frame.mode}")
        if any(ms <= 0 for ms in self.durations_ms):
            raise ValueError("durations must be positive milliseconds")
        if not 0 <= self.poster_index < len(self.frames):
            raise ValueError("poster_index out of range")

    @property
    def poster(self) -> Frame:
        """The frame that stands for the clip in a still preview."""
        return self.frames[self.poster_index]

    @property
    def total_ms(self) -> int:
        return sum(self.durations_ms)

    @property
    def animated(self) -> bool:
        return len(self.frames) > 1

    def map(self, fn: Callable[[Frame], Frame]) -> Clip:
        return Clip(tuple(fn(f) for f in self.frames), self.durations_ms, self.poster_index)


def still(frame: Frame, duration_ms: int = STILL_MS) -> Clip:
    return Clip((frame,), (duration_ms,))
