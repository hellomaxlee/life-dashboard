"""Pixoo-64 adapter over the device's local HTTP API. UNVERIFIED ON HARDWARE.

No Pixoo has been bought. Everything here follows the community-documented local API and is
tested against a fake transport only; Phase 5 verifies it on the real device. Nothing calls
this adapter by default: `pixoo_from_settings` returns None while `device.pixoo_host` is empty.

Protocol as understood: every command is a JSON POST to the device's `/post` path on port 80.
`Draw/GetHttpGifId` returns the next animation id; each frame then goes as one
`Draw/SendHttpGif` with PicNum (frame count), PicOffset (frame index), PicWidth 64, PicSpeed
(ms) and PicData (base64 of 64*64*3 raw RGB bytes). A reply's `error_code` is 0 on success.

Assumptions to check on hardware:
- an animation holds fewer than 60 frames; longer clips are thinned here to fit (frames are
  dropped evenly and their time is given to the frame kept before them), which keeps total
  duration but makes the summary scroll coarse. A scroll that long needs a better transport
  plan in Phase 5;
- PicSpeed is honoured per frame; some firmware may apply the first frame's speed to all;
- the device applies no gamma of its own (see app/render/gamma.py).

The host must be a private LAN address. Health data stays home; so do frames.
"""

from __future__ import annotations

import base64
import ipaddress
from dataclasses import dataclass

import httpx

from app.config import Settings
from app.render.frame import SIZE, Clip

MAX_FRAMES = 59
TIMEOUT_S = 5.0


class PixooError(RuntimeError):
    pass


@dataclass(frozen=True)
class SendReport:
    pic_id: int
    frames_sent: int
    frames_dropped: int


def require_lan_host(host: str) -> str:
    """Accept only a literal private IPv4 address; anything else is refused before any I/O."""
    try:
        address = ipaddress.IPv4Address(host)
    except ValueError as exc:
        raise ValueError(f"pixoo host must be a LAN IPv4 address, got {host!r}") from exc
    if not address.is_private or address.is_loopback:
        raise ValueError(f"pixoo host must be a private LAN address, got {host!r}")
    return str(address)


def thin(clip: Clip, max_frames: int = MAX_FRAMES) -> Clip:
    """Fit a clip to the device's frame limit without changing its total duration."""
    count = len(clip.frames)
    if count <= max_frames:
        return clip
    keep = sorted({int(i * count / max_frames) for i in range(max_frames)})
    durations = []
    for position, index in enumerate(keep):
        until = keep[position + 1] if position + 1 < len(keep) else count
        durations.append(sum(clip.durations_ms[index:until]))
    return Clip(tuple(clip.frames[i] for i in keep), tuple(durations))


class PixooAdapter:
    def __init__(self, host: str, client: httpx.Client | None = None) -> None:
        self.host = require_lan_host(host)
        self._client = client or httpx.Client(timeout=TIMEOUT_S)

    def _command(self, body: dict[str, object]) -> dict[str, object]:
        url = f"http://{self.host}/post"
        try:
            response = self._client.post(url, json=body)
            response.raise_for_status()
            reply = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PixooError(f"{body.get('Command')}: {exc}") from exc
        if not isinstance(reply, dict) or reply.get("error_code") != 0:
            raise PixooError(f"{body.get('Command')}: device replied {reply!r}")
        return reply

    def send(self, clip: Clip) -> SendReport:
        """Push a clip as one animation. Raises PixooError on any failed command."""
        fitted = thin(clip)
        reply = self._command({"Command": "Draw/GetHttpGifId"})
        pic_id = reply.get("PicId")
        if isinstance(pic_id, bool) or not isinstance(pic_id, int):
            raise PixooError(f"Draw/GetHttpGifId: no PicId in {reply!r}")
        for offset, (frame, duration_ms) in enumerate(
            zip(fitted.frames, fitted.durations_ms, strict=True)
        ):
            self._command(
                {
                    "Command": "Draw/SendHttpGif",
                    "PicNum": len(fitted.frames),
                    "PicWidth": SIZE,
                    "PicOffset": offset,
                    "PicID": pic_id,
                    "PicSpeed": duration_ms,
                    "PicData": base64.b64encode(frame.tobytes()).decode("ascii"),
                }
            )
        return SendReport(pic_id, len(fitted.frames), len(clip.frames) - len(fitted.frames))


def pixoo_from_settings(settings: Settings) -> PixooAdapter | None:
    """None while no host is configured, which is the default."""
    host = settings.device.pixoo_host.strip()
    return PixooAdapter(host) if host else None
