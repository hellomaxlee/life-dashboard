"""The scheduled display rotation: a tick sends the next screen once the last one's hold is up.

Week, Today, one sparkle per small win the day earned, Books, wrapping (the sequence and
its holds are `app.render.rotation`). The scheduler runs one tick, then moves the next run to
`due()`: the moment the screen just sent has had its hold, so no tick overlaps a slow send.
A tick renders only its own screen from a fresh connection, so a renderer or a device that
fails costs that one slot: the slot is spent before any work, the error goes to the
scheduler's guard, and the next tick, one dwell later, tries the next screen. Nothing is sent
on a failed tick, so the device keeps whatever it showed last. The week-complete party is
not in the sequence.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.config import Settings
from app.render.adapters.pixoo import FRAME_BUDGET_S, PixooAdapter, PixooError
from app.render.frame import Clip
from app.render.rotation import hold_ms, render_screen, sequence_names
from app.render.view_db import view_from_db
from app.timeutil import local_day

ROTATION_JOB = "device_rotation"
MIN_SCREEN_SECONDS = 3
DEVICE_TIMEOUT_S = 5.0
ROTATION_FALLBACK_S = 300
ROTATION_MISFIRE_GRACE_S = 24 * 3600
HOLDING = "holding"
SEND_ERRORS = (PixooError,)


class ClipAdapter(Protocol):
    def send(self, clip: Clip) -> object: ...


class DeviceRotation:
    def __init__(
        self,
        settings: Settings,
        open_conn: Callable[[], sqlite3.Connection],
        adapter: ClipAdapter,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._settings = settings
        self._open_conn = open_conn
        self._adapter = adapter
        self._clock = clock
        self._next = 0
        self._hold_until: datetime | None = None

    def tick(self) -> str:
        """Send the next screen and return its name, or HOLDING while the last one's hold runs."""
        now = self._clock()
        if self._hold_until is not None and now < self._hold_until:
            return HOLDING
        dwell = self._settings.device.screen_seconds
        self._hold_until = now + timedelta(seconds=dwell)
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
        self._adapter.send(clip)
        self._hold_until = self._clock() + timedelta(milliseconds=hold_ms(name, clip, dwell))
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
