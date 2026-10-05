"""Send the rotation to a Pixoo once, by hand, and time every send: the hardware smoke test.

python -m tools.pixoo_check --host 192.168.1.50
python -m tools.pixoo_check --host 192.168.1.50 --screen week --screen win-workout
python -m tools.pixoo_check --host 192.168.1.50 --date 2026-10-04

Without --date the screens come from a fixture day, so no Health data is needed. Nothing is
read from or written to `config.toml` and the service is not involved: the rotation job
stays off until `device.pixoo_host` is set. Each screen is held as the job would hold it.
See workflows/run-service.md section 15.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

from app.config import REPO_ROOT, load_settings
from app.db import SchemaMismatch, connect_live
from app.jobs.rotation import DEVICE_TIMEOUT_S
from app.render.adapters.pixoo import FRAME_BUDGET_S, PixooAdapter, PixooError
from app.render.rotation import WIN_NAMES, rotation_sequence
from app.render.view import load_fixture
from app.render.view_db import view_from_db
from app.timeutil import now_utc

DEFAULT_FIXTURE = REPO_ROOT / "fixtures" / "days" / "train__all-sources__alive__base.json"
SCREENS = ("week", "today", *WIN_NAMES, "books")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools.pixoo_check", description=__doc__)
    parser.add_argument("--host", required=True, help="the Pixoo's literal LAN IPv4 address")
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE), help="a fixtures/days file")
    parser.add_argument("--date", help="a home-timezone day to read from the database instead")
    parser.add_argument("--screen", action="append", choices=SCREENS, help="default: the day's")
    parser.add_argument("--no-hold", action="store_true", help="send without waiting between")
    args = parser.parse_args(argv)

    settings = load_settings()
    try:
        adapter = PixooAdapter(args.host, timeout_s=DEVICE_TIMEOUT_S, frame_budget_s=FRAME_BUDGET_S)
        if args.date:
            day_local = date.fromisoformat(args.date).isoformat()
            conn = connect_live(settings.storage.db_path)
            try:
                view, now = view_from_db(conn, settings, day_local), now_utc()
            finally:
                conn.close()
        else:
            view, now = load_fixture(Path(args.fixture), settings)
    except (ValueError, OSError, SchemaMismatch) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    dwell = settings.device.screen_seconds
    slots = {name: (clip, hold) for name, clip, hold in rotation_sequence(view, now, dwell)}
    for name in args.screen or list(slots):
        if name not in slots:
            print(f"{name}: not earned on this day, skipped")
            continue
        clip, hold = slots[name]
        started = time.monotonic()
        try:
            report = adapter.send(clip)
        except PixooError as exc:
            print(f"{name}: FAILED after {time.monotonic() - started:.2f} s: {exc}")
            return 1
        took = time.monotonic() - started
        print(
            f"{name}: {report.frames_sent} frame(s) sent in {took:.2f} s "
            f"(PicId {report.pic_id}, {clip.durations_ms[0]} ms per frame, hold {hold / 1000:g} s)"
        )
        if not args.no_hold:
            time.sleep(hold / 1000)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
