"""Health Auto Export JSON v2 -> typed records. Pure functions, no I/O.

Field names follow help.healthyapps.dev/en/health-auto-export/export-format/
{workouts,health-metrics}.

A Health Metrics push comes in one of two shapes, and the parser accepts both:

- day summaries (Summarize Data on, Time Grouping Days): one row per day per metric, stamped
  midnight in the phone's zone; a sleep row carries the night's totals (`asleep`, `inBed`,
  `sleepStart`, ...);
- samples (the shape the real pushes have, issue #3): tens of thousands of rows per metric,
  one per HealthKit sample or per-second slice, and one `sleep_analysis` row per sleep-stage
  segment (`value` Core / Deep / REM / Awake / InBed / Asleep with `startDate` / `endDate`).

Detection is per metric. A sleep row is a summary when it has a night total or a night start
(`asleep`, `totalSleep`, `inBed`, `sleepStart`, `inBedStart`), a segment when it has a stage
`value` and a start and end. Every other metric is day-summed when all its rows are stamped
at midnight (00:00:00 in their own offset, or a bare date) and samples otherwise.

Both shapes end as one value per home-timezone day per metric (`DailyValue`) and one
`SleepSession` per night or nap, so the store and everything after it see one shape.
Day-summed rows keep the phone's calendar date (notes.txt § Day-grouped metrics); samples are
projected to the home day. Per-day rules, named in the stored source label when applied:

- steps: sum per (day, source label); the day's figure is the LARGEST source total, never the
  sum across sources, because HealthKit's own de-duplication of Watch and iPhone steps is not
  applied by the export ("<source> (summed)");
- time in daylight: as steps, minutes ("<source> (summed)");
- HRV: mean of every reading of the day across sources ("<sources> (daily mean)"). The
  Watch reads HRV through the day, not only overnight; the overnight subset is not identified;
- resting heart rate, VO2 max: the latest reading of the day ("<source> (latest)").

Sleep sessions are built per source from stage segments sorted by start: a segment that
starts within `sleep_gap_min` of the running session's end joins it, otherwise it starts a
new one. `in_bed_s` is the session's span; `asleep_s` is Core + Deep + REM + unspecified
Asleep seconds (never Awake or InBed); the wake day is the home-timezone day of the end.
A short session (a nap) is stored like a night.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from app.timeutil import from_utc_iso, local_day, parse_hae_datetime, to_utc_iso

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
SUMMED_METRICS = {STEP_COUNT, "time_in_daylight"}
MEAN_METRICS = {"heart_rate_variability"}
LATEST_METRICS = {"resting_heart_rate", "vo2_max"}
HEART_RATE = "heart_rate"
HEART_RATE_FIELDS = {"Max": "heart_rate_max", "Avg": "heart_rate_avg", "Min": "heart_rate_min"}
IGNORED_METRICS: set[str] = set()
UNKNOWN_SOURCE = "unknown"
DEFAULT_SLEEP_GAP_MIN = 60

SLEEP_SUMMARY_KEYS = ("asleep", "totalSleep", "inBed", "sleepStart", "inBedStart")
ASLEEP_STAGES = {"core": "core", "deep": "deep", "rem": "rem", "asleep": "asleep"}
AWAKE_STAGE = "awake"
IN_BED_STAGE = "inbed"

_HAE_MINUTE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}):\d{2}( [+-]\d{4})$")


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
class SleepSegment:
    start: datetime
    end: datetime
    stage: str
    source: str


@dataclass(frozen=True)
class DailyValue:
    day_local: str
    metric: str
    value: float
    units: str | None
    source: str


@dataclass(frozen=True)
class Sample:
    day_local: str
    value: float
    source: str
    order: tuple[str, int]


@dataclass(frozen=True)
class HrMinute:
    day_local: str
    minute_utc: str
    hr_min: float | None
    hr_avg: float | None
    hr_max: float | None


@dataclass
class ParsedPayload:
    workouts: list[Workout] = field(default_factory=list)
    sleep: list[SleepSession] = field(default_factory=list)
    steps: list[DailyValue] = field(default_factory=list)
    wellness: list[DailyValue] = field(default_factory=list)
    hr_minutes: list[HrMinute] = field(default_factory=list)
    unknown_metrics: list[str] = field(default_factory=list)
    skipped_rows: int = 0
    sample_rows: int = 0
    first_day_local: str | None = None

    def saw_day(self, day_local: str) -> None:
        if self.first_day_local is None or day_local < self.first_day_local:
            self.first_day_local = day_local


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


def is_sleep_summary_row(row: dict[str, Any]) -> bool:
    return any(key in row for key in SLEEP_SUMMARY_KEYS)


def parse_sleep_row(row: dict[str, Any], tz: str) -> SleepSession | None:
    start_text = row.get("sleepStart") or row.get("inBedStart") or row.get("startDate")
    end_text = row.get("sleepEnd") or row.get("inBedEnd") or row.get("endDate")
    if not start_text or not end_text:
        return None
    start = parse_hae_datetime(str(start_text), tz)
    end = parse_hae_datetime(str(end_text), tz)
    # `asleep` is only the unspecified stage (0 from a Watch); the night is `totalSleep`.
    asleep = row.get("totalSleep")
    if not isinstance(asleep, int | float) or isinstance(asleep, bool) or asleep <= 0:
        asleep = row.get("asleep")
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


def parse_sleep_segment(row: dict[str, Any], tz: str) -> SleepSegment | None:
    stage = row.get("value")
    start_text = row.get("startDate") or row.get("start")
    end_text = row.get("endDate") or row.get("end")
    if not isinstance(stage, str) or not start_text or not end_text:
        return None
    start = parse_hae_datetime(str(start_text), tz)
    end = parse_hae_datetime(str(end_text), tz)
    if end < start:
        return None
    return SleepSegment(start, end, stage_key(stage), _row_source(row, "aggregate"))


def stage_key(stage: str) -> str:
    """'Core', 'REM', 'In Bed', 'AsleepCore', 'asleep_unspecified' -> core, rem, inbed, core,
    asleep."""
    key = re.sub(r"[\s_]+", "", stage.strip().lower())
    if key.startswith("asleep") and len(key) > len("asleep"):
        rest = key[len("asleep") :]
        return "asleep" if rest == "unspecified" else rest
    return key


def _sum_or_none(seconds: list[int]) -> int | None:
    return sum(seconds) if seconds else None


def _session_from_segments(group: list[SleepSegment], tz: str) -> SleepSession:
    start = min(s.start for s in group)
    end = max(s.end for s in group)
    by_stage: dict[str, list[int]] = defaultdict(list)
    for seg in group:
        by_stage[seg.stage].append(int((seg.end - seg.start).total_seconds()))
    asleep = [sec for stage in ASLEEP_STAGES for sec in by_stage.get(stage, [])]
    return SleepSession(
        wake_day_local=local_day(end, tz),
        start_utc=to_utc_iso(start),
        end_utc=to_utc_iso(end),
        in_bed_s=int((end - start).total_seconds()),
        asleep_s=_sum_or_none(asleep),
        core_s=_sum_or_none(by_stage.get("core", [])),
        deep_s=_sum_or_none(by_stage.get("deep", [])),
        rem_s=_sum_or_none(by_stage.get("rem", [])),
        awake_s=_sum_or_none(by_stage.get(AWAKE_STAGE, [])),
        source=group[0].source,
    )


def build_sleep_sessions(
    segments: list[SleepSegment], tz: str, gap_min: float = DEFAULT_SLEEP_GAP_MIN
) -> list[SleepSession]:
    """Nights and naps from stage segments: per source, segments sorted by start, a gap of
    more than `gap_min` minutes between one segment's end and the next's start ends the
    session. Returns sessions ordered by (end, source)."""
    gap_s = gap_min * 60
    by_source: dict[str, list[SleepSegment]] = defaultdict(list)
    for seg in segments:
        by_source[seg.source].append(seg)
    sessions: list[SleepSession] = []
    for source in sorted(by_source):
        ordered = sorted(by_source[source], key=lambda s: (s.start, s.end))
        group: list[SleepSegment] = []
        group_end: datetime | None = None
        for seg in ordered:
            if group_end is not None and (seg.start - group_end).total_seconds() > gap_s:
                sessions.append(_session_from_segments(group, tz))
                group, group_end = [], None
            group.append(seg)
            group_end = seg.end if group_end is None else max(group_end, seg.end)
        if group:
            sessions.append(_session_from_segments(group, tz))
    return sorted(sessions, key=lambda s: (s.end_utc, s.source))


def aggregate_day(row_date: str, tz: str) -> str:
    """Calendar day of a day-aggregated row.

    Health Auto Export sums a day in the phone's own zone and stamps it midnight there, so the
    stamp's own calendar date is the day that was lived; converting through UTC would shift a
    day summed abroad. At home the two agree.
    """
    moment: datetime = parse_hae_datetime(row_date, tz)
    return moment.date().isoformat()


def is_midnight_stamp(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) == 10:
        return True
    return stripped[11:19] == "00:00:00" or stripped[10:19] == "T00:00:00"


def rows_are_day_summaries(rows: list[Any]) -> bool:
    """Day-summed rows are all stamped midnight in their own offset; samples are not."""
    stamped = [row["date"] for row in rows if isinstance(row, dict) and "date" in row]
    return bool(stamped) and all(is_midnight_stamp(str(d)) for d in stamped)


class SampleDays:
    """Home-timezone day of a sample stamp, memoised per minute (a push holds ~100k stamps
    at second resolution; the day never changes inside a minute, whatever the offsets)."""

    def __init__(self, tz: str) -> None:
        self.tz = tz
        self.zone = ZoneInfo(tz)
        self.memo: dict[str, str] = {}

    def day(self, text: str) -> str:
        match = _HAE_MINUTE.match(text)
        key = match.group(1) + match.group(2) if match else text
        day = self.memo.get(key)
        if day is None:
            day = parse_hae_datetime(text, self.tz).astimezone(self.zone).date().isoformat()
            self.memo[key] = day
        return day


def _sort_key(text: str, tz: str) -> str:
    return to_utc_iso(parse_hae_datetime(text, tz))


def reduce_samples(
    name: str, samples: list[Sample], units: str | None, summed_by_phone: bool
) -> list[DailyValue]:
    """One value per day from a metric's rows, by the metric's rule (module docstring)."""
    by_day: dict[str, list[Sample]] = defaultdict(list)
    for sample in samples:
        by_day[sample.day_local].append(sample)
    out: list[DailyValue] = []
    for day in sorted(by_day):
        rows = by_day[day]
        if name in MEAN_METRICS:
            value = sum(r.value for r in rows) / len(rows)
            source = "|".join(sorted({r.source for r in rows}))
            rule = "daily mean"
        elif name in LATEST_METRICS:
            latest = max(rows, key=lambda r: r.order)
            value, source, rule = latest.value, latest.source, "latest"
        else:
            totals: dict[str, float] = defaultdict(float)
            for r in rows:
                totals[r.source] += r.value
            source = max(sorted(totals), key=lambda s: totals[s])
            value, rule = totals[source], "summed"
        label = source if summed_by_phone else f"{source} ({rule})"
        out.append(DailyValue(day, name, value, units, label))
    return out


def parse_sleep_metric(rows: list[Any], tz: str, out: ParsedPayload, sleep_gap_min: float) -> None:
    segments: list[SleepSegment] = []
    for row in rows:
        if not isinstance(row, dict):
            out.skipped_rows += 1
            continue
        if is_sleep_summary_row(row):
            session = parse_sleep_row(row, tz)
            if session is None:
                out.skipped_rows += 1
            else:
                out.sleep.append(session)
                out.saw_day(local_day(from_utc_iso(session.start_utc), tz))
            continue
        segment = parse_sleep_segment(row, tz)
        if segment is None:
            out.skipped_rows += 1
        else:
            segments.append(segment)
            out.sample_rows += 1
            out.saw_day(local_day(segment.start, tz))
    out.sleep.extend(build_sleep_sessions(segments, tz, sleep_gap_min))


def parse_metric(
    metric: dict[str, Any],
    tz: str,
    out: ParsedPayload,
    sleep_gap_min: float = DEFAULT_SLEEP_GAP_MIN,
) -> None:
    raw_name = metric.get("name")
    if not isinstance(raw_name, str):
        out.skipped_rows += 1
        return
    name = normalize_metric_name(raw_name)
    units = metric.get("units")
    rows = metric.get("data") or []
    if name == SLEEP_ANALYSIS:
        parse_sleep_metric(rows, tz, out, sleep_gap_min)
        return
    if name in IGNORED_METRICS:
        return
    if name == HEART_RATE:
        parse_heart_rate_metric(rows, tz, out, str(units) if units is not None else None)
        return
    if name != STEP_COUNT and name not in WELLNESS_METRICS.values():
        if raw_name not in out.unknown_metrics:
            out.unknown_metrics.append(raw_name)
        return
    summed_by_phone = rows_are_day_summaries(rows)
    days = SampleDays(tz)
    samples: list[Sample] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or "date" not in row:
            out.skipped_rows += 1
            continue
        qty = _qty(row.get("qty"))
        if qty is None:
            out.skipped_rows += 1
            continue
        stamp = str(row["date"])
        day = aggregate_day(stamp, tz) if summed_by_phone else days.day(stamp)
        order = (_sort_key(stamp, tz), index) if name in LATEST_METRICS else ("", index)
        samples.append(Sample(day, qty, _row_source(row, "aggregate"), order))
        out.saw_day(day)
    if not summed_by_phone:
        out.sample_rows += len(samples)
    unit_text = str(units) if units is not None else None
    reduced = reduce_samples(name, samples, unit_text, summed_by_phone)
    (out.steps if name == STEP_COUNT else out.wellness).extend(reduced)


def parse_heart_rate_metric(
    rows: list[Any], tz: str, out: ParsedPayload, units: str | None
) -> None:
    """The day's Max/Avg/Min heart rate as three wellness values; the day's max and min
    win across rows, the average is a mean. No samples: these are whole-day figures."""
    by_day: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    sources: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if not isinstance(row, dict) or "date" not in row:
            out.skipped_rows += 1
            continue
        stamp = str(row["date"])
        if is_midnight_stamp(stamp):
            day = aggregate_day(stamp, tz)
        else:
            moment = parse_hae_datetime(stamp, tz)
            day = local_day(moment, tz)
            out.hr_minutes.append(
                HrMinute(
                    day,
                    to_utc_iso(moment.replace(second=0, microsecond=0)),
                    _qty(row.get("Min")),
                    _qty(row.get("Avg")),
                    _qty(row.get("Max")),
                )
            )
        for field_name, metric in HEART_RATE_FIELDS.items():
            value = _qty(row.get(field_name))
            if value is not None:
                by_day[day][metric].append(value)
                sources[day].add(_row_source(row, "aggregate"))
        out.saw_day(day)
    for day in sorted(by_day):
        source = "|".join(sorted(sources[day]))
        for metric, values in by_day[day].items():
            if metric == "heart_rate_max":
                value = max(values)
            elif metric == "heart_rate_min":
                value = min(values)
            else:
                value = sum(values) / len(values)
            out.wellness.append(DailyValue(day, metric, value, units, source))


def parse_payload(
    payload: Any, tz: str, sleep_gap_min: float = DEFAULT_SLEEP_GAP_MIN
) -> ParsedPayload:
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
            parse_metric(metric, tz, out, sleep_gap_min)
        else:
            out.skipped_rows += 1
    return out


def window_start_utc(parsed: ParsedPayload, tz: str) -> str | None:
    """Midnight (home timezone) of the earliest day the payload carries: where the export's
    date window begins, and where a night that began earlier was cut."""
    if parsed.first_day_local is None:
        return None
    moment = datetime.combine(
        datetime.fromisoformat(parsed.first_day_local).date(), time.min, ZoneInfo(tz)
    )
    return to_utc_iso(moment)
