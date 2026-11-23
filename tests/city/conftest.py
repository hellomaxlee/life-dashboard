from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.city.parse import MERCURY, WatchedLine
from app.config import CityConfig

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures" / "city"
NY = ZoneInfo("America/New_York")
CITY = CityConfig(
    enabled=True,
    latitude=40.77,
    longitude=-73.94,
    subway=("N", "W"),
    subway_weekday=("M",),
    subway_weekend=("F",),
    bus=("Q103", "Q66", "Q69", "B62"),
)
SUNDAY_NIGHT = datetime(2026, 10, 4, 23, 0, tzinfo=NY)
MONDAY_MORNING = datetime(2026, 10, 5, 8, 30, tzinfo=NY)
EMPTY_FEED = {"header": {"gtfs_realtime_version": "2.0"}, "entity": []}
N_LINE = WatchedLine("N", "subway")


def ny(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=NY)


def load(folder: str, name: str) -> dict:
    return json.loads((FIXTURES / folder / f"{name}.json").read_text())


def alert(
    route: str,
    alert_type: str,
    periods: list[tuple[datetime, datetime | None]] | None = None,
    updated: int = 0,
    text: str | None = None,
) -> dict:
    """One alert in the feed's own shape. `periods` None = no active_period key at all."""
    built: dict = {
        "informed_entity": [{"agency_id": "MTASBWY", "route_id": route}],
        "header_text": {
            "translation": [
                {"text": text or f"{alert_type} on {route}", "language": "en"},
                {"text": f"<p>{text or alert_type}</p>", "language": "en-html"},
            ]
        },
        MERCURY: {"alert_type": alert_type, "created_at": updated, "updated_at": updated},
    }
    if periods is not None:
        built["active_period"] = [
            {
                "start": int(start.timestamp()),
                **({} if end is None else {"end": int(end.timestamp())}),
            }
            for start, end in periods
        ]
    return built


def feed(*alerts: dict) -> dict:
    return {**EMPTY_FEED, "entity": [{"id": str(i), "alert": a} for i, a in enumerate(alerts)]}


@pytest.fixture
def city_settings(settings):
    from dataclasses import replace

    return replace(settings, city=CITY)
