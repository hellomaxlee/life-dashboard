"""Pixoo-64 adapter over the device's local HTTP API. UNVERIFIED ON HARDWARE.

No Pixoo has been bought. Everything here follows the community-documented local API and is
tested against a fake transport only; Phase 5 verifies it on the real device. Its one caller
is the `device_rotation` job (app/jobs/rotation.py), which exists only while
`device.pixoo_host` is set; `pixoo_from_settings` returns None while it is empty.

Protocol as understood: every command is a JSON POST to the device's `/post` path on port 80.
`Draw/GetHttpGifId` returns the next animation id; each frame then goes as one
`Draw/SendHttpGif` with PicNum (frame count), PicOffset (frame index), PicWidth 64, PicSpeed
(ms) and PicData (base64 of 64*64*3 raw RGB bytes). A reply's `error_code` is 0 on success.
`Draw/ResetHttpGifId` goes out before the first clip and then every RESET_EVERY clips:
community clients report the panel stops answering after about 300 animations without it,
which a rotation reaches in half an hour.

Assumptions to check on hardware:
- an animation holds fewer than 60 frames. A clip over frame.MAX_CLIP_FRAMES is refused
  with PixooError before anything is sent; it is never thinned, because dropped frames are
  not what the renderer drew. Every screen and celebration is built to fit;
- PicSpeed is honoured per frame; some firmware may apply the first frame's speed to all;
- the device applies no gamma of its own (see app/render/gamma.py).

The host must be a literal address in 10/8, 172.16/12 or 192.168/16, nothing else.
The client is built with trust_env=False, so HTTP_PROXY and friends in the environment are
ignored and a frame can only go to that address. Health data stays home; so do frames.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import time
from dataclasses import dataclass

import httpx

from app.config import Settings
from app.render.frame import MAX_CLIP_FRAMES, SIZE, Clip

TIMEOUT_S = 5.0
RESET_EVERY = 32
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
    """`timeout_s` bounds each phase of one command and twice that bounds its whole reply, so
    a device that drips bytes cannot hold a command open; `send_budget_s`, when set, bounds a
    whole clip: once it is spent, the remaining frames are not sent and the send raises."""

    def __init__(
        self,
        host: str,
        client: httpx.Client | None = None,
        timeout_s: float = TIMEOUT_S,
        send_budget_s: float | None = None,
    ) -> None:
        self.host = require_lan_host(host)
        self._client = client or httpx.Client(timeout=timeout_s, trust_env=False)
        self._command_budget_s = 2 * timeout_s
        self._send_budget_s = send_budget_s
        self._since_reset: int | None = None

    def _command(self, body: dict[str, object]) -> dict[str, object]:
        url = f"http://{self.host}/post"
        deadline = time.monotonic() + self._command_budget_s
        try:
            with self._client.stream("POST", url, json=body) as response:
                response.raise_for_status()
                chunks = []
                for chunk in response.iter_bytes():
                    chunks.append(chunk)
                    if time.monotonic() >= deadline:
                        raise httpx.ReadTimeout("reply took over the command budget")
            reply = json.loads(b"".join(chunks))
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
        budget = self._send_budget_s
        deadline = None if budget is None else time.monotonic() + budget
        if self._since_reset is None or self._since_reset >= RESET_EVERY:
            self._command({"Command": "Draw/ResetHttpGifId"})
            self._since_reset = 0
        self._since_reset += 1
        reply = self._command({"Command": "Draw/GetHttpGifId"})
        pic_id = reply.get("PicId")
        if isinstance(pic_id, bool) or not isinstance(pic_id, int):
            raise PixooError(f"Draw/GetHttpGifId: no PicId in {reply!r}")
        for offset, (frame, duration_ms) in enumerate(
            zip(clip.frames, clip.durations_ms, strict=True)
        ):
            if deadline is not None and time.monotonic() >= deadline:
                raise PixooError(f"over the {budget} s send budget at frame {offset} of {count}")
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
