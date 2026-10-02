"""The scheduled display rotation: each tick sends the next screen to the device.

Week, Today, Books, wrapping. A tick renders only its own screen from a fresh connection, so
a renderer or a device that fails costs that one slot: the slot is spent before any work, the
error goes to the scheduler's guard, and the next tick tries the next screen. Nothing is sent
on a failed tick, so the device keeps whatever it showed last. A clip longer than the tick
interval keeps its screen until it has played once. No celebrations: their triggers need the
metrics engine.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

from app.config import Settings
from app.render.adapters.pixoo import PixooAdapter
from app.render.frame import Clip
from app.render.rotation import ROTATION_ORDER, render_screen
from app.render.view_db import view_from_db
from app.timeutil import local_day, now_utc

ROTATION_JOB = "device_rotation"
MIN_SCREEN_SECONDS = 15
DEVICE_TIMEOUT_S = 2.0
HOLDING = "holding"


class ClipAdapter(Protocol):
    def send(self, clip: Clip) -> object: ...


class DeviceRotation:
    def __init__(
        self,
        settings: Settings,
        open_conn: Callable[[], sqlite3.Connection],
        adapter: ClipAdapter,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self._settings = settings
        self._open_conn = open_conn
        self._adapter = adapter
        self._clock = clock
        self._next = 0
        self._hold_until: datetime | None = None

    def tick(self) -> str:
        """Send the next screen and return its name, or HOLDING while a long clip plays out."""
        now = self._clock()
        if self._hold_until is not None and now < self._hold_until:
            return HOLDING
        name = ROTATION_ORDER[self._next]
        self._next = (self._next + 1) % len(ROTATION_ORDER)
        self._hold_until = None
        conn = self._open_conn()
        try:
            view = view_from_db(conn, self._settings, local_day(now, self._settings.home_tz))
        finally:
            conn.close()
        clip = render_screen(name, view, now)
        self._adapter.send(clip)
        self._hold_until = now + timedelta(milliseconds=clip.total_ms)
        return name


def device_adapter(settings: Settings) -> PixooAdapter:
    """The Pixoo adapter for the configured host, timed so one send ends before the next tick:
    half the dwell for the clip plus at most connect, write and read timeouts of one command.
    Raises ValueError for a host off the LAN or a dwell under MIN_SCREEN_SECONDS."""
    dwell = settings.device.screen_seconds
    if dwell < MIN_SCREEN_SECONDS:
        raise ValueError(f"device.screen_seconds must be {MIN_SCREEN_SECONDS} or more, got {dwell}")
    return PixooAdapter(
        settings.device.pixoo_host, timeout_s=DEVICE_TIMEOUT_S, send_budget_s=dwell / 2
    )
