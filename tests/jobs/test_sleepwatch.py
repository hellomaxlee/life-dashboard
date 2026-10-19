"""The macOS sleep watcher: its dispatch, and that it registers and lets go on a Mac."""

from __future__ import annotations

import sys

import pytest

from app.jobs import sleepwatch
from app.jobs.sleepwatch import (
    CAN_SLEEP,
    HAS_POWERED_ON,
    WILL_NOT_SLEEP,
    WILL_SLEEP,
    SleepWatcher,
)


def watcher(calls: list[str]) -> SleepWatcher:
    return SleepWatcher(lambda: calls.append("sleep"), lambda: calls.append("wake"))


def test_sleep_notices_park_and_power_on_wakes():
    calls: list[str] = []
    w = watcher(calls)
    w.handle(WILL_SLEEP, 7)
    w.handle(CAN_SLEEP, 8)
    w.handle(WILL_NOT_SLEEP)
    w.handle(HAS_POWERED_ON)
    w.handle(0xE0000320)
    assert calls == ["sleep", "sleep", "wake"]


def test_a_handler_that_raises_is_logged_and_the_notice_still_returns(caplog):
    def boom() -> None:
        raise RuntimeError("panel gone")

    w = SleepWatcher(boom, boom)
    w.handle(WILL_SLEEP, 1)
    w.handle(HAS_POWERED_ON)
    assert caplog.text.count("handler failed") == 2


def test_elsewhere_than_macos_it_declines(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    w = watcher([])
    assert w.start() is False
    w.stop()


@pytest.mark.skipif(sys.platform != "darwin", reason="IOKit is macOS")
def test_on_a_mac_it_registers_takes_a_notice_through_the_c_callback_and_lets_go():
    calls: list[str] = []
    w = watcher(calls)
    assert w.start() is True
    assert w._port and w._notifier.value
    w._callback(None, 0, HAS_POWERED_ON, None)
    assert calls == ["wake"], "the C-side thunk reaches the dispatch"
    w.stop()
    assert w._thread is not None and not w._thread.is_alive()
    assert sleepwatch.ACKNOWLEDGED == (WILL_SLEEP, CAN_SLEEP)
