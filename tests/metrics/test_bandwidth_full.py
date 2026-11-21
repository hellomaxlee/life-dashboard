"""The fuller bandwidth score (app/metrics/bandwidth_full.py), which the Books lamp
reads: each term by hand, the range and the cue line derived from the weights, and the
hostile inputs that once stopped the whole engine. The ordinary day used throughout has
every baseline term at 0 and both sleep terms in the green band:
30 x 2 + 14 x 2 = 88, B_full = 50 + 88 / 6 = 64.67."""

from __future__ import annotations

import json
import math
import random
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from app.metrics.bandwidth import NightSignal
from app.metrics.bandwidth_full import (
    CUE_WEIGHTED_SUM,
    DOMAINS,
    FULL_GREEN,
    FULL_KEYS,
    FULL_RED,
    MIN_CUE_WEIGHT,
    SESSION_KNOTS,
    TERM_RANGE,
    WEIGHTS,
    FullInputs,
    full_json,
    onset_minutes,
    session_z,
    terms,
)
from app.metrics.engine import recompute
from app.metrics.keys import DAILY_KEYS
from app.metrics.sleep import NightStages, SleepFragment, night_stages
from app.render.view_db import view_from_db
from app.summary.payload import build_payload
from tests.metrics.test_bandwidth import NOW, daily, store_night, stored_history
from tools.bandwidth import FULL_STOPS, TWO_STOPS, band, day_line, trace_lines

DAY = date(2026, 10, 15)
YESTERDAY = DAY - timedelta(days=1)
TZ = "America/New_York"
ORDINARY = 64.67


def onset(day: date, minutes_after_midnight: float = -30.0) -> datetime:
    """Asleep at 23:30 the evening before by default; October is UTC-4."""
    local_midnight_utc = datetime(day.year, day.month, day.day, 4, tzinfo=UTC)
    return local_midnight_utc + timedelta(minutes=minutes_after_midnight)


def ordinary(nights: int = 30) -> FullInputs:
    days = [DAY - timedelta(days=k) for k in range(nights + 1)]
    return FullInputs(
        sleep_hours={d: 7.5 for d in days},
        nights={d: NightSignal(55.0, 40, False) for d in days},
        stages={d: NightStages(27000, 4050, 6750, 600) for d in days},
        onsets={d: onset(d) for d in days},
        hrv_ms={d: 45.0 for d in days},
        resting_hr={d: 60.0 for d in days},
        load_balance={d: 1.0 for d in days},
        load_chronic={d: 30.0 for d in days},
        load_trimp={d: 0.0 for d in days},
        steps={d: 8000 for d in days},
        daylight_min={d: 40.0 for d in days},
        tz=TZ,
    )


def score(inputs: FullInputs) -> float | None:
    return full_json(DAY, inputs)["bandwidth_full"]


def test_an_ordinary_day_is_64_67_with_every_term_present():
    out = full_json(DAY, ordinary())
    trace = out["bandwidth_full_trace"]
    assert out["bandwidth_full"] == ORDINARY
    assert out["bandwidth_full_low"] is False
    assert trace["weight_present"] == 100
    assert {name: trace[name]["points"] for name in WEIGHTS} == {
        "sleep": 10.0,
        "recent_nights": 4.67,
        "sleep_quality": 0.0,
        "sleep_timing": 0.0,
        "sleep_hr": 0.0,
        "hrv": 0.0,
        "resting_hr": 0.0,
        "load": 0.0,
        "movement": 0.0,
        "daylight": 0.0,
    }
    readded = 50 + sum(trace[name]["points"] for name in WEIGHTS)
    assert abs(readded - out["bandwidth_full"]) <= 0.01


def changed(**fields) -> FullInputs:
    base = ordinary()
    return replace(base, **{name: {**getattr(base, name), **new} for name, new in fields.items()})


CASES = {
    "a 5.5 h night: 30 x -2 + 28 = -32": (changed(sleep_hours={DAY: 5.5}), 44.67),
    "sleeping heart rate 6 bpm up: z -3, 88 - 72": (
        changed(nights={DAY: NightSignal(61.0, 40, False)}),
        52.67,
    ),
    "three short nights leading in: 14 x -2": (
        changed(sleep_hours={DAY - timedelta(days=k): 5.5 for k in (1, 2, 3)}),
        55.33,
    ),
    "deep and REM at 34% of the night: half of z -2": (
        changed(stages={DAY: NightStages(27000, 2430, 6750, 600)}),
        63.83,
    ),
    "20 minutes awake against 10: half of z -2": (
        changed(stages={DAY: NightStages(27000, 4050, 6750, 1200)}),
        63.83,
    ),
    "asleep an hour late: z -2 on a 30 min floor": (changed(onsets={DAY: onset(DAY, 30)}), 60.67),
    "asleep two hours early: capped at +1": (changed(onsets={DAY: onset(DAY, -150)}), 66.67),
    "yesterday's HRV down by 0.2 in ln: z -2": (
        changed(hrv_ms={YESTERDAY: 45.0 * math.exp(-0.2)}),
        63.33,
    ),
    "yesterday's resting heart rate 4 bpm up: z -2": (changed(resting_hr={YESTERDAY: 64.0}), 63.67),
    "yesterday's load balance 1.5: z -2 on weight 4": (
        changed(load_balance={YESTERDAY: 1.5}),
        63.33,
    ),
    "a moderate session yesterday: z +1": (changed(load_trimp={YESTERDAY: 80.0}), 65.33),
    "a moderate session two days ago: half of +1": (
        changed(load_trimp={YESTERDAY - timedelta(days=1): 80.0}),
        65.0,
    ),
    "moderate sessions on both days: +1.5, the most": (
        changed(load_trimp={YESTERDAY: 60.0, YESTERDAY - timedelta(days=1): 100.0}),
        65.67,
    ),
    "a hard session yesterday, load 220: z -2": (changed(load_trimp={YESTERDAY: 220.0}), 63.33),
    "a hard session two days ago, load 220: half of -2": (
        changed(load_trimp={YESTERDAY - timedelta(days=1): 220.0}),
        64.0,
    ),
    "a session three days ago is spent": (
        changed(load_trimp={YESTERDAY - timedelta(days=2): 220.0}),
        ORDINARY,
    ),
    "a moderate session in an overreaching week: +1 - 2": (
        changed(load_trimp={YESTERDAY: 80.0}, load_balance={YESTERDAY: 1.5}),
        64.0,
    ),
    "yesterday 3000 steps short: z -2 on weight 3": (changed(steps={YESTERDAY: 5000}), 63.67),
    "yesterday 20 minutes less daylight: z -2 on weight 1": (
        changed(daylight_min={YESTERDAY: 20.0}),
        64.33,
    ),
}


@pytest.mark.parametrize("case", CASES)
def test_each_term_moves_the_score_by_its_weight(case):
    inputs, expected = CASES[case]
    assert score(inputs) == expected


def test_the_session_knots_are_his_priors_and_the_term_stops_at_minus_three():
    assert SESSION_KNOTS == ((0.0, 0.0), (60.0, 1.0), (100.0, 1.0), (140.0, 0.0), (260.0, -3.0))
    assert [session_z(x) for x in (0, 30, 60, 100, 120, 140, 180, 260, 900)] == [
        0.0,
        0.5,
        1.0,
        1.0,
        0.5,
        0.0,
        -1.0,
        -3.0,
        -3.0,
    ]
    both = changed(load_trimp={YESTERDAY: 300.0, YESTERDAY - timedelta(days=1): 300.0})
    assert terms(DAY, both)["load"].z == -3.0, "-3 - 1.5 stops at the term's floor"
    assert TERM_RANGE["load"] == (-3.0, 1.5)


def test_training_under_his_usual_is_no_cost_and_a_thin_chronic_load_has_no_week_part():
    assert score(changed(load_balance={YESTERDAY: 0.4})) == ORDINARY
    thin = full_json(DAY, changed(load_balance={YESTERDAY: 3.0}, load_chronic={YESTERDAY: 19.9}))
    assert thin["bandwidth_full"] == ORDINARY
    assert thin["bandwidth_full_trace"]["load"]["z"] == 0.0, "one workout after a quiet month"
    assert thin["bandwidth_full_trace"]["weight_present"] == 100
    assert score(changed(load_balance={YESTERDAY: 3.0}, load_chronic={YESTERDAY: 20.0})) == 62.67
    unknown = full_json(DAY, replace(ordinary(), load_trimp={}))
    assert unknown["bandwidth_full_trace"]["load"] is None, "no load on record is no term"
    assert unknown["bandwidth_full_trace"]["weight_present"] == 96
    one_day = replace(ordinary(), load_trimp={YESTERDAY: 80.0})
    assert terms(DAY, one_day)["load"].z == 1.0, "the day before missing counts as none"


def test_recent_nights_needs_two_of_the_three_and_leaves_last_night_out():
    one = ordinary()
    for k in (2, 3):
        del one.sleep_hours[DAY - timedelta(days=k)]
    out = full_json(DAY, one)
    assert out["bandwidth_full_trace"]["recent_nights"] is None
    assert (out["bandwidth_full"], out["bandwidth_full_trace"]["weight_present"]) == (60.0, 86)
    assert score(changed(sleep_hours={DAY: 5.5})) == 44.67, "last night is not a recent night"
    cut = changed(sleep_hours={YESTERDAY: 4.0}, nights={YESTERDAY: NightSignal(55.0, 40, True)})
    assert full_json(DAY, cut)["bandwidth_full_trace"]["recent_nights"]["z"] == pytest.approx(
        4 / 3, abs=1e-4
    ), "a censored night in the three counts as 0, never under"


def test_a_censored_night_has_no_quality_term_and_lowers_nothing():
    cut = changed(
        sleep_hours={DAY: 4.0},
        nights={DAY: NightSignal(61.0, 40, True)},
        stages={DAY: NightStages(14400, 100, 100, 3000)},
    )
    out = full_json(DAY, cut)
    assert out["bandwidth_full_trace"]["sleep_quality"] is None
    assert out["bandwidth_full_trace"]["sleep"]["z"] == 0.0
    assert out["bandwidth_full_trace"]["sleep_hr"]["z"] == 0.0
    assert out["bandwidth_full"] == 54.67


def test_the_cue_line_and_the_colour_stops_come_from_the_weights():
    ratio = math.sqrt(sum(w * w for w in WEIGHTS.values())) / math.sqrt(50**2 + 50**2)
    assert pytest.approx(-100 * ratio) == CUE_WEIGHTED_SUM
    assert pytest.approx(50 - 100 * ratio / 6) == FULL_RED
    assert pytest.approx(50 + 100 * ratio / 6) == FULL_GREEN
    assert sum(WEIGHTS.values()) == 100
    assert (round(CUE_WEIGHTED_SUM, 2), round(FULL_RED, 2), round(FULL_GREEN, 2)) == (
        -61.51,
        39.75,
        60.25,
    )


def test_each_anchor_term_can_reach_the_cue_alone():
    """Last night's sleep and sleeping heart rate are what a reading rests on: either at its
    worst, with every other term at 0, must cross the line."""
    for anchor in ("sleep", "sleep_hr"):
        assert WEIGHTS[anchor] * TERM_RANGE[anchor][0] <= CUE_WEIGHTED_SUM, anchor


def test_the_cue_is_on_at_the_line_and_needs_sixty_of_the_hundred_present():
    """4.5 h is -3: 30 x -3 + 28 = -62, under -61.51. 4.6 h is -2.9: -59, over it."""
    on = full_json(DAY, changed(sleep_hours={DAY: 4.5}))
    assert (on["bandwidth_full"], on["bandwidth_full_low"]) == (39.67, True)
    off = full_json(DAY, changed(sleep_hours={DAY: 4.6}))
    assert (off["bandwidth_full"], off["bandwidth_full_low"]) == (40.17, False)
    alone = FullInputs(sleep_hours={DAY: 4.5}, nights={DAY: NightSignal(None, 0, False)}, tz=TZ)
    thin = full_json(DAY, alone)
    assert thin["bandwidth_full_trace"]["weight_present"] == 30 < MIN_CUE_WEIGHT
    assert (thin["bandwidth_full"], thin["bandwidth_full_low"]) == (35.0, False)


def test_without_last_night_there_is_no_reading_whatever_else_is_on_record():
    base = ordinary()
    del base.sleep_hours[DAY], base.nights[DAY]
    out = full_json(DAY, base)
    assert (out["bandwidth_full"], out["bandwidth_full_low"]) == (None, False)
    assert out["bandwidth_full_trace"]["movement"] is not None
    hr_only = ordinary()
    del hr_only.sleep_hours[DAY]
    assert full_json(DAY, hr_only)["bandwidth_full"] is not None


def test_the_attainable_range_is_what_the_term_ranges_say():
    low = 50 + sum(WEIGHTS[name] * TERM_RANGE[name][0] for name in WEIGHTS) / 6
    high = 50 + sum(WEIGHTS[name] * TERM_RANGE[name][1] for name in WEIGHTS) / 6
    assert (low, round(high, 2)) == (0.0, 87.67)
    worst = changed(
        sleep_hours={DAY - timedelta(days=k): 4.0 for k in range(4)},
        nights={DAY: NightSignal(110.0, 40, False)},
        stages={DAY: NightStages(27000, 100, 100, 9000)},
        onsets={DAY: onset(DAY, 300)},
        hrv_ms={YESTERDAY: 5.0},
        resting_hr={YESTERDAY: 100.0},
        load_balance={YESTERDAY: 5.0},
        load_trimp={YESTERDAY: 400.0},
        steps={YESTERDAY: 0},
        daylight_min={YESTERDAY: 0.0},
    )
    assert score(worst) == 0.0
    best = changed(
        nights={DAY: NightSignal(31.0, 40, False)},
        stages={DAY: NightStages(27000, 9000, 9000, 0)},
        onsets={DAY: onset(DAY, -300)},
        hrv_ms={YESTERDAY: 250.0},
        resting_hr={YESTERDAY: 31.0},
        load_trimp={YESTERDAY: 80.0, YESTERDAY - timedelta(days=1): 80.0},
        steps={YESTERDAY: 60000},
        daylight_min={YESTERDAY: 600.0},
    )
    assert score(best) == 87.25, "awake cannot go under 0 min, so quality stops at +2.5 here"


TERM_OF = {
    "hrv_ms": "hrv",
    "resting_hr": "resting_hr",
    "load_trimp": "load",
    "steps": "movement",
    "daylight_min": "daylight",
}
HOSTILE = (float("nan"), float("inf"), float("-inf"), -5.0, 1e26, 1e308, 10**30)


@pytest.mark.parametrize(
    "field",
    ["hrv_ms", "resting_hr", "load_balance", "load_chronic", "load_trimp", "steps", "daylight_min"],
)
@pytest.mark.parametrize("value", HOSTILE)
def test_an_impossible_value_is_an_absent_term_and_never_stops_the_engine(field, value):
    """`daylight_min = 1e26` once raised in the rounding and took the lamp's score with it."""
    out = full_json(DAY, changed(**{field: {YESTERDAY: value}}))
    assert json.dumps(out, allow_nan=False)
    assert 0.0 <= out["bandwidth_full"] <= 87.67
    if field in TERM_OF:
        assert out["bandwidth_full_trace"][TERM_OF[field]] is None, "not scored as a real value"
    else:
        assert out["bandwidth_full_trace"]["load"]["z"] == 0.0, "no week part, the sessions stand"
    wild_history = changed(**{field: {DAY - timedelta(days=5): value}})
    assert json.dumps(full_json(DAY, wild_history), allow_nan=False)


def test_every_score_is_in_range_and_every_term_in_its_own_whatever_the_inputs():
    rng = random.Random(20261121)

    def pick(low: float, high: float) -> float:
        return rng.choice(HOSTILE) if rng.random() < 0.06 else rng.uniform(low, high)

    readings = 0
    for _ in range(1500):
        inputs = FullInputs(tz=TZ)
        for k in range(0, 36):
            day = DAY - timedelta(days=k)
            if rng.random() < 0.85:
                inputs.sleep_hours[day] = pick(2, 13)
            if rng.random() < 0.85:
                rate = rng.choice((None, pick(40, 90)))
                inputs.nights[day] = NightSignal(rate, 40, rng.random() < 0.1)
            if rng.random() < 0.8:
                asleep = rng.randrange(0, 40000)
                inputs.stages[day] = NightStages(
                    asleep, rng.randrange(0, 20000), rng.randrange(0, 20000), rng.randrange(0, 9000)
                )
            if rng.random() < 0.85:
                inputs.onsets[day] = onset(day, rng.uniform(-2000, 2000))
            inputs.hrv_ms[day] = pick(10, 120)
            inputs.resting_hr[day] = pick(45, 80)
            inputs.load_balance[day] = rng.choice((None, pick(0, 3)))
            inputs.load_chronic[day] = rng.choice((None, pick(0, 80)))
            if rng.random() < 0.9:
                inputs.load_trimp[day] = rng.choice((0.0, pick(0, 400)))
            inputs.steps[day] = pick(0, 25000)
            inputs.daylight_min[day] = pick(0, 200)
        out = full_json(DAY, inputs)
        assert json.dumps(out, allow_nan=False)
        for name, term in terms(DAY, inputs).items():
            if term is not None:
                low, high = TERM_RANGE[name]
                assert low <= term.z <= high, (name, term)
        value = out["bandwidth_full"]
        if value is None:
            assert out["bandwidth_full_low"] is False
            continue
        readings += 1
        assert 0.0 <= value <= 87.67
        if out["bandwidth_full_low"]:
            assert value <= round(FULL_RED, 2)
            assert out["bandwidth_full_trace"]["weight_present"] >= MIN_CUE_WEIGHT
    assert readings > 1000


def test_onset_is_minutes_from_the_wake_days_local_midnight():
    assert onset_minutes(datetime(2026, 10, 15, 3, 30, tzinfo=UTC), DAY, TZ) == -30.0
    assert onset_minutes(datetime(2026, 10, 15, 5, 27, tzinfo=UTC), DAY, TZ) == 87.0
    winter = date(2026, 11, 20)
    assert onset_minutes(datetime(2026, 11, 20, 6, 27, tzinfo=UTC), winter, TZ) == 87.0


def test_night_stages_follow_the_source_with_the_most_sleep_and_need_every_stage():
    def fragment(source: str, asleep: int, deep: int | None, start: str = "03:00") -> SleepFragment:
        return SleepFragment(
            DAY, f"2026-10-15T{start}:00Z", asleep, source, "2026-10-15T11:00:00Z", deep, 600, 120
        )

    stages = night_stages(
        [
            fragment("Watch", 10000, 900),
            fragment("Watch", 9000, 800, "07:00"),
            fragment("Phone", 12000, 5000),
            SleepFragment(DAY, "2026-10-15T18:30:00Z", 3600, "Watch", None, 500, 500, 0),
        ],
        TZ,
    )
    assert stages == {DAY: NightStages(19000, 1700, 1200, 240)}
    assert night_stages([fragment("Watch", 10000, None)], TZ) == {}


def test_the_weights_cover_the_domains_and_the_keys_are_the_engines():
    assert sorted(name for names in DOMAINS.values() for name in names) == sorted(WEIGHTS)
    assert FULL_KEYS <= DAILY_KEYS


def test_the_engine_stores_it_the_lamp_reads_it_and_the_summary_never_sees_it(db, settings):
    stored_history(db)
    store_night(db, DAY, 5.5, 57.0)
    db.commit()
    recompute(db, settings, DAY.isoformat(), NOW)
    row = daily(db, DAY)
    trace = row["bandwidth_full_trace"]
    assert trace["sleep"]["z"] == row["bandwidth_trace"]["sleep"]["z"] == -2.0
    assert trace["sleep_hr"]["z"] == -1.0
    assert trace["recent_nights"]["z"] == 2.0
    assert trace["sleep_timing"]["z"] == 0.0
    assert trace["weight_present"] == 30 + 14 + 12 + 24 + 4
    assert trace["load"]["z"] == 0.0, "no workout on record is a term at 0"
    assert row["bandwidth_full"] == 40.67, "50 + (-60 + 28 + 0 - 24) / 6"
    assert (row["bandwidth"], row["bandwidth_low"]) == (25.0, True), "the two-term score is red"
    assert (row["bandwidth_full_lamp"], row["bandwidth_full_low"]) == (40.67, False)
    view = view_from_db(db, settings, DAY.isoformat())
    assert (view.bandwidth_lamp, view.bandwidth_low) == (40.67, False), "the lamp reads the fuller"
    text = build_payload(db, settings, (DAY + timedelta(days=1)).isoformat()).text()
    assert "bandwidth" not in text and "40.67" not in text
    before = daily(db, DAY)
    recompute(db, settings, DAY.isoformat(), NOW)
    assert daily(db, DAY) == before


def test_the_lamp_needs_sixty_of_the_hundred_so_a_thin_day_is_gold():
    full = full_json(DAY, ordinary())
    assert full["bandwidth_full_lamp"] == full["bandwidth_full"] == ORDINARY
    thin = full_json(DAY, FullInputs(sleep_hours={DAY: 7.5}, tz=TZ))
    assert (thin["bandwidth_full"], thin["bandwidth_full_lamp"]) == (60.0, None)
    worn = {DAY: NightSignal(None, 0, False)}
    short = full_json(DAY, FullInputs(sleep_hours={DAY: 4.5}, nights=worn, tz=TZ))
    assert short["bandwidth_full"] == 35.0 < FULL_RED
    assert (short["bandwidth_full_lamp"], short["bandwidth_full_low"]) == (None, False)
    edge = replace(ordinary(), stages={}, nights={}, hrv_ms={}, resting_hr={})
    assert full_json(DAY, edge)["bandwidth_full_trace"]["weight_present"] == 64
    assert full_json(DAY, edge)["bandwidth_full_lamp"] is not None
    under = replace(edge, daylight_min={}, steps={})
    assert full_json(DAY, under)["bandwidth_full_trace"]["weight_present"] == 60
    assert full_json(DAY, under)["bandwidth_full_lamp"] is not None, "sixty is enough"
    under = replace(under, load_trimp={})
    assert full_json(DAY, under)["bandwidth_full_trace"]["weight_present"] == 56
    assert full_json(DAY, under)["bandwidth_full_lamp"] is None


def test_before_the_night_arrives_today_carries_yesterdays_lamp_and_cue():
    base = changed(sleep_hours={YESTERDAY: 4.5}, nights={YESTERDAY: NightSignal(61.0, 40, False)})
    for name in ("sleep_hours", "nights", "stages", "onsets"):
        getattr(base, name).pop(DAY)
    bad = full_json(YESTERDAY, base)
    assert bad["bandwidth_full_low"] is True and bad["bandwidth_full"] < FULL_RED
    plain = full_json(DAY, base)
    assert (plain["bandwidth_full"], plain["bandwidth_full_low"]) == (None, False)
    assert plain["bandwidth_full_lamp"] is None
    carried = full_json(DAY, base, awaiting_night=True)
    assert (carried["bandwidth_full"], carried["bandwidth_full_low"]) == (None, True)
    assert carried["bandwidth_full_lamp"] == bad["bandwidth_full"]
    assert carried["bandwidth_full_trace"]["carried_from"] == YESTERDAY.isoformat()
    arrived = full_json(DAY, ordinary(), awaiting_night=True)
    assert (arrived["bandwidth_full_lamp"], arrived["bandwidth_full_low"]) == (ORDINARY, False)
    assert arrived["bandwidth_full_trace"]["carried_from"] is None
    worn = replace(base, nights={**base.nights, DAY: NightSignal(None, 3, False)})
    assert full_json(DAY, worn, awaiting_night=True)["bandwidth_full_low"] is False


def test_after_midnight_the_lamp_holds_through_the_engine_until_noon(db, settings):
    stored_history(db)
    store_night(db, DAY, 4.5, 61.0)
    db.commit()
    tomorrow = DAY + timedelta(days=1)
    recompute(db, settings, tomorrow.isoformat(), datetime(2026, 10, 16, 5, tzinfo=UTC))
    assert daily(db, DAY)["bandwidth_full"] == 27.67, "50 + (-90 + 28 + 0 - 72) / 6"
    row = daily(db, tomorrow)
    assert (row["bandwidth_full"], row["bandwidth_full_low"]) == (None, True)
    assert row["bandwidth_full_lamp"] == 27.67
    view = view_from_db(db, settings, tomorrow.isoformat())
    assert (view.bandwidth_lamp, view.bandwidth_low) == (27.67, True)
    recompute(db, settings, tomorrow.isoformat(), datetime(2026, 10, 16, 16, 1, tzinfo=UTC))
    row = daily(db, tomorrow)
    assert (row["bandwidth_full_low"], row["bandwidth_full_lamp"]) == (False, None)
    later = tomorrow + timedelta(days=1)
    recompute(db, settings, later.isoformat(), datetime(2026, 10, 17, 5, tzinfo=UTC))
    assert daily(db, tomorrow)["bandwidth_full_low"] is False, "a past day never carries"
    assert daily(db, later)["bandwidth_full_lamp"] is None, "a carried cue is not carried again"


def test_the_tool_names_each_score_by_its_own_stops():
    assert band(None, False, TWO_STOPS) == "none"
    assert [band(v, False, TWO_STOPS) for v in (33.33, 40.0, 50.0, 60.0, 66.67)] == [
        "red",
        "orange",
        "gold",
        "lime",
        "green",
    ]
    assert [band(v, False, FULL_STOPS) for v in (39.74, 44.0, 50.0, 57.0, 60.26)] == [
        "red",
        "orange",
        "gold",
        "lime",
        "green",
    ]
    assert band(45.0, True, FULL_STOPS) == "red"
    out = full_json(DAY, changed(sleep_hours={DAY: 5.5}))
    row = {"bandwidth": 33.33, "bandwidth_low": True, **out}
    line = day_line(DAY.isoformat(), row)
    assert "two  33.3 red" in line and "full  44.7 orange" in line and "100/100" in line
    assert "sleep -10.0" in line
    lines = trace_lines(DAY.isoformat(), row)
    assert any(text.startswith("50 + the points = 44.67; stored 44.67") for text in lines)
    assert "recovery" in lines and "sleep amount" in lines
