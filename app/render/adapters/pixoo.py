"""Pixoo-64 adapter over the device's local HTTP API, as verified on Max's panel 2026-10-04.

Its callers are the `device_rotation` job (app/jobs/rotation.py), which exists only while
`device.pixoo_host` is set, and the hand check `tools.pixoo_check`; `pixoo_from_settings`
returns None while the host is empty.

Protocol as this firmware speaks it (it is not the community-documented Pixoo-64 one: port 80
is closed and `/post` is a 404): every command is a JSON POST to `/divoom_api` on port 9000.
A reply carries `ReturnCode`, 0 on success; an unknown command or a body it cannot parse is
ReturnCode 1. Each frame goes as one `Draw/SendHttpGif` with PicNum (frame count), PicOffset
(frame index), PicWidth 64, PicID, PicSpeed (ms) and PicData (base64 of 64*64*3 raw RGB
bytes). `Draw/GetHttpGifId` is accepted but returns no id, so the adapter numbers its own
animations from 1. `Draw/ResetHttpGifId` goes out before the first clip and then every
RESET_EVERY clips: community clients of the older API report the panel stops answering after
about 300 animations without it, which a rotation reaches within the hour.

Measured on the panel: the device takes a request body in at about 12 KB/s whatever the
command, so one frame (16.4 KB of base64) takes 1.4 to 1.8 s and a 20-frame sparkle 30 s.
Timeouts and the per-frame budget below are set from that.

Every request asks for its connection to be closed: the panel drops an idle kept-alive
connection without saying so, and the next command on it times out (seen after a 6 s hold).
It also refuses a connection now and then straight after closing the last one, so a refused
connect, where nothing was sent, is tried CONNECT_TRIES times.

Still to check by eye:
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

PORT = 9000
PATH = "/divoom_api"
TIMEOUT_S = 5.0
FRAME_BUDGET_S = 4.0
RESET_EVERY = 32
NO_REUSE = {"Connection": "close"}
CONNECT_TRIES = 3
CONNECT_RETRY_S = 0.3
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
    a device that drips bytes cannot hold a command open; `frame_budget_s`, when set, bounds a
    whole clip at that long per frame: once it is spent, the remaining frames are not sent
    and the send raises."""

    def __init__(
        self,
        host: str,
        client: httpx.Client | None = None,
        timeout_s: float = TIMEOUT_S,
        frame_budget_s: float | None = None,
    ) -> None:
        self.host = require_lan_host(host)
        self._client = client or httpx.Client(timeout=timeout_s, trust_env=False)
        self._command_budget_s = 2 * timeout_s
        self._frame_budget_s = frame_budget_s
        self._since_reset: int | None = None

    def _command(self, body: dict[str, object]) -> dict[str, object]:
        url = f"http://{self.host}:{PORT}{PATH}"
        deadline = time.monotonic() + self._command_budget_s
        try:
            for attempt in range(CONNECT_TRIES):
                try:
                    with self._client.stream("POST", url, json=body, headers=NO_REUSE) as response:
                        response.raise_for_status()
                        chunks = []
                        for chunk in response.iter_bytes():
                            chunks.append(chunk)
                            if time.monotonic() >= deadline:
                                raise httpx.ReadTimeout("reply took over the command budget")
                    break
                except httpx.ConnectError:
                    if attempt == CONNECT_TRIES - 1:
                        raise
                    time.sleep(CONNECT_RETRY_S)
            reply = json.loads(b"".join(chunks))
        except (httpx.HTTPError, ValueError) as exc:
            raise PixooError(f"{body.get('Command')}: {exc}") from exc
        if not isinstance(reply, dict) or reply.get("ReturnCode") != 0:
            raise PixooError(f"{body.get('Command')}: device replied {reply!r}")
        return reply

    def set_brightness(self, percent: int) -> None:
        """0 to 100. Accepted by the panel (ReturnCode 0); the effect is judged by eye."""
        self._command({"Command": "Channel/SetBrightness", "Brightness": percent})

    def send(self, clip: Clip) -> SendReport:
        """Push a clip as one animation. Raises PixooError if it is too long or a command fails."""
        count = len(clip.frames)
        if count > MAX_CLIP_FRAMES:
            raise PixooError(f"clip has {count} frames; the device limit is {MAX_CLIP_FRAMES}")
        budget = None if self._frame_budget_s is None else self._frame_budget_s * count
        deadline = None if budget is None else time.monotonic() + budget
        if self._since_reset is None or self._since_reset >= RESET_EVERY:
            self._command({"Command": "Draw/ResetHttpGifId"})
            self._since_reset = 0
        self._since_reset += 1
        pic_id = self._since_reset
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
