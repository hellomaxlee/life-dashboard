"""The fuller bandwidth score, which colours the Books lamp (notes.txt § Goal model,
"Bandwidth, the fuller score"). Max, 2026-11-20: robust "isn't necessarily to decrease
the number of variables ... partially means defensibility and partially means
completeness as a proxy for my actual bandwidth"; built as a shadow first, then, the same
day: "I would like to switch to the in general fuller score".

Ten terms in five domains, each a number inside [-3, 3] with higher meaning more to give
(TERM_RANGE has each term's own range), each with a fixed weight in hundredths:

    B_full = 50 + (1 / 6) * sum(weight_i * z_i)

so B_full is in [0, 87.67] by construction, with no terminal clamp. An absent term adds 0
and the weights are never renormalised; `weight_present` in the trace says how much of the
hundred backed the score. A weighted mean of ten terms spreads less than the two-term
score's sum of two (bandwidth.py), by the ratio of the root-sum-squares of their weights,
so the cue line and the colour stops are that ratio of the two-term score's
(CUE_WEIGHTED_SUM, FULL_RED, FULL_GREEN), computed from WEIGHTS and never typed in. The
cue and the lamp both need MIN_CUE_WEIGHT of the hundred present: a thinner day is the
plain gold lamp. `bandwidth_full_lamp` is what the lamp is coloured by: today's score, or
yesterday's while today, before noon, awaits its night.

The weights are priors (reliability on Max's data times a stated guess at relevance), not
fitted: nothing is logged that they could be fitted to. See the notes for what the two
reviews found, above all that the sleep bands carry most of the score's movement.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.metrics.bandwidth import (
    MIN_HISTORY,
    NightSignal,
    Term,
    _baseline,
    _censored,
    _clamp,
    _history,
    _valid_sleep,
    sleep_band_z,
    sleep_hr_term,
    sleep_term,
)
from app.metrics.keys import round_half_up
from app.metrics.sleep import NightStages

WEIGHTS: dict[str, int] = {
    "sleep": 30,
    "recent_nights": 14,
    "sleep_quality": 5,
    "sleep_timing": 12,
    "sleep_hr": 24,
    "hrv": 4,
    "resting_hr": 3,
    "load": 2,
    "movement": 4,
    "daylight": 2,
}
TERM_RANGE: dict[str, tuple[float, float]] = {
    "sleep": (-3.0, 2.0),
    "recent_nights": (-3.0, 2.0),
    "sleep_quality": (-3.0, 3.0),
    "sleep_timing": (-3.0, 1.0),
    "sleep_hr": (-3.0, 3.0),
    "hrv": (-3.0, 3.0),
    "resting_hr": (-3.0, 3.0),
    "load": (-3.0, 0.0),
    "movement": (-3.0, 3.0),
    "daylight": (-3.0, 3.0),
}
DOMAINS: dict[str, tuple[str, ...]] = {
    "sleep amount": ("sleep", "recent_nights"),
    "sleep quality and timing": ("sleep_quality", "sleep_timing"),
    "recovery": ("sleep_hr", "hrv", "resting_hr"),
    "load": ("load",),
    "movement and light": ("movement", "daylight"),
}
POINTS_PER_WEIGHTED_Z = 1 / 6
# The two-term score is this form with weights (50, 50) and its cue at a weighted sum of
# -100; a sum of independent terms spreads as the root-sum-square of its weights.
LAMP_WEIGHTS = (50, 50)
SPREAD_RATIO = math.hypot(*WEIGHTS.values()) / math.hypot(*LAMP_WEIGHTS)
CUE_WEIGHTED_SUM = -100.0 * SPREAD_RATIO
FULL_RED = 50 + POINTS_PER_WEIGHTED_Z * CUE_WEIGHTED_SUM
FULL_GREEN = 50 - POINTS_PER_WEIGHTED_Z * CUE_WEIGHTED_SUM
MIN_CUE_WEIGHT = 60
RECENT_NIGHTS = 3
MIN_RECENT_NIGHTS = 2
RESTORATIVE_FLOOR = 0.03
AWAKE_FLOOR_MIN = 5.0
ONSET_FLOOR_MIN = 30.0
ONSET_Z_MAX = 1.0
LN_HRV_FLOOR = 0.10
RHR_FLOOR_BPM = 2.0
RHR_MIN_BPM, RHR_MAX_BPM = 30.0, 120.0
LOAD_BALANCE_EVEN = 1.0
LOAD_BALANCE_PER_Z = 0.25
LOAD_BALANCE_MAX = 10.0
# Under this chronic load the balance is a ratio over almost nothing: one workout after
# a quiet month reads as overreach. A third of the 60-point bar a day is about a week on
# target.
LOAD_MIN_CHRONIC = 20.0
LOAD_MAX_CHRONIC = 1000.0
STEPS_FLOOR = 1500.0
STEPS_MAX = 100_000.0
DAYLIGHT_FLOOR_MIN = 10.0
DAYLIGHT_MAX_MIN = 1440.0
HRV_MIN_MS, HRV_MAX_MS = 1.0, 300.0
ONSET_MIN, ONSET_MAX = -720.0, 720.0

FULL = "bandwidth_full"
FULL_LOW = "bandwidth_full_low"
FULL_LAMP = "bandwidth_full_lamp"
FULL_TRACE = "bandwidth_full_trace"
FULL_KEYS = frozenset({FULL, FULL_LOW, FULL_LAMP, FULL_TRACE})


@dataclass
class FullInputs:
    sleep_hours: dict[date, float] = field(default_factory=dict)
    nights: dict[date, NightSignal] = field(default_factory=dict)
    stages: dict[date, NightStages] = field(default_factory=dict)
    onsets: dict[date, datetime] = field(default_factory=dict)
    hrv_ms: dict[date, float] = field(default_factory=dict)
    resting_hr: dict[date, float] = field(default_factory=dict)
    load_balance: dict[date, float | None] = field(default_factory=dict)
    load_chronic: dict[date, float | None] = field(default_factory=dict)
    steps: dict[date, int] = field(default_factory=dict)
    daylight_min: dict[date, float] = field(default_factory=dict)
    tz: str = "America/New_York"


def _finite(value: float | None) -> bool:
    return value is not None and not isinstance(value, bool) and math.isfinite(value)


def _robust(
    values: dict[date, float], day: date, floor: float, sign: float, upper: float = 3.0
) -> Term | None:
    """Robust z of `values[day]` against the 28 days before it, signed so higher is better."""
    x = values.get(day)
    if not _finite(x):
        return None
    usable = {d: v for d, v in values.items() if _finite(v)}
    history = _history(usable, day)
    base = _baseline(history, floor)
    if base is None:
        return None
    z = min(_clamp(sign * (x - base[0]) / base[1]), upper)
    return Term(x, z, base[0], base[1], len(history))


def recent_nights_term(day: date, inputs: FullInputs) -> Term | None:
    """The mean of the sleep bands over the three nights before last night: the days
    leading in, with last night itself left to its own term."""
    scores = []
    for back in range(1, RECENT_NIGHTS + 1):
        night = day - timedelta(days=back)
        hours = inputs.sleep_hours.get(night)
        if _valid_sleep(hours):
            z = sleep_band_z(hours)
            scores.append(max(z, 0.0) if _censored(night, inputs.nights) else z)
    if len(scores) < MIN_RECENT_NIGHTS:
        return None
    return Term(float(len(scores)), sum(scores) / len(scores))


def _restorative(stages: NightStages) -> float | None:
    if stages.asleep_s <= 0:
        return None
    share = (stages.deep_s + stages.rem_s) / stages.asleep_s
    return share if 0 < share < 1 else None


def sleep_quality_term(day: date, inputs: FullInputs) -> Term | None:
    """Half the share of the night in deep and REM sleep, half the minutes awake inside
    it, each against his own four weeks. A censored night has no whole set of stages."""
    usable = {d: s for d, s in inputs.stages.items() if not _censored(d, inputs.nights)}
    shares = {d: r for d, s in usable.items() if (r := _restorative(s)) is not None}
    awake = {d: s.awake_s / 60 for d, s in usable.items() if s.awake_s >= 0}
    share_z = _robust(shares, day, RESTORATIVE_FLOOR, +1.0)
    awake_z = _robust(awake, day, AWAKE_FLOOR_MIN, -1.0)
    if share_z is None or awake_z is None:
        return None
    return Term(share_z.x, (share_z.z + awake_z.z) / 2, share_z.reference, share_z.scale)


def onset_minutes(onset: datetime, wake_day: date, tz: str) -> float:
    """Minutes from the wake day's local midnight to falling asleep: -30 is 23:30."""
    midnight = datetime.combine(wake_day, datetime.min.time(), ZoneInfo(tz))
    return (onset - midnight).total_seconds() / 60


def sleep_timing_term(day: date, inputs: FullInputs) -> Term | None:
    """How much later than his usual he went to sleep. Earlier than usual earns at most
    one scale unit; later costs up to three."""
    minutes = {d: onset_minutes(o, d, inputs.tz) for d, o in inputs.onsets.items()}
    plausible = {d: m for d, m in minutes.items() if ONSET_MIN <= m <= ONSET_MAX}
    return _robust(plausible, day, ONSET_FLOOR_MIN, -1.0, ONSET_Z_MAX)


def hrv_term(day: date, inputs: FullInputs) -> Term | None:
    """ln of yesterday's HRV: the last whole day, since today's is still filling in."""
    logs = {
        d: math.log(v)
        for d, v in inputs.hrv_ms.items()
        if _finite(v) and HRV_MIN_MS <= v <= HRV_MAX_MS
    }
    return _robust(logs, day - timedelta(days=1), LN_HRV_FLOOR, +1.0)


def resting_hr_term(day: date, inputs: FullInputs) -> Term | None:
    valid = {
        d: v for d, v in inputs.resting_hr.items() if _finite(v) and RHR_MIN_BPM <= v <= RHR_MAX_BPM
    }
    return _robust(valid, day - timedelta(days=1), RHR_FLOOR_BPM, -1.0)


def load_term(day: date, inputs: FullInputs) -> Term | None:
    """Overreach only: yesterday's acute load over chronic, 0 at even or under, one unit
    down for every quarter over. Training less than usual is not a cost here."""
    yesterday = day - timedelta(days=1)
    balance, chronic = inputs.load_balance.get(yesterday), inputs.load_chronic.get(yesterday)
    if not _finite(balance) or not 0 <= balance <= LOAD_BALANCE_MAX:
        return None
    if not _finite(chronic) or not LOAD_MIN_CHRONIC <= chronic <= LOAD_MAX_CHRONIC:
        return None
    over = max(0.0, balance - LOAD_BALANCE_EVEN) / LOAD_BALANCE_PER_Z
    return Term(balance, -min(3.0, over), LOAD_BALANCE_EVEN, LOAD_BALANCE_PER_Z)


def movement_term(day: date, inputs: FullInputs) -> Term | None:
    steps = {d: float(v) for d, v in inputs.steps.items() if _finite(v) and 0 <= v <= STEPS_MAX}
    return _robust(steps, day - timedelta(days=1), STEPS_FLOOR, +1.0)


def daylight_term(day: date, inputs: FullInputs) -> Term | None:
    light = {
        d: v for d, v in inputs.daylight_min.items() if _finite(v) and 0 <= v <= DAYLIGHT_MAX_MIN
    }
    return _robust(light, day - timedelta(days=1), DAYLIGHT_FLOOR_MIN, +1.0)


def terms(day: date, inputs: FullInputs) -> dict[str, Term | None]:
    return {
        "sleep": sleep_term(day, inputs.sleep_hours, inputs.nights),
        "recent_nights": recent_nights_term(day, inputs),
        "sleep_quality": sleep_quality_term(day, inputs),
        "sleep_timing": sleep_timing_term(day, inputs),
        "sleep_hr": sleep_hr_term(day, inputs.nights),
        "hrv": hrv_term(day, inputs),
        "resting_hr": resting_hr_term(day, inputs),
        "load": load_term(day, inputs),
        "movement": movement_term(day, inputs),
        "daylight": daylight_term(day, inputs),
    }


def _trace(name: str, term: Term | None) -> dict[str, object] | None:
    if term is None:
        return None
    return {
        "x": round_half_up(term.x, 2),
        "reference": None if term.reference is None else round_half_up(term.reference, 2),
        "scale": None if term.scale is None else round_half_up(term.scale, 2),
        "z": round_half_up(term.z, 4) + 0.0,
        "weight": WEIGHTS[name],
        "points": round_half_up(POINTS_PER_WEIGHTED_Z * WEIGHTS[name] * term.z, 2) + 0.0,
    }


def full_json(day: date, inputs: FullInputs, *, awaiting_night: bool = False) -> dict[str, object]:
    """The day's fuller score, its cue, what the lamp shows and the trace. It needs last
    night: with neither the sleep term nor the sleeping heart rate there is no reading,
    whatever else is on record, because a score of yesterday's steps alone would not be
    about today. The lamp shows the score only with MIN_CUE_WEIGHT of the hundred present,
    so a thin day is gold, never red or green; and with `awaiting_night` (today, before
    noon, no night yet) it carries yesterday's cue and lamp value."""
    found = terms(day, inputs)
    present = sum(WEIGHTS[name] for name, term in found.items() if term is not None)
    trace: dict[str, object] = {name: _trace(name, term) for name, term in found.items()}
    trace["weight_present"] = present
    trace["carried_from"] = None
    if found["sleep"] is None and found["sleep_hr"] is None:
        low, lamp = False, None
        if awaiting_night and day not in inputs.sleep_hours and day not in inputs.nights:
            before = day - timedelta(days=1)
            prior = full_json(before, inputs)
            low, lamp = prior[FULL_LOW] is True, prior[FULL_LAMP]
            trace["carried_from"] = before.isoformat() if lamp is not None else None
        return {FULL: None, FULL_LOW: low, FULL_LAMP: lamp, FULL_TRACE: trace}
    weighted = sum(WEIGHTS[name] * term.z for name, term in found.items() if term is not None)
    score = round_half_up(50 + POINTS_PER_WEIGHTED_Z * weighted, 2)
    shown = present >= MIN_CUE_WEIGHT
    return {
        FULL: score,
        FULL_LOW: shown and round(weighted, 9) <= round(CUE_WEIGHTED_SUM, 9),
        FULL_LAMP: score if shown else None,
        FULL_TRACE: trace,
    }


assert sum(WEIGHTS.values()) == 100
assert set(TERM_RANGE) == set(WEIGHTS)
assert sorted(name for names in DOMAINS.values() for name in names) == sorted(WEIGHTS)
assert MIN_HISTORY >= 2
