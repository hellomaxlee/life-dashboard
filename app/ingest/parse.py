"""Health Auto Export JSON v2 -> typed records. Pure functions, no I/O.

Field names follow help.healthyapps.dev/en/health-auto-export/export-format/
{workouts,health-metrics}.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.timeutil import local_day, parse_hae_datetime, to_utc_iso

DISTANCE_TO_M = {"m": 1.0, "km": 1000.0, "mi": 1609.344, "yd": 0.9144, "ft": 0.3048}
ENERGY_TO_KCAL = {"kcal": 1.0, "cal": 1.0, "kj": 1.0 / 4.184}

STEP_COUNT = "step_count"
SLEEP_ANALYSIS = "sleep_analysis"
WELLNESS_METRICS = {
    "heart_rate_variability": "heart_rate_variability",
    "hrv": "heart_rate_variability",
    "resting_heart_rate": "resting_heart_rate",
    "vo2_max": "vo2_max",
    "vo2max": "vo2_max",
    "vo₂_max": "vo2_max",
    "time_in_daylight": "time_in_daylight",
}
UNKNOWN_SOURCE = "unknown"


@dataclass(frozen=True)
class HrSample:
    ts_utc: str
    bpm_min: float | None
    bpm_avg: float | None
    bpm_max: float | None
    source: str


@dataclass(frozen=True)
class Workout:
    external_id: str
    source_app: str
    type: str
    start_utc: str
    end_utc: str
    duration_s: int
    distance_m: float | None
    energy_kcal: float | None
    avg_hr: float | None
    max_hr: float | None
    hr_samples: tuple[HrSample, ...]


@dataclass(frozen=True)
class SleepSession:
    wake_day_local: str
    start_utc: str
    end_utc: str
    in_bed_s: int | None
    asleep_s: int | None
    core_s: int | None
    deep_s: int | None
    rem_s: int | None
    awake_s: int | None
    source: str


@dataclass(frozen=True)
class DailyValue:
    day_local: str
    metric: str
    value: float
    units: str | None
    source: str


@dataclass
class ParsedPayload:
    workouts: list[Workout] = field(default_factory=list)
    sleep: list[SleepSession] = field(default_factory=list)
    steps: list[DailyValue] = field(default_factory=list)
    wellness: list[DailyValue] = field(default_factory=list)
    unknown_metrics: list[str] = field(default_factory=list)
    skipped_rows: int = 0


def normalize_metric_name(name: str) -> str:
    text = re.sub(r"\s+", "_", name.strip().lower())
    return WELLNESS_METRICS.get(text, text)


def _qty(node: Any) -> float | None:
    if node is None:
        return None
    if isinstance(node, dict):
        node = node.get("qty")
    if isinstance(node, int | float) and not isinstance(node, bool):
        return float(node)
    return None


def _units(node: Any) -> str | None:
    return node.get("units") if isinstance(node, dict) else None


def _distance_m(node: Any) -> float | None:
    qty = _qty(node)
    if qty is None:
        return None
    return qty * DISTANCE_TO_M.get((_units(node) or "m").lower(), 1.0)


def _energy_kcal(node: Any) -> float | None:
    qty = _qty(node)
    if qty is None:
        return None
    return qty * ENERGY_TO_KCAL.get((_units(node) or "kcal").lower(), 1.0)


def _hours_to_s(node: Any) -> int | None:
    qty = _qty(node)
    return None if qty is None else int(round(qty * 3600))


def _summary_hr(workout: dict[str, Any], key: str) -> float | None:
    direct = _qty(workout.get(f"{key}HeartRate"))
    if direct is not None:
        return direct
    nested = workout.get("heartRate")
    if isinstance(nested, dict):
        return _qty(nested.get(key))
    return None


def _series_sources(workout: dict[str, Any]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for value in workout.values():
        if not isinstance(value, list):
            continue
        for entry in value:
            if isinstance(entry, dict) and isinstance(entry.get("source"), str):
                counts[entry["source"]] += 1
    return counts


def workout_source_app(workout: dict[str, Any]) -> str:
    """v2 has no top-level source; the most frequent time-series `source` stands in for it."""
    top = workout.get("source")
    if isinstance(top, str) and top.strip():
        return top.strip()
    counts = _series_sources(workout)
    if counts:
        return counts.most_common(1)[0][0]
    return UNKNOWN_SOURCE


def parse_hr_samples(workout: dict[str, Any], source_app: str, tz: str) -> tuple[HrSample, ...]:
    samples: dict[tuple[str, str], HrSample] = {}
    for entry in workout.get("heartRateData") or []:
        if not isinstance(entry, dict) or "date" not in entry:
            continue
        ts = to_utc_iso(parse_hae_datetime(str(entry["date"]), tz))
        source = str(entry.get("source") or source_app)
        avg = _qty(entry.get("Avg"))
        if avg is None:
            avg = _qty(entry.get("qty"))
        sample = HrSample(ts, _qty(entry.get("Min")), avg, _qty(entry.get("Max")), source)
        samples[(ts, source)] = sample
    return tuple(sorted(samples.values(), key=lambda s: (s.ts_utc, s.source)))


def parse_workout(workout: dict[str, Any], tz: str) -> Workout | None:
    external_id = workout.get("id")
    if not external_id or "start" not in workout or "end" not in workout:
        return None
    start = parse_hae_datetime(str(workout["start"]), tz)
    end = parse_hae_datetime(str(workout["end"]), tz)
    duration = workout.get("duration")
    duration_s = (
        int(round(float(duration))) if duration is not None else int((end - start).total_seconds())
    )
    source_app = workout_source_app(workout)
    return Workout(
        external_id=str(external_id),
        source_app=source_app,
        type=str(workout.get("name") or "Unknown"),
        start_utc=to_utc_iso(start),
        end_utc=to_utc_iso(end),
        duration_s=duration_s,
        distance_m=_distance_m(workout.get("distance")),
        energy_kcal=_energy_kcal(workout.get("activeEnergyBurned")),
        avg_hr=_summary_hr(workout, "avg"),
        max_hr=_summary_hr(workout, "max"),
        hr_samples=parse_hr_samples(workout, source_app, tz),
    )


def _row_source(row: dict[str, Any], fallback: str) -> str:
    source = row.get("source")
    return str(source) if isinstance(source, str) and source.strip() else fallback


def parse_sleep_row(row: dict[str, Any], tz: str) -> SleepSession | None:
    start_text = row.get("sleepStart") or row.get("inBedStart") or row.get("startDate")
    end_text = row.get("sleepEnd") or row.get("inBedEnd") or row.get("endDate")
    if not start_text or not end_text:
        return None
    start = parse_hae_datetime(str(start_text), tz)
    end = parse_hae_datetime(str(end_text), tz)
    asleep = row.get("asleep")
    if asleep is None:
        asleep = row.get("totalSleep")
    return SleepSession(
        wake_day_local=local_day(end, tz),
        start_utc=to_utc_iso(start),
        end_utc=to_utc_iso(end),
        in_bed_s=_hours_to_s(row.get("inBed")),
        asleep_s=_hours_to_s(asleep),
        core_s=_hours_to_s(row.get("core")),
        deep_s=_hours_to_s(row.get("deep")),
        rem_s=_hours_to_s(row.get("rem")),
        awake_s=_hours_to_s(row.get("awake")),
        source=_row_source(row, "aggregate"),
    )


def aggregate_day(row_date: str, tz: str) -> str:
    """Calendar day of a day-aggregated row.

    Health Auto Export sums a day in the phone's own zone and stamps it midnight there, so the
    stamp's own calendar date is the day that was lived; converting through UTC would shift a
    day summed abroad. At home the two agree.
    """
    moment: datetime = parse_hae_datetime(row_date, tz)
    return moment.date().isoformat()


def parse_metric(metric: dict[str, Any], tz: str, out: ParsedPayload) -> None:
    raw_name = metric.get("name")
    if not isinstance(raw_name, str):
        out.skipped_rows += 1
        return
    name = normalize_metric_name(raw_name)
    units = metric.get("units")
    rows = metric.get("data") or []
    if name == SLEEP_ANALYSIS:
        for row in rows:
            session = parse_sleep_row(row, tz) if isinstance(row, dict) else None
            if session is None:
                out.skipped_rows += 1
            else:
                out.sleep.append(session)
        return
    if name != STEP_COUNT and name not in WELLNESS_METRICS.values():
        if raw_name not in out.unknown_metrics:
            out.unknown_metrics.append(raw_name)
        return
    for row in rows:
        if not isinstance(row, dict) or "date" not in row:
            out.skipped_rows += 1
            continue
        qty = _qty(row.get("qty"))
        if qty is None:
            out.skipped_rows += 1
            continue
        value = DailyValue(
            day_local=aggregate_day(str(row["date"]), tz),
            metric=name,
            value=qty,
            units=str(units) if units is not None else None,
            source=_row_source(row, "aggregate"),
        )
        (out.steps if name == STEP_COUNT else out.wellness).append(value)


def parse_payload(payload: Any, tz: str) -> ParsedPayload:
    out = ParsedPayload()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise ValueError("payload has no 'data' object")
    for workout in data.get("workouts") or []:
        parsed = parse_workout(workout, tz) if isinstance(workout, dict) else None
        if parsed is None:
            out.skipped_rows += 1
        else:
            out.workouts.append(parsed)
    for metric in data.get("metrics") or []:
        if isinstance(metric, dict):
            parse_metric(metric, tz, out)
        else:
            out.skipped_rows += 1
    return out
