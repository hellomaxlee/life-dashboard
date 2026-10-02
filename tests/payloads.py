"""Hand-built Health Auto Export v2 payloads and small ingest helpers for ordering tests."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from app.ingest import health

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def post(client, body: bytes):
    return client.post("/ingest/health", content=body, headers={"content-type": "application/json"})


def steps_payload(days: dict[str, int], note: str = "") -> bytes:
    data = [{"date": f"{d} 00:00:00 -0400", "qty": v, "source": "iPhone"} for d, v in days.items()]
    body: dict = {"data": {"metrics": [{"name": "step_count", "units": "count", "data": data}]}}
    if note:
        body["note"] = note
    return json.dumps(body).encode()


def sleep_payload(end: str, hours: float, start: str = "2026-09-29 23:00:00 -0400") -> bytes:
    row = {"date": "2026-09-30", "asleep": hours, "sleepStart": start, "sleepEnd": end}
    row["inBed"] = hours
    body = {"data": {"metrics": [{"name": "sleep_analysis", "units": "hr", "data": [row]}]}}
    return json.dumps(body).encode()


def wellness_payload(value: float, source: str, day: str = "2026-09-30") -> bytes:
    row = {"date": f"{day} 00:00:00 -0400", "qty": value, "source": source}
    metric = {"name": "resting_heart_rate", "units": "bpm", "data": [row]}
    return json.dumps({"data": {"metrics": [metric]}}).encode()


def workout(
    id_: str, start: str, minutes: int, source: str | None = None, hr: bool = True, name="Running"
) -> dict:
    hh, mm = (int(x) for x in start.split(":"))
    first = hh * 60 + mm

    def stamp(minute: int) -> str:
        return f"2026-09-22 {minute // 60:02d}:{minute % 60:02d}:00 -0400"

    record: dict = {
        "id": id_,
        "name": name,
        "start": stamp(first),
        "end": stamp(first + minutes),
        "duration": minutes * 60,
        "distance": {"qty": 4.0, "units": "mi"},
    }
    if source and hr:
        record["heartRateData"] = [
            {"date": stamp(first + i), "Min": 130, "Avg": 150, "Max": 160, "source": source}
            for i in range(0, minutes, 2)
        ]
    elif source:
        record["activeEnergy"] = [{"date": stamp(first), "qty": 1, "source": source}]
    return record


def workouts_payload(*workouts: dict, note: str = "") -> bytes:
    return json.dumps({"data": {"workouts": list(workouts)}, "note": note}).encode()


def tick_clock(monkeypatch, step: timedelta = timedelta(minutes=1)) -> None:
    moments = (T0 + step * n for n in range(1000))
    monkeypatch.setattr(health, "now_utc", lambda: next(moments))


def fail_next_stores(monkeypatch, failures: int = 1) -> None:
    """The next `failures` calls to store_payload die the way a transient db error would."""
    real, calls = health.store_payload, []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) <= failures:
            raise sqlite3.OperationalError("simulated db hiccup")
        return real(*args, **kwargs)

    monkeypatch.setattr(health, "store_payload", flaky)


def steps(db) -> dict[str, int]:
    return {r["day_local"]: r["steps"] for r in db.execute("SELECT * FROM steps_daily")}
