"""Table `city_snapshots`: the latest parsed transit snapshot and the latest parsed weather
snapshot, one row per kind, each replaced by the next successful fetch.

The transit row holds a status for every configured line together with the config groups
they came from, so `load_status` can pick the weekday or the weekend lines for the day it is
asked about without reading the config. Whether a snapshot is too old to show is the
renderer's call; it gets both fetch times.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime

from app.city.model import KINDS, STATUS_ORDER, CityStatus, HourWeather, LineStatus, Weather
from app.city.parse import lines_for_day
from app.config import CityConfig
from app.timeutil import to_utc_iso

TRANSIT = "transit"
WEATHER = "weather"
GROUPS = ("subway", "subway_weekday", "subway_weekend", "bus")

log = logging.getLogger(__name__)
_unreadable_logged: set[tuple[str, str]] = set()


def _save(conn: sqlite3.Connection, kind: str, snapshot: dict, fetched_at: datetime) -> None:
    conn.execute(
        "INSERT INTO city_snapshots (kind, snapshot_json, fetched_at_utc) VALUES (?, ?, ?) "
        "ON CONFLICT (kind) DO UPDATE SET snapshot_json = excluded.snapshot_json, "
        "fetched_at_utc = excluded.fetched_at_utc",
        (kind, json.dumps(snapshot, sort_keys=True), to_utc_iso(fetched_at)),
    )


def _row(conn: sqlite3.Connection, kind: str) -> tuple[dict, str] | None:
    row = conn.execute(
        "SELECT snapshot_json, fetched_at_utc FROM city_snapshots WHERE kind = ?", (kind,)
    ).fetchone()
    if row is None:
        return None
    try:
        snapshot = json.loads(row["snapshot_json"])
        if not isinstance(snapshot, dict):
            raise ValueError("not an object")
    except ValueError as exc:
        _log_unreadable(kind, str(row["fetched_at_utc"]), exc)
        return None
    return snapshot, str(row["fetched_at_utc"])


def _log_unreadable(kind: str, stamp: str, exc: Exception) -> None:
    if (kind, stamp) not in _unreadable_logged:
        _unreadable_logged.add((kind, stamp))
        log.error("city %s snapshot of %s cannot be read: %s", kind, stamp, exc)


def save_transit(
    conn: sqlite3.Connection,
    city: CityConfig,
    statuses: Sequence[LineStatus],
    fetched_at: datetime,
) -> None:
    """Replace the transit snapshot. `statuses` covers every configured line."""
    snapshot = {
        "groups": {group: list(getattr(city, group)) for group in GROUPS},
        "lines": [asdict(status) for status in statuses],
    }
    _save(conn, TRANSIT, snapshot, fetched_at)


def save_weather(
    conn: sqlite3.Connection,
    weather: Weather,
    fetched_at: datetime,
    alerts_fetched_at_utc: str | None,
) -> None:
    """Replace the weather snapshot. `alerts_fetched_at_utc` is when `weather.alerts` came
    from the weather service, which is earlier than `fetched_at` when they were carried
    over a failed alerts fetch, and None when they are not known at all."""
    body = asdict(weather)
    body.pop("fetched_at_utc")
    _save(conn, WEATHER, {**body, "alerts_fetched_at_utc": alerts_fetched_at_utc}, fetched_at)


def stored_weather_alerts(conn: sqlite3.Connection) -> tuple[tuple[str, ...], str | None]:
    """(alerts, when they were fetched) of the stored weather snapshot; ((), None) without."""
    found = _row(conn, WEATHER)
    if found is None:
        return (), None
    alerts, stamp = found[0].get("alerts"), found[0].get("alerts_fetched_at_utc")
    if not isinstance(alerts, list) or not isinstance(stamp, str):
        return (), None
    return tuple(str(name) for name in alerts), stamp


def _line(raw: object) -> LineStatus:
    if not isinstance(raw, dict):
        raise ValueError("a line that is not an object")
    status = LineStatus(
        line=str(raw["line"]),
        kind=str(raw["kind"]),
        status=str(raw["status"]),
        headline=None if raw.get("headline") is None else str(raw["headline"]),
        now=bool(raw.get("now", False)),
        alerts=int(raw.get("alerts", 0)),
    )
    if status.status not in STATUS_ORDER or status.kind not in KINDS:
        raise ValueError(f"line {status.line!r} has status {status.status!r}, kind {status.kind!r}")
    return status


def _lines(snapshot: dict, day_local: str) -> tuple[LineStatus, ...]:
    groups = snapshot["groups"]
    stored = {(s.line, s.kind): s for s in map(_line, snapshot["lines"])}
    wanted = lines_for_day(*(groups.get(group, []) for group in GROUPS), day_local)
    return tuple(stored[key] for w in wanted if (key := (w.line, w.kind)) in stored)


def _weather(snapshot: dict, fetched_at_utc: str) -> Weather:
    def number(name: str) -> float | None:
        return None if snapshot.get(name) is None else float(snapshot[name])

    def whole(name: str) -> int | None:
        return None if snapshot.get(name) is None else int(snapshot[name])

    return Weather(
        temp_f=number("temp_f"),
        feels_f=number("feels_f"),
        code=whole("code"),
        high_f=number("high_f"),
        low_f=number("low_f"),
        precip_pct_max=whole("precip_pct_max"),
        hours=tuple(
            HourWeather(
                hour_local=int(hour["hour_local"]),
                temp_f=float(hour["temp_f"]),
                precip_pct=int(hour["precip_pct"]),
                code=int(hour["code"]),
                tomorrow=bool(hour.get("tomorrow", False)),
            )
            for hour in snapshot.get("hours", [])
        ),
        alerts=tuple(str(name) for name in snapshot.get("alerts", [])),
        fetched_at_utc=fetched_at_utc,
    )


def load_status(conn: sqlite3.Connection, day_local: str) -> CityStatus | None:
    """The stored city status for `day_local` (home-timezone YYYY-MM-DD): the lines that
    apply on that weekday, the weather, and when each was fetched. None when nothing has
    ever been fetched. A stored row that cannot be read counts as never fetched (logged
    once per row)."""
    lines: tuple[LineStatus, ...] = ()
    transit_at: str | None = None
    weather: Weather | None = None
    transit = _row(conn, TRANSIT)
    if transit is not None:
        try:
            lines, transit_at = _lines(transit[0], day_local), transit[1]
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            _log_unreadable(TRANSIT, transit[1], exc)
    stored = _row(conn, WEATHER)
    if stored is not None:
        try:
            weather = _weather(*stored)
        except (KeyError, TypeError, ValueError) as exc:
            _log_unreadable(WEATHER, stored[1], exc)
    if transit_at is None and weather is None:
        return None
    return CityStatus(
        day_local=day_local, weather=weather, lines=lines, transit_fetched_at_utc=transit_at
    )
