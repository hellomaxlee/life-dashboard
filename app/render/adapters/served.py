"""Clips the panel fetches instead of receiving frame by frame.

Uploading an animation to the panel costs about 1.5 s a frame and the panel shows a loading
cycle while it waits (Max, 2026-10-05: "it sometimes goes to the loading cycle before the
small win animations"). Its `Device/PlayTFGif` command instead fetches a GIF from a URL, so
an animated clip is published here as a GIF a few KB long, served by the LAN page at
`/pixoo/clip/<token>.gif`, and the panel is given that address. Only the newest few clips
are kept; the panel fetches within a second of being told.
"""

from __future__ import annotations

import hashlib
import socket
from collections import OrderedDict

from app.render.adapters.file import gif_bytes
from app.render.adapters.pixoo import require_lan_host
from app.render.frame import Clip

KEEP = 8
_clips: OrderedDict[str, bytes] = OrderedDict()


def publish(clip: Clip) -> str:
    """Store the clip's GIF and return its token (the sha256 of the bytes, so the same clip
    is one entry)."""
    data = gif_bytes(clip)
    token = hashlib.sha256(data).hexdigest()[:16]
    _clips[token] = data
    _clips.move_to_end(token)
    while len(_clips) > KEEP:
        _clips.popitem(last=False)
    return token


def served(token: str) -> bytes | None:
    return _clips.get(token)


def clear() -> None:
    _clips.clear()


def own_address(device_host: str) -> str:
    """The address this machine has on the device's network, read off a connected UDP
    socket; no packet is sent."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((require_lan_host(device_host), 9))
        return probe.getsockname()[0]
    finally:
        probe.close()


def clip_url(device_host: str, port: int, token: str) -> str:
    return f"http://{own_address(device_host)}:{port}/pixoo/clip/{token}.gif"
