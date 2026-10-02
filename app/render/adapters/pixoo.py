"""Pixoo-64 adapter over the device's local HTTP API. UNVERIFIED ON HARDWARE.

No Pixoo has been bought. Everything here follows the community-documented local API and is
tested against a fake transport only; Phase 5 verifies it on the real device. Nothing calls
this adapter by default: `pixoo_from_settings` returns None while `device.pixoo_host` is empty.

Protocol as understood: every command is a JSON POST to the device's `/post` path on port 80.
`Draw/GetHttpGifId` returns the next animation id; each frame then goes as one
`Draw/SendHttpGif` with PicNum (frame count), PicOffset (frame index), PicWidth 64, PicSpeed
(ms) and PicData (base64 of 64*64*3 raw RGB bytes). A reply's `error_code` is 0 on success.

Assumptions to check on hardware:
- an animation holds fewer than 60 frames. A clip over frame.MAX_CLIP_FRAMES is refused
  with PixooError before anything is sent; it is never thinned, because dropped frames are
  not what the renderer drew. Every screen and celebration is built to fit;
- PicSpeed is honoured per frame; some firmware may apply the first frame's speed to all;
- the device applies no gamma of its own (see app/render/gamma.py).

The host must be a literal address in 10/8, 172.16/12 or 192.168/16, nothing else.
Health data stays home; so do frames.
"""

from __future__ import annotations

import base64
import ipaddress
from dataclasses import dataclass

import httpx

from app.config import Settings
from app.render.frame import MAX_CLIP_FRAMES, SIZE, Clip

TIMEOUT_S = 5.0
LAN_NETWORKS = tuple(
    ipaddress.IPv4Network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


class PixooError(RuntimeError):
    pass


@dataclass(frozen=True)
class SendReport:
    pic_id: int
    frames_sent: int


def require_lan_host(host: str) -> str:
    """Accept only a literal home-LAN IPv4 address; anything else is refused before any I/O."""
    try:
        address = ipaddress.IPv4Address(host)
    except ValueError as exc:
        raise ValueError(f"pixoo host must be a LAN IPv4 address, got {host!r}") from exc
    if not any(address in network for network in LAN_NETWORKS):
        raise ValueError(f"pixoo host must be in 10/8, 172.16/12 or 192.168/16, got {host!r}")
    return str(address)


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
        """Push a clip as one animation. Raises PixooError if it is too long or a command fails."""
        count = len(clip.frames)
        if count > MAX_CLIP_FRAMES:
            raise PixooError(f"clip has {count} frames; the device limit is {MAX_CLIP_FRAMES}")
        reply = self._command({"Command": "Draw/GetHttpGifId"})
        pic_id = reply.get("PicId")
        if isinstance(pic_id, bool) or not isinstance(pic_id, int):
            raise PixooError(f"Draw/GetHttpGifId: no PicId in {reply!r}")
        for offset, (frame, duration_ms) in enumerate(
            zip(clip.frames, clip.durations_ms, strict=True)
        ):
            self._command(
                {
                    "Command": "Draw/SendHttpGif",
                    "PicNum": count,
                    "PicWidth": SIZE,
                    "PicOffset": offset,
                    "PicID": pic_id,
                    "PicSpeed": duration_ms,
                    "PicData": base64.b64encode(frame.tobytes()).decode("ascii"),
                }
            )
        return SendReport(pic_id, count)


def pixoo_from_settings(settings: Settings) -> PixooAdapter | None:
    """None while no host is configured, which is the default."""
    host = settings.device.pixoo_host.strip()
    return PixooAdapter(host) if host else None
