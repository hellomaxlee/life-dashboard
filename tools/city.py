"""Fetch or inspect the city panel's data: the lines Max rides and the day's weather.

python -m tools.city                  fetch the MTA alerts and the weather now, store both
                                      snapshots, print the day's status
python -m tools.city --show           print the stored status; nothing is fetched
python -m tools.city --fixture DIR    parse subway.json, bus.json, wx.json and nws.json from
                                      DIR; nothing is fetched, read or stored
python -m tools.city --fixture DIR --now 2026-10-04T23:00
                                      the same, as of a home-timezone time (default: the
                                      subway feed's own timestamp)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.city import store
from app.city.fetch import FetchError, poll_transit, poll_weather
from app.city.model import CityStatus
from app.city.parse import ParseError, line_statuses, lines_for_day, parse_weather
from app.config import Settings, load_settings
from app.db import SchemaMismatch, connect_live
from app.timeutil import local_day, now_utc, to_utc_iso

FIXTURE_FILES = ("subway.json", "bus.json", "wx.json", "nws.json")


def format_status(status: CityStatus) -> str:
    """The status as the table this tool prints."""
    out = [f"city {status.day_local}"]
    out.append(f"transit fetched {status.transit_fetched_at_utc or 'never'}")
    for line in status.lines:
        when = "" if line.status == "ok" else ("now" if line.now else "later")
        out.append(
            f"  {line.line:<5}{line.kind:<7}{line.status:<10}{when:<6}"
            f"{line.alerts:>2}  {line.headline or ''}".rstrip()
        )
    weather = status.weather
    if weather is None:
        out.append("weather fetched never")
        return "\n".join(out)

    def degrees(value: float | None) -> str:
        return "?" if value is None else f"{value:.0f}F"

    out.append(f"weather fetched {weather.fetched_at_utc or 'never'}")
    chance = "?" if weather.precip_pct_max is None else f"{weather.precip_pct_max}%"
    out.append(
        f"  now {degrees(weather.temp_f)} feels {degrees(weather.feels_f)} "
        f"code {'?' if weather.code is None else weather.code}; high {degrees(weather.high_f)} "
        f"low {degrees(weather.low_f)}; precip max {chance}"
    )
    for hour in weather.hours:
        mark = "+1" if hour.tomorrow else "  "
        out.append(
            f"  {hour.hour_local:02d}:00{mark} {hour.temp_f:5.1f}F  "
            f"{hour.precip_pct:3d}%  code {hour.code}"
        )
    out.append("  alerts: " + (", ".join(weather.alerts) or "none"))
    return "\n".join(out)


def fixture_status(folder: Path, settings: Settings, now: datetime | None) -> CityStatus:
    """A CityStatus parsed from saved replies, as of `now` (default: the subway feed's
    timestamp). Touches no network and no database."""
    subway, bus, forecast, nws = (json.loads((folder / name).read_text()) for name in FIXTURE_FILES)
    tz = ZoneInfo(settings.home_tz)
    moment = now or datetime.fromtimestamp(int(subway["header"]["timestamp"]), UTC)
    day = local_day(moment, settings.home_tz)
    city = settings.city
    wanted = lines_for_day(city.subway, city.subway_weekday, city.subway_weekend, city.bus, day)
    weather = parse_weather(forecast, nws, moment, tz)
    stamp = to_utc_iso(moment)
    return CityStatus(
        day_local=day,
        weather=replace(weather, fetched_at_utc=stamp),
        lines=line_statuses(subway, bus, wanted, moment, tz),
        transit_fetched_at_utc=stamp,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools.city", description=__doc__)
    parser.add_argument("--show", action="store_true", help="print the stored status only")
    parser.add_argument("--fixture", type=Path, help="parse saved replies from this directory")
    parser.add_argument("--now", help="with --fixture: home-timezone time, YYYY-MM-DDTHH:MM")
    args = parser.parse_args(argv)
    settings = load_settings()
    if args.now and not args.fixture:
        print("refusing: --now goes with --fixture", file=sys.stderr)
        return 2
    if args.fixture:
        try:
            now = None
            if args.now:
                now = datetime.fromisoformat(args.now).replace(tzinfo=ZoneInfo(settings.home_tz))
            print(format_status(fixture_status(args.fixture, settings, now)))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"refusing: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        return 0
    try:
        conn = connect_live(settings.storage.db_path)
    except SchemaMismatch as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    try:
        failed = 0
        if not args.show:
            for name, poll in (("transit", poll_transit), ("weather", poll_weather)):
                try:
                    poll(conn, settings)
                except (FetchError, ParseError) as exc:
                    failed = 1
                    print(f"{name} fetch failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        status = store.load_status(conn, local_day(now_utc(), settings.home_tz))
        if status is None:
            print("nothing stored: the city panel has never been fetched")
            return 1
        print(format_status(status))
        return failed
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
