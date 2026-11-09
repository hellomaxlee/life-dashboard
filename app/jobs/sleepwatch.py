"""The host's sleep and wake, as macOS reports them, turned into the rotation's park and wake.

Max's MacBook is the host for now (a Raspberry Pi later), and a laptop sleeps: lid closed, on
battery, it is gone within seconds and dark-wakes for a few seconds every 17 minutes or so.
The panel keeps whatever was last sent, so the rotation looked frozen, on a mid-quote Books
page as often as not (power log against the send log, 2026-10-19). IOKit tells a process that
registers for system power notices about sleep before it happens and waits for the process to
acknowledge, up to 30 s, which is time enough to put one still on the panel.

Only the dispatch is portable. `start` returns False anywhere but macOS, or when IOKit will
not register us, and the rotation then relies on its own late-tick restart alone. The run
loop the notices need lives on one daemon thread of its own; the callback runs there, so
`on_sleep` does its sending on that thread, under the rotation's lock. Every sleep notice is
acknowledged whatever `on_sleep` did, because an unacknowledged one only delays the sleep.
Constants are from the SDK's IOMessage.h, checked by compiling them (2026-10-19).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import re
import subprocess
import sys
import threading
from collections.abc import Callable

WILL_SLEEP = 0xE0000280
CAN_SLEEP = 0xE0000270
WILL_NOT_SLEEP = 0xE0000290
HAS_POWERED_ON = 0xE0000300
ACKNOWLEDGED = (WILL_SLEEP, CAN_SLEEP)
IOKIT = "/System/Library/Frameworks/IOKit.framework/IOKit"
CORE_FOUNDATION = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
START_WAIT_S = 2.0
PMSET_TIMEOUT_S = 5.0
_USER_ACTIVE = re.compile(r"^\s*UserIsActive\s+(\d+)\s*$", re.MULTILINE)

log = logging.getLogger(__name__)

_Callback = ctypes.CFUNCTYPE(
    None, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p
)


def user_present(run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> bool:
    """False only when macOS says no user is active: a dark wake, the lid closed. A
    multi-minute model call started then dies when the host drops back to sleep two seconds
    later (all three November 2026 month runs, power log against the attempt rows). True
    anywhere pmset cannot answer, so a host that never sleeps (the Pi) is always present."""
    try:
        out = run(
            ["pmset", "-g", "assertions"],
            capture_output=True,
            text=True,
            timeout=PMSET_TIMEOUT_S,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return True
    match = _USER_ACTIVE.search(out or "")
    return match is None or match.group(1) != "0"


class SleepWatcher:
    def __init__(self, on_sleep: Callable[[], object], on_wake: Callable[[], object]) -> None:
        self._on_sleep = on_sleep
        self._on_wake = on_wake
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._registered = False
        self._iokit: ctypes.CDLL | None = None
        self._cf: ctypes.CDLL | None = None
        self._callback = _Callback(self._dispatch)
        self._port = 0
        self._notifier = ctypes.c_uint32(0)
        self._notify_port: int | None = None
        self._runloop: int | None = None

    def handle(self, message: int, argument: int | None = None) -> None:
        """One notice. Sleep, in either of its forms, parks; power-on wakes; the rest is
        noise. A handler that raises is logged, never propagated: the notice is acknowledged
        by the caller regardless."""
        if message in ACKNOWLEDGED:
            self._call(self._on_sleep, "sleep")
        elif message == HAS_POWERED_ON:
            self._call(self._on_wake, "wake")

    def _call(self, fn: Callable[[], object], what: str) -> None:
        try:
            fn()
        except Exception:
            log.exception("sleep watcher: the %s handler failed", what)

    def _dispatch(self, refcon: object, service: int, message: int, argument: object) -> None:
        try:
            self.handle(message, argument)
        finally:
            if message in ACKNOWLEDGED and self._iokit is not None:
                self._iokit.IOAllowPowerChange(self._port, argument)

    def start(self) -> bool:
        """Register and run the notice loop on its own thread. True once registered; False
        anywhere but macOS or when IOKit declines, with the reason logged."""
        if sys.platform != "darwin":
            return False
        self._thread = threading.Thread(target=self._run, name="sleep-watcher", daemon=True)
        self._thread.start()
        self._ready.wait(START_WAIT_S)
        return self._registered

    def _run(self) -> None:
        try:
            iokit, cf = self._load()
            port = ctypes.c_void_p()
            self._port = iokit.IORegisterForSystemPower(
                None, ctypes.byref(port), self._callback, ctypes.byref(self._notifier)
            )
            if not self._port:
                log.error("sleep watcher: IORegisterForSystemPower refused; no sleep notices")
                return
            self._notify_port = port.value
            source = iokit.IONotificationPortGetRunLoopSource(port)
            self._runloop = cf.CFRunLoopGetCurrent()
            mode = ctypes.c_void_p.in_dll(cf, "kCFRunLoopDefaultMode")
            cf.CFRunLoopAddSource(self._runloop, source, mode)
            self._registered = True
        except (OSError, AttributeError, ValueError):
            log.exception("sleep watcher: could not register; no sleep notices")
            return
        finally:
            self._ready.set()
        cf.CFRunLoopRun()

    def _load(self) -> tuple[ctypes.CDLL, ctypes.CDLL]:
        iokit = ctypes.CDLL(ctypes.util.find_library("IOKit") or IOKIT)
        cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation") or CORE_FOUNDATION)
        iokit.IORegisterForSystemPower.restype = ctypes.c_uint32
        iokit.IORegisterForSystemPower.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            _Callback,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        iokit.IONotificationPortGetRunLoopSource.restype = ctypes.c_void_p
        iokit.IONotificationPortGetRunLoopSource.argtypes = [ctypes.c_void_p]
        iokit.IOAllowPowerChange.restype = ctypes.c_int
        iokit.IOAllowPowerChange.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        iokit.IODeregisterForSystemPower.restype = ctypes.c_int
        iokit.IODeregisterForSystemPower.argtypes = [ctypes.POINTER(ctypes.c_uint32)]
        iokit.IONotificationPortDestroy.restype = None
        iokit.IONotificationPortDestroy.argtypes = [ctypes.c_void_p]
        iokit.IOServiceClose.restype = ctypes.c_int
        iokit.IOServiceClose.argtypes = [ctypes.c_uint32]
        cf.CFRunLoopGetCurrent.restype = ctypes.c_void_p
        cf.CFRunLoopAddSource.restype = None
        cf.CFRunLoopAddSource.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        cf.CFRunLoopRun.restype = None
        cf.CFRunLoopStop.restype = None
        cf.CFRunLoopStop.argtypes = [ctypes.c_void_p]
        self._iokit, self._cf = iokit, cf
        return iokit, cf

    def stop(self) -> None:
        """Deregister, end the loop and release the port and the connection, in the order
        IOPMLib.h asks for; harmless when never started. A registration still in progress is
        waited for first, so a slow start cannot leave a live watcher behind."""
        if self._thread is None:
            return
        self._ready.wait(START_WAIT_S)
        if not self._registered or self._iokit is None or self._cf is None:
            return
        self._iokit.IODeregisterForSystemPower(ctypes.byref(self._notifier))
        self._cf.CFRunLoopStop(self._runloop)
        self._registered = False
        self._thread.join(START_WAIT_S)
        if self._thread.is_alive():
            log.error("sleep watcher: the notice loop did not stop; its port is left open")
            return
        self._iokit.IONotificationPortDestroy(self._notify_port)
        self._iokit.IOServiceClose(self._port)
