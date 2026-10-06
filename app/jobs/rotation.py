"""The scheduled display rotation: a tick sends the next screen once the last one's hold is up.

Today, City, Week, Month, Books, one sparkle per small win the day earned, then the
week-complete party once the week's target is met, wrapping (the sequence and its holds are
`app.render.rotation`; a restart begins at Today). A paged screen (City, Books, Month with a
note) goes page by page as stills. The scheduler runs one tick, then moves the next run to
`due()`: the moment the screen just sent has had its hold, so no tick overlaps a slow send.
A tick renders only its own screen from a fresh connection, so a renderer or a device that
fails costs that one slot: the slot is spent before any work, the error goes to the
scheduler's guard, and the next tick, one dwell later, tries the next screen. Nothing is sent
on a failed tick, so the device keeps whatever it showed last.
"""

from __future__ import annotations

import sqlite3
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from app.config import Settings
from app.render.adapters.pixoo import FRAME_BUDGET_S, PixooAdapter, PixooError
from app.render.frame import Clip
from app.render.rotation import device_parts, hold_ms, render_screen, sequence_names
from app.render.view_db import view_from_db
from app.timeutil import local_day, to_utc_iso

ROTATION_JOB = "device_rotation"
MIN_SCREEN_SECONDS = 3
DEVICE_TIMEOUT_S = 5.0
ROTATION_FALLBACK_S = 300
ROTATION_MISFIRE_GRACE_S = 24 * 3600
HOLDING = "holding"
SEND_LOG_SIZE = 80
SEND_ERRORS = (PixooError,)


class ClipAdapter(Protocol):
    def send(self, clip: Clip) -> object: ...

    def set_brightness(self, percent: int) -> None: ...

    def play_url(self, url: str) -> bool: ...


def brightness_at(now: datetime, settings: Settings) -> int:
    """The night level from `night_from` until `night_until`, home time, across midnight if
    the window wraps; the day level otherwise."""
    device = settings.device
    clock = now.astimezone(ZoneInfo(settings.home_tz)).strftime("%H:%M")
    start, end = device.night_from, device.night_until
    night = start <= clock < end if start <= end else clock >= start or clock < end
    return device.night_brightness if night else device.brightness


@dataclass(frozen=True)
class SendRecord:
    """One send to the panel, kept for the status page: when it started, what it was, how
    long the panel took to accept it, how long it is then held, and the error if it failed."""

    at_utc: str
    name: str
    page: int
    pages: int
    frames: int
    seconds: float
    hold_ms: int
    error: str | None = None
    how: str = "uploaded"


class DeviceRotation:
    def __init__(
        self,
        settings: Settings,
        open_conn: Callable[[], sqlite3.Connection],
        adapter: ClipAdapter,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        clip_url: Callable[[Clip], str] | None = None,
    ) -> None:
        self._settings = settings
        self._open_conn = open_conn
        self._adapter = adapter
        self._clock = clock
        self._clip_url = clip_url
        self._fetch_works: bool | None = None
        self._next = 0
        self._hold_until: datetime | None = None
        self._brightness: int | None = None
        self._pages: list[Clip] = []
        self._page_of = ""
        self._page_count = 1
        self.sends: deque[SendRecord] = deque(maxlen=SEND_LOG_SIZE)

    def _deliver(self, clip: Clip) -> str:
        """Every clip, stills included, goes as a GIF the panel fetches from this service when
        a publisher is wired and the panel accepts the command: an upload costs about 1.5 s a
        frame and shows a loading cycle for an animation, and switching between a fetched GIF
        and an uploaded frame made the panel flash its own cloud channel ("heart HOT") in
        between (Max, 2026-10-06). A panel that does not know the command gets uploads. The
        publisher is wired only by `[device] fetch_clips`, off while the panel shows that
        channel instead of any fetched GIF (workflows/run-service.md section 15)."""
        if self._clip_url is not None and self._fetch_works is not False:
            if self._adapter.play_url(self._clip_url(clip)):
                self._fetch_works = True
                return "fetched"
            self._fetch_works = False
        self._adapter.send(clip)
        return "uploaded"

    def _send(self, clip: Clip, name: str, page: int, pages: int, hold: int) -> None:
        at, started = to_utc_iso(self._clock()), time.monotonic()
        error = None
        how = "uploaded"
        try:
            how = self._deliver(clip)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            took = round(time.monotonic() - started, 2)
            self.sends.append(
                SendRecord(at, name, page, pages, len(clip.frames), took, hold, error, how)
            )

    def tick(self) -> str:
        """Send the next screen and return its name, or HOLDING while the last one's hold runs."""
        now = self._clock()
        if self._hold_until is not None and now < self._hold_until:
            return HOLDING
        dwell = self._settings.device.screen_seconds
        self._hold_until = now + timedelta(seconds=dwell)
        if self._pages:
            page = self._pages.pop(0)
            number = self._page_count - len(self._pages)
            try:
                self._send(page, self._page_of, number, self._page_count, page.total_ms)
            except Exception:
                self._pages = []
                raise
            self._hold_until = self._clock() + timedelta(milliseconds=page.total_ms)
            return self._page_of
        slot = self._next
        self._next += 1
        conn = self._open_conn()
        try:
            view = view_from_db(conn, self._settings, local_day(now, self._settings.home_tz))
        finally:
            conn.close()
        names = sequence_names(view)
        index = slot if slot < len(names) else 0
        name = names[index]
        self._next = (index + 1) % len(names)
        clip = render_screen(name, view, now)
        level = brightness_at(now, self._settings)
        if level != self._brightness:
            self._adapter.set_brightness(level)
            self._brightness = level
        first, *rest = device_parts(clip)
        hold = first.total_ms if rest else hold_ms(name, clip, dwell)
        self._send(first, name, 1, 1 + len(rest), hold)
        self._pages, self._page_of, self._page_count = rest, name, 1 + len(rest)
        self._hold_until = self._clock() + timedelta(milliseconds=hold)
        return name

    def due(self) -> datetime:
        """When the next tick should run: the end of the current hold, or of the wait after a
        failed tick."""
        return self._hold_until or self._clock()


def device_adapter(settings: Settings) -> PixooAdapter:
    """The Pixoo adapter for the configured host, with the timeouts measured on the panel.
    Raises ValueError for a host off the LAN or a dwell under MIN_SCREEN_SECONDS."""
    dwell = settings.device.screen_seconds
    if dwell < MIN_SCREEN_SECONDS:
        raise ValueError(f"device.screen_seconds must be {MIN_SCREEN_SECONDS} or more, got {dwell}")
    return PixooAdapter(
        settings.device.pixoo_host, timeout_s=DEVICE_TIMEOUT_S, frame_budget_s=FRAME_BUDGET_S
    )
