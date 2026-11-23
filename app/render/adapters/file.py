"""File adapter: clips as PNG and GIF bytes, for tests, the CLI, and the LAN preview page."""

from __future__ import annotations

from collections.abc import Callable
from io import BytesIO
from pathlib import Path

from app.render.frame import Clip, Frame, upscale

Transform = Callable[[Frame], Frame]


def png_bytes(frame: Frame, scale: int = 1) -> bytes:
    buffer = BytesIO()
    upscale(frame, scale).save(buffer, format="PNG")
    return buffer.getvalue()


def gif_bytes(clip: Clip, scale: int = 1, transform: Transform | None = None) -> bytes:
    """A looping GIF with each frame's own duration."""
    shown = clip.map(transform) if transform else clip
    images = [upscale(frame, scale) for frame in shown.frames]
    buffer = BytesIO()
    images[0].save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=images[1:],
        duration=list(shown.durations_ms),
        loop=0,
        disposal=1,
        optimize=False,
    )
    return buffer.getvalue()


class FileAdapter:
    """Writes `<name>.png` (the poster) and, for an animated clip, `<name>.gif`."""

    def __init__(self, directory: Path, scale: int = 1, transform: Transform | None = None) -> None:
        self.directory = directory
        self.scale = scale
        self.transform = transform

    def send(self, clip: Clip, name: str = "clip") -> list[Path]:
        self.directory.mkdir(parents=True, exist_ok=True)
        poster = self.transform(clip.poster) if self.transform else clip.poster
        written = [self.directory / f"{name}.png"]
        written[0].write_bytes(png_bytes(poster, self.scale))
        if clip.animated:
            written.append(self.directory / f"{name}.gif")
            written[1].write_bytes(gif_bytes(clip, self.scale, self.transform))
        return written
