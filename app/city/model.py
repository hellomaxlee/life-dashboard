"""The city panel's data: weather for the day and the state of the lines Max rides.

`app/city/` fetches and stores a CityStatus; `app/render/city.py` draws it. This module is
the contract between them. Nothing here is a metric: it is display-only, comes from public
feeds, and carries no health data.

Statuses, mildest first (STATUS_ORDER): a line shows the most severe alert that touches today.
- "ok": no alert active today.
- "planned": planned work that changes the service (reroute, stops skipped, express to
  local, detour, part suspended) at some point today.
- "delays": delays or reduced service now.
- "suspended": no service on the line now.
"""

from __future__ import annotations

from dataclasses import dataclass

STATUS_ORDER = ("ok", "planned", "delays", "suspended")
KINDS = ("subway", "bus")


@dataclass(frozen=True)
class HourWeather:
    """One step of the day's forecast. `hour_local` is 0 to 23 in the home timezone and
    `tomorrow` marks a step past midnight; `code` is a WMO weather code."""

    hour_local: int
    temp_f: float
    precip_pct: int
    code: int
    tomorrow: bool = False


@dataclass(frozen=True)
class Weather:
    """`hours` runs from the current hour through the day in 3-hour steps (six steps, running
    into tomorrow morning when today has fewer left). `alerts` are active National Weather
    Service event names for the point, most severe first ("Wind Advisory")."""

    temp_f: float | None
    feels_f: float | None
    code: int | None
    high_f: float | None
    low_f: float | None
    precip_pct_max: int | None
    hours: tuple[HourWeather, ...] = ()
    alerts: tuple[str, ...] = ()
    fetched_at_utc: str | None = None


@dataclass(frozen=True)
class LineStatus:
    """`line` is what the rider calls it ("N", "Q69"). `headline` is the top alert's own
    header text, cleaned to plain ASCII, or None when the line is ok. `now` is True when
    that alert is in effect at the time of the fetch, False when it starts later today.
    `alerts` counts the alerts touching the line today."""

    line: str
    kind: str
    status: str = "ok"
    headline: str | None = None
    now: bool = False
    alerts: int = 0


@dataclass(frozen=True)
class CityStatus:
    """`lines` holds the lines that apply on `day_local` (M on weekdays, F at weekends), in
    the configured order, subways first. A part that has never been fetched is None/empty
    with its `*_fetched_at_utc` None; the renderer says so rather than guessing."""

    day_local: str
    weather: Weather | None = None
    lines: tuple[LineStatus, ...] = ()
    transit_fetched_at_utc: str | None = None
