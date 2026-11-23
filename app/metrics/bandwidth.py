"""Bandwidth: a night recovery score on 0 to 100 (notes.txt § Goal model, "Bandwidth").

Two terms for last night. Sleep is scored on Max's own bands (2026-11-20: "it's not too
much about deprivation that is the issue for me, but rather oversleeping"): +2 from 7 to
8.5 hours, 0 from 6 to 6.5 and from 9 to 9.5, -2 at 5.5 and at 10, straight lines between,
down to -3 at 4.5 and at 11 and flat beyond. Sleeping heart rate is a robust z against his
prior four weeks (median, 1.4826 x MAD with a floor), clamped to [-3, 3]. An absent term
contributes 0 and the weights are never renormalised.

    B = 50 + (25 / 3) * (z_sleep + z_sleep_hr)

so B is in [0, 275/3] by construction, with no terminal clamp: 50 is a middling night, and
the cue line 100/3 is a z sum of -2, decided on the z sum and not on the rounded score.
A night whose watch came off before waking (no heart-rate row in the 90 minutes after the
session ends) saw only part of the night, so neither of its terms can go below 0, and it
stays out of the heart-rate history. Until today's night arrives, and no later than noon,
today carries yesterday's cue and score, so neither switches off at midnight
(`bandwidth_lamp`: today's, or the carried one).

Since 2026-11-20 the Books lamp is coloured by the fuller score (bandwidth_full.py), of
which these two terms are the anchors; this score stays stored beside it for comparison
(`tools.bandwidth`) and nothing displays it.

Unchecked against how Max felt: it says what kind of night last night was, no more.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import pairwise
from statistics import median

from app.metrics.keys import round_half_up

WINDOW_DAYS = 28
MIN_HISTORY = 14
Z_LIMIT = 3.0
MAD_TO_SD = 1.4826
# (hours asleep, z): Max's bands, linear between the knots and flat outside them.
SLEEP_KNOTS: tuple[tuple[float, float], ...] = (
    (4.5, -3.0),
    (5.5, -2.0),
    (6.0, 0.0),
    (6.5, 0.0),
    (7.0, 2.0),
    (8.5, 2.0),
    (9.0, 0.0),
    (9.5, 0.0),
    (10.0, -2.0),
    (11.0, -3.0),
)
SLEEP_MAX_H = 16.0
HR_FLOOR_BPM = 2.0
HR_MIN_BPM = 30.0
HR_MAX_BPM = 120.0
MIN_NIGHT_HR_ROWS = 30
CENSOR_AFTER = timedelta(minutes=90)
POINTS_PER_Z = 25 / 3
CUE_Z_SUM = -2.0

BANDWIDTH = "bandwidth"
BANDWIDTH_LOW = "bandwidth_low"
BANDWIDTH_LAMP = "bandwidth_lamp"
BANDWIDTH_TRACE = "bandwidth_trace"
BANDWIDTH_KEYS = frozenset({BANDWIDTH, BANDWIDTH_LOW, BANDWIDTH_LAMP, BANDWIDTH_TRACE})


@dataclass(frozen=True)
class NightSignal:
    sleep_hr: float | None
    hr_rows: int
    censored: bool


@dataclass(frozen=True)
class Term:
    x: float
    z: float
    reference: float | None = None
    scale: float | None = None
    history: int | None = None

    @property
    def points(self) -> float:
        return POINTS_PER_Z * self.z


def night_signals(
    spans: dict[date, tuple[datetime, datetime]], hr: list[tuple[datetime, float]]
) -> dict[date, NightSignal]:
    """Per wake day: the median heart rate inside the night's span (null under
    MIN_NIGHT_HR_ROWS rows or outside a plausible range) and whether the night is censored."""
    rows = sorted({when: bpm for when, bpm in hr if math.isfinite(bpm)}.items())
    times = [when for when, _ in rows]
    out: dict[date, NightSignal] = {}
    for day, (start, end) in spans.items():
        lo = bisect.bisect_left(times, start)
        hi = bisect.bisect_right(times, end)
        inside = [bpm for _, bpm in rows[lo:hi]]
        sleep_hr = median(inside) if len(inside) >= MIN_NIGHT_HR_ROWS else None
        if not _valid_rate(sleep_hr):
            sleep_hr = None
        worn_after = hi < len(times) and times[hi] <= end + CENSOR_AFTER
        out[day] = NightSignal(sleep_hr, len(inside), not worn_after)
    return out


def _valid_sleep(hours: float | None) -> bool:
    return hours is not None and math.isfinite(hours) and 0 < hours <= SLEEP_MAX_H


def _valid_rate(bpm: float | None) -> bool:
    return bpm is not None and math.isfinite(bpm) and HR_MIN_BPM <= bpm <= HR_MAX_BPM


def _censored(day: date, nights: dict[date, NightSignal]) -> bool:
    return day not in nights or nights[day].censored


def _history(values: dict[date, float], day: date) -> list[float]:
    return [
        values[d] for k in range(1, WINDOW_DAYS + 1) if (d := day - timedelta(days=k)) in values
    ]


def _baseline(history: list[float], floor: float) -> tuple[float, float] | None:
    if len(history) < MIN_HISTORY:
        return None
    centre = median(history)
    spread = MAD_TO_SD * median(abs(v - centre) for v in history)
    return centre, max(spread, floor)


def _clamp(z: float) -> float:
    return max(-Z_LIMIT, min(Z_LIMIT, z))


def sleep_band_z(hours: float) -> float:
    """Max's bands as a number: the knots joined by straight lines, flat past either end."""
    if hours <= SLEEP_KNOTS[0][0]:
        return SLEEP_KNOTS[0][1]
    for (lo, below), (hi, above) in pairwise(SLEEP_KNOTS):
        if hours <= hi:
            return below + (above - below) * (hours - lo) / (hi - lo)
    return SLEEP_KNOTS[-1][1]


def sleep_term(
    day: date, sleep_hours: dict[date, float], nights: dict[date, NightSignal]
) -> Term | None:
    x = sleep_hours.get(day)
    if not _valid_sleep(x):
        return None
    z = sleep_band_z(x)
    return Term(x, max(z, 0.0) if _censored(day, nights) else z)


def sleep_hr_term(day: date, nights: dict[date, NightSignal]) -> Term | None:
    night = nights.get(day)
    if night is None or not _valid_rate(night.sleep_hr):
        return None
    x = night.sleep_hr
    usable = {
        d: n.sleep_hr for d, n in nights.items() if _valid_rate(n.sleep_hr) and not n.censored
    }
    history = _history(usable, day)
    base = _baseline(history, HR_FLOOR_BPM)
    if base is None:
        return None
    z = _clamp(-(x - base[0]) / base[1])
    return Term(x, max(z, 0.0) if night.censored else z, base[0], base[1], len(history))


def _term_json(term: Term | None) -> dict[str, object] | None:
    if term is None:
        return None
    return {
        "x": round_half_up(term.x, 2),
        "reference": None if term.reference is None else round_half_up(term.reference, 2),
        "scale": None if term.scale is None else round_half_up(term.scale, 2),
        "z": round_half_up(term.z, 4) + 0.0,
        "points": round_half_up(term.points, 2) + 0.0,
        "history": term.history,
    }


def bandwidth_json(
    day: date,
    sleep_hours: dict[date, float],
    nights: dict[date, NightSignal],
    *,
    awaiting_night: bool = False,
) -> dict[str, object]:
    """The day's score, cue and trace, keyed as the daily row stores them. With no term
    present there is no reading: the score is null, never 50, and the cue is off, unless
    `awaiting_night` (today, before noon, no night yet), when yesterday's cue and score
    are carried for the lamp."""
    sleep = sleep_term(day, sleep_hours, nights)
    sleep_hr = sleep_hr_term(day, nights)
    night = nights.get(day)
    trace = {
        "sleep": _term_json(sleep),
        "sleep_hr": _term_json(sleep_hr),
        "censored": night.censored if night is not None else None,
        "hr_rows": night.hr_rows if night is not None else None,
        "carried_from": None,
    }
    if sleep is None and sleep_hr is None:
        low, lamp = False, None
        if awaiting_night and day not in sleep_hours and night is None:
            before = day - timedelta(days=1)
            prior = bandwidth_json(before, sleep_hours, nights)
            low, lamp = prior[BANDWIDTH_LOW] is True, prior[BANDWIDTH]
            trace["carried_from"] = before.isoformat() if lamp is not None else None
        return {
            BANDWIDTH: None,
            BANDWIDTH_LOW: low,
            BANDWIDTH_LAMP: lamp,
            BANDWIDTH_TRACE: trace,
        }
    z_sum = sum(term.z for term in (sleep, sleep_hr) if term is not None)
    score = round_half_up(50 + POINTS_PER_Z * z_sum, 2)
    return {
        BANDWIDTH: score,
        BANDWIDTH_LOW: round(z_sum, 9) <= CUE_Z_SUM,
        BANDWIDTH_LAMP: score,
        BANDWIDTH_TRACE: trace,
    }
