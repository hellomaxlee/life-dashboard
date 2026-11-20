"""The night recovery score (app/metrics/bandwidth.py): hand-computed cases, the range and
monotonicity claims swept over hostile inputs, the missing-data rules, and the path from
stored nights through the engine to the Books lamp. The cases are small enough to redo on
paper: with a constant history the scale is its floor (1.0 h, 2.0 bpm)."""

from __future__ import annotations

import json
import math
import random
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from app.metrics.bandwidth import (
    BANDWIDTH_KEYS,
    MIN_HISTORY,
    NightSignal,
    bandwidth_json,
    night_signals,
)
from app.metrics.engine import recompute
from app.metrics.keys import DAILY_KEYS
from app.metrics.sleep import SleepFragment, night_spans
from app.render.palette import GOLD, GREEN, RED, to_oklch
from app.render.screens import DECOR_X, LAMP, TIER_BOARDS, lamp_shade, render_books
from app.render.view import DayView
from app.render.view_db import view_from_db
from app.summary.payload import build_payload

DAY = date(2026, 10, 15)
WORN = NightSignal(sleep_hr=None, hr_rows=0, censored=False)


def history(
    sleep: float = 7.5, sleep_hr: float = 55.0, nights: int = MIN_HISTORY
) -> tuple[dict[date, float], dict[date, NightSignal]]:
    days = [DAY - timedelta(days=k) for k in range(1, nights + 1)]
    return (
        {d: sleep for d in days},
        {d: NightSignal(sleep_hr, 40, False) for d in days},
    )


def score(
    sleep: float | None,
    sleep_hr: float | None,
    *,
    censored: bool = False,
    base: tuple[dict[date, float], dict[date, NightSignal]] | None = None,
) -> dict[str, object]:
    hours, nights = base if base is not None else history()
    hours, nights = dict(hours), dict(nights)
    if sleep is not None:
        hours[DAY] = sleep
    nights[DAY] = NightSignal(sleep_hr, 40, censored)
    return bandwidth_json(DAY, hours, nights)


def test_a_short_night_with_a_raised_heart_rate_is_29_17_and_low():
    """z_sleep = (6.0 - 7.5) / 1.0 = -1.5; z_hr = -(57 - 55) / 2.0 = -1.0;
    B = 50 + (25/3) x -2.5 = 29.17."""
    out = score(6.0, 57.0)
    assert out["bandwidth"] == 29.17
    assert out["bandwidth_low"] is True
    trace = out["bandwidth_trace"]
    assert trace["sleep"] == {
        "x": 6.0,
        "reference": 7.5,
        "scale": 1.0,
        "z": -1.5,
        "points": -12.5,
        "history": 14,
    }
    assert trace["sleep_hr"]["z"] == -1.0
    assert trace["sleep_hr"]["points"] == -8.33
    readded = 50 + trace["sleep"]["points"] + trace["sleep_hr"]["points"]
    assert abs(readded - out["bandwidth"]) <= 0.01


def test_the_cue_line_is_a_z_sum_of_exactly_minus_two():
    on = score(6.5, 57.0)
    assert (on["bandwidth"], on["bandwidth_low"]) == (33.33, True)
    off = score(6.51, 57.0)
    assert (off["bandwidth"], off["bandwidth_low"]) == (33.42, False)


def test_an_ordinary_night_is_fifty_and_the_ends_are_reached_without_a_clamp_on_b():
    assert score(7.5, 55.0)["bandwidth"] == 50.0
    assert score(0.5, 110.0)["bandwidth"] == 0.0
    assert score(15.0, 31.0)["bandwidth"] == 83.33


def test_a_long_night_counts_for_one_scale_unit_at_most():
    """12 h against 7.5 is z = +4.5 unclamped; it enters as +1, so it cannot cancel a
    sleeping heart rate two scale units up (z = -2): 50 + (25/3) x -1 = 41.67."""
    assert score(8.5, 55.0)["bandwidth"] == 58.33
    assert score(12.0, 55.0)["bandwidth"] == 58.33
    assert score(12.0, 59.0)["bandwidth"] == 41.67


def test_the_cue_is_decided_on_the_z_sum_not_on_the_rounded_score():
    """A z sum of -1.9999 stores 33.33 like the line itself, and is off."""
    near = score(6.5001, 57.0)
    assert (near["bandwidth"], near["bandwidth_low"]) == (33.33, False)
    for hours, rate in ((5.54, 55.08), (6.3, 56.6), (6.9, 57.8)):
        tie = score(hours, rate)
        assert (tie["bandwidth"], tie["bandwidth_low"]) == (33.33, True), (hours, rate)


def test_an_absent_term_adds_nothing_and_the_other_is_not_reweighted():
    """Heart rate alone at z = -1.5 is 37.5 and off; renormalised it would be 25 and on."""
    alone = score(None, 58.0)
    assert (alone["bandwidth"], alone["bandwidth_low"]) == (37.5, False)
    assert alone["bandwidth_trace"]["sleep"] is None
    assert score(None, 59.0)["bandwidth_low"] is True
    sleep_only = score(6.0, None)
    assert (sleep_only["bandwidth"], sleep_only["bandwidth_low"]) == (37.5, False)


def test_no_term_is_no_reading_never_fifty_and_never_the_cue():
    out = score(None, None)
    assert out["bandwidth"] is None
    assert out["bandwidth_low"] is False
    short = score(3.0, 90.0, base=history(nights=MIN_HISTORY - 1))
    assert (short["bandwidth"], short["bandwidth_low"]) == (None, False)
    assert score(3.0, 90.0, base=history(nights=MIN_HISTORY))["bandwidth"] == 0.0


def test_a_censored_night_is_a_lower_bound_so_it_never_lowers_the_score():
    """The watch came off at 4.0 h: sleep was at least that, so the term is 0, not -3."""
    cut = score(4.0, 55.0, censored=True)
    assert (cut["bandwidth"], cut["bandwidth_low"]) == (50.0, False)
    assert cut["bandwidth_trace"]["censored"] is True
    assert score(4.0, 55.0)["bandwidth"] == 25.0
    assert score(8.0, 55.0, censored=True)["bandwidth"] == 54.17


def test_a_censored_night_cannot_lower_the_heart_rate_term_either():
    """Half a night's median runs high (the first half sits about 2 bpm over the second),
    so a raised rate on a censored night is 0, and a lower one still counts."""
    assert score(7.5, 61.0, censored=True)["bandwidth"] == 50.0
    assert score(7.5, 61.0)["bandwidth"] == 25.0
    assert score(7.5, 53.0, censored=True)["bandwidth"] == 58.33
    assert score(4.0, 70.0, censored=True)["bandwidth_low"] is False


def test_the_censored_bound_is_the_capped_reference_not_the_median():
    """History of 9.0 h nights: reference 8.0. A censored 8.5 h night is +0.5, not +1."""
    assert score(8.5, 55.0, censored=True, base=history(sleep=9.0))["bandwidth"] == 54.17


def test_a_night_with_no_signal_row_is_treated_as_censored():
    hours, nights = history()
    hours[DAY] = 4.0
    out = bandwidth_json(DAY, hours, nights)
    assert (out["bandwidth"], out["bandwidth_low"]) == (50.0, False)


@pytest.mark.parametrize("hours", [0.0, -1.0, 16.01, float("nan"), float("inf")])
def test_an_impossible_night_is_absent(hours):
    assert score(hours, None)["bandwidth"] is None
    assert score(hours, 59.0)["bandwidth"] == 33.33


@pytest.mark.parametrize("rate", [29.9, 120.1, 150.0, float("nan"), float("inf")])
def test_an_impossible_sleeping_heart_rate_is_absent(rate):
    out = score(None, rate)
    assert out["bandwidth"] is None
    assert json.dumps(out, allow_nan=False)
    assert score(6.0, rate)["bandwidth"] == 37.5


def test_yesterdays_cue_is_carried_only_while_today_awaits_its_night():
    hours, nights = history(nights=MIN_HISTORY + 1)
    yesterday = DAY - timedelta(days=1)
    hours[yesterday], nights[yesterday] = 5.0, NightSignal(57.0, 40, False)
    assert bandwidth_json(yesterday, hours, nights)["bandwidth_low"] is True
    plain = bandwidth_json(DAY, hours, nights)
    assert (plain["bandwidth"], plain["bandwidth_low"]) == (None, False)
    assert plain["bandwidth_lamp"] is None
    carried = bandwidth_json(DAY, hours, nights, awaiting_night=True)
    assert (carried["bandwidth"], carried["bandwidth_low"]) == (None, True)
    assert carried["bandwidth_lamp"] == 20.83
    assert carried["bandwidth_trace"]["carried_from"] == yesterday.isoformat()
    hours[yesterday] = 7.5
    ordinary = bandwidth_json(DAY, hours, nights, awaiting_night=True)
    assert (ordinary["bandwidth_low"], ordinary["bandwidth_lamp"]) == (False, 41.67)
    del hours[yesterday], nights[yesterday]
    nothing = bandwidth_json(DAY, hours, nights, awaiting_night=True)
    assert (nothing["bandwidth_lamp"], nothing["bandwidth_trace"]["carried_from"]) == (None, None)
    hours[yesterday], nights[yesterday] = 5.0, NightSignal(57.0, 40, False)
    hours[yesterday] = 5.0
    hours[DAY], nights[DAY] = 7.5, NightSignal(55.0, 40, False)
    arrived = bandwidth_json(DAY, hours, nights, awaiting_night=True)
    assert (arrived["bandwidth"], arrived["bandwidth_low"]) == (50.0, False)
    assert arrived["bandwidth_lamp"] == 50.0
    del hours[DAY]
    nights[DAY] = NightSignal(None, 3, True)
    assert bandwidth_json(DAY, hours, nights, awaiting_night=True)["bandwidth_low"] is False


def test_censored_nights_stay_out_of_the_history():
    hours, nights = history()
    for k in range(1, 4):
        nights[DAY - timedelta(days=k)] = NightSignal(55.0, 40, True)
    thin = score(6.0, 57.0, base=(hours, nights))["bandwidth_trace"]
    assert thin["sleep"] is None and thin["sleep_hr"] is None
    for k in range(15, 18):
        hours[DAY - timedelta(days=k)] = 7.5
        nights[DAY - timedelta(days=k)] = NightSignal(55.0, 40, False)
    full = score(6.0, 57.0, base=(hours, nights))["bandwidth_trace"]
    assert full["sleep"]["history"] == 14 and full["sleep_hr"]["history"] == 14


def test_the_reference_night_is_capped_at_eight_hours():
    out = score(8.0, 55.0, base=history(sleep=9.0))
    assert out["bandwidth_trace"]["sleep"]["reference"] == 8.0
    assert out["bandwidth"] == 50.0


def test_the_scale_is_the_mad_when_it_is_wider_than_the_floor():
    """Seven nights of 6.0 and seven of 9.0: median 7.5, MAD 1.5, scale 1.4826 x 1.5."""
    hours, nights = history()
    for k, day in enumerate(sorted(hours)):
        hours[day] = 6.0 if k % 2 else 9.0
    trace = score(6.0, 55.0, base=(hours, nights))["bandwidth_trace"]["sleep"]
    assert trace["scale"] == 2.22
    assert trace["z"] == pytest.approx(-1.5 / (1.4826 * 1.5), abs=1e-4)


def test_only_the_prior_28_days_are_history():
    hours, nights = history(nights=28)
    old = DAY - timedelta(days=29)
    hours[old], nights[old] = 1.0, NightSignal(100.0, 40, False)
    assert score(7.5, 55.0, base=(hours, nights))["bandwidth_trace"]["sleep"]["history"] == 28
    future = DAY + timedelta(days=1)
    hours[future], nights[future] = 1.0, NightSignal(100.0, 40, False)
    assert score(7.5, 55.0, base=(hours, nights))["bandwidth"] == 50.0


HOSTILE = (float("nan"), float("inf"), float("-inf"), 0.0, -4.0, 1e308, 16.0, 16.01, 5e-324)


def test_every_score_is_in_range_or_absent_whatever_the_inputs():
    rng = random.Random(20261120)
    seen_low = seen_high = False
    readings = 0
    for _ in range(4000):
        hours: dict[date, float] = {}
        nights: dict[date, NightSignal] = {}
        for k in range(0, 40):
            day = DAY - timedelta(days=k)
            if rng.random() < 0.8:
                wild = rng.uniform(0, 17) if rng.random() < 0.3 else rng.gauss(7.5, 1.2)
                hours[day] = rng.choice(HOSTILE) if rng.random() < 0.1 else wild
            if rng.random() < 0.85:
                rate = rng.choice(
                    (None, rng.uniform(30, 120), *[rng.gauss(56, 3)] * 6, *HOSTILE[:3])
                )
                nights[day] = NightSignal(rate, rng.randrange(0, 200), rng.random() < 0.15)
        out = bandwidth_json(DAY, hours, nights)
        value = out["bandwidth"]
        assert json.dumps(out, allow_nan=False)
        if value is None:
            assert out["bandwidth_low"] is False
            continue
        assert isinstance(value, float) and math.isfinite(value) and 0.0 <= value <= 83.34
        assert out["bandwidth_low"] is (value <= 33.33) or abs(value - 100 / 3) < 0.01
        seen_low, seen_high = seen_low or value < 30, seen_high or value > 60
        readings += 1
    assert seen_low and seen_high and readings > 1000, readings


def test_more_sleep_never_lowers_it_and_a_higher_sleeping_heart_rate_never_raises_it():
    rng = random.Random(7)
    for _ in range(300):
        base = history(sleep=rng.uniform(5, 10), sleep_hr=rng.uniform(45, 70))
        censored = rng.random() < 0.3
        rate = rng.uniform(40, 90)
        by_sleep = [
            score(h / 10, rate, censored=censored, base=base)["bandwidth"] for h in range(1, 161)
        ]
        assert by_sleep == sorted(by_sleep)
        hours = rng.uniform(3, 11)
        by_rate = [
            score(hours, float(r), censored=censored, base=base)["bandwidth"]
            for r in range(30, 121)
        ]
        assert by_rate == sorted(by_rate, reverse=True)


def test_one_wobble_in_an_input_moves_the_score_by_a_bounded_amount():
    """Lipschitz: 25/3 points per scale unit, so 8.33 per hour and 4.17 per bpm at the
    floors, plus 0.01 for the two-decimal rounding of each stored score."""
    for tenth in range(40, 110):
        a, b = score(tenth / 10, 55.0)["bandwidth"], score(tenth / 10 + 0.1, 55.0)["bandwidth"]
        assert abs(b - a) <= 0.85
    for rate in range(40, 80):
        a, b = score(7.5, float(rate))["bandwidth"], score(7.5, rate + 1.0)["bandwidth"]
        assert abs(b - a) <= 4.18


def utc(hour: int, minute: int = 0, day: int = 15) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def test_sleeping_heart_rate_is_the_median_inside_the_night_and_needs_thirty_rows():
    span = {DAY: (utc(3), utc(11))}
    rows = [(utc(3) + timedelta(minutes=5 * k), 50.0 + (k % 3)) for k in range(30)]
    worn = [(utc(11, 30), 70.0)]
    signal = night_signals(span, rows + worn)[DAY]
    assert signal == NightSignal(51.0, 30, False)
    assert night_signals(span, rows[:29] + worn)[DAY].sleep_hr is None
    outside = [(utc(2, 59), 150.0), (utc(11, 1), 150.0)]
    assert night_signals(span, rows + outside)[DAY].sleep_hr == 51.0
    assert night_signals(span, [(t, 25.0) for t, _ in rows])[DAY].sleep_hr is None
    assert night_signals(span, [(t, 150.0) for t, _ in rows])[DAY].sleep_hr is None
    broken = [(utc(3) + timedelta(minutes=5 * k + 1), float("nan")) for k in range(30)]
    assert night_signals(span, rows + broken + worn)[DAY] == NightSignal(51.0, 30, False)


def test_a_repeated_timestamp_is_one_reading():
    span = {DAY: (utc(3), utc(11))}
    rows = [(utc(3) + timedelta(minutes=5 * k), 50.0) for k in range(29)]
    assert night_signals(span, rows + [rows[0]])[DAY] == NightSignal(None, 29, True)


def test_a_night_span_is_the_union_of_its_sources_and_leaves_naps_out():
    def fragment(start: str, end: str, source: str) -> SleepFragment:
        return SleepFragment(DAY, start, 3600, source, end)

    spans = night_spans(
        [
            fragment("2026-10-15T03:10:00Z", "2026-10-15T07:00:00Z", "Watch"),
            fragment("2026-10-15T08:10:00Z", "2026-10-15T11:00:00Z", "Watch"),
            fragment("2026-10-15T02:50:00Z", "2026-10-15T10:40:00Z", "Phone"),
            fragment("2026-10-15T18:00:00Z", "2026-10-15T19:00:00Z", "Watch"),
            SleepFragment(DAY, "2026-10-15T04:00:00Z", 3600, "Other"),
        ],
        "America/New_York",
    )
    assert spans == {DAY: (utc(2, 50), utc(11))}


def test_a_night_is_censored_without_a_reading_in_the_ninety_minutes_after_it():
    span = {DAY: (utc(3), utc(11))}
    assert night_signals(span, [(utc(12, 30), 60.0)])[DAY].censored is False
    assert night_signals(span, [(utc(12, 31), 60.0)])[DAY].censored is True
    assert night_signals(span, [(utc(11), 60.0)])[DAY].censored is True
    assert night_signals(span, [])[DAY].censored is True


def store_night(db, wake: date, asleep_h: float, bpm: float, *, worn_after: bool = True) -> None:
    start = datetime(wake.year, wake.month, wake.day, 3, tzinfo=UTC)
    end = start + timedelta(hours=8)
    db.execute(
        "INSERT INTO sleep_sessions (id, wake_day_local, start_utc, end_utc, in_bed_s, "
        "asleep_s, source) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            f"night-{wake}",
            wake.isoformat(),
            start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            8 * 3600,
            int(asleep_h * 3600),
            "Watch",
        ),
    )
    minutes = [start + timedelta(minutes=10 * k) for k in range(1, 41)]
    if worn_after:
        minutes.append(end + timedelta(minutes=20))
    for minute in minutes:
        db.execute(
            "INSERT INTO hr_minutes (day_local, minute_utc, hr_min, hr_avg, hr_max) "
            "VALUES (?, ?, ?, ?, ?)",
            (wake.isoformat(), minute.strftime("%Y-%m-%dT%H:%M:%SZ"), bpm, bpm, bpm),
        )


def stored_history(db) -> None:
    for k in range(1, MIN_HISTORY + 1):
        store_night(db, DAY - timedelta(days=k), 7.5, 55.0)


def daily(db, day: date) -> dict:
    row = db.execute(
        "SELECT metrics_json FROM daily_metrics WHERE day_local = ?", (day.isoformat(),)
    ).fetchone()
    return json.loads(row["metrics_json"])


NOW = datetime(2026, 10, 15, 16, tzinfo=UTC)


def test_the_engine_scores_stored_nights_and_the_view_carries_only_the_cue(db, settings):
    stored_history(db)
    store_night(db, DAY, 6.0, 57.0)
    db.commit()
    recompute(db, settings, DAY.isoformat(), NOW)
    row = daily(db, DAY)
    assert (row["bandwidth"], row["bandwidth_low"], row["bandwidth_lamp"]) == (29.17, True, 29.17)
    assert view_from_db(db, settings, DAY.isoformat()).bandwidth_lamp == 29.17
    assert row["bandwidth_trace"]["hr_rows"] == 40
    assert daily(db, DAY - timedelta(days=1))["bandwidth"] is None
    assert view_from_db(db, settings, DAY.isoformat()).bandwidth_low is True
    assert view_from_db(db, settings, (DAY - timedelta(days=1)).isoformat()).bandwidth_low is False
    before = daily(db, DAY)
    recompute(db, settings, DAY.isoformat(), NOW)
    assert daily(db, DAY) == before


def test_after_midnight_the_cue_holds_until_the_new_night_or_noon(db, settings):
    stored_history(db)
    store_night(db, DAY, 6.0, 57.0)
    db.commit()
    tomorrow = DAY + timedelta(days=1)
    recompute(db, settings, tomorrow.isoformat(), datetime(2026, 10, 16, 5, tzinfo=UTC))
    row = daily(db, tomorrow)
    assert (row["bandwidth"], row["bandwidth_low"], row["bandwidth_lamp"]) == (None, True, 29.17)
    assert row["bandwidth_trace"]["carried_from"] == DAY.isoformat()
    assert view_from_db(db, settings, tomorrow.isoformat()).bandwidth_low is True
    recompute(db, settings, tomorrow.isoformat(), datetime(2026, 10, 16, 16, 1, tzinfo=UTC))
    assert daily(db, tomorrow)["bandwidth_low"] is False
    assert daily(db, tomorrow)["bandwidth_lamp"] is None
    recompute(db, settings, tomorrow.isoformat(), datetime(2026, 10, 16, 15, 59, tzinfo=UTC))
    assert daily(db, tomorrow)["bandwidth_low"] is True
    later = tomorrow + timedelta(days=1)
    recompute(db, settings, later.isoformat(), datetime(2026, 10, 17, 5, tzinfo=UTC))
    assert daily(db, tomorrow)["bandwidth_low"] is False, "a past day never carries"
    assert daily(db, later)["bandwidth_low"] is False, "a carried cue is not carried again"
    assert daily(db, later)["bandwidth_lamp"] is None
    store_night(db, tomorrow, 7.5, 55.0)
    db.commit()
    recompute(db, settings, tomorrow.isoformat(), datetime(2026, 10, 16, 12, tzinfo=UTC))
    row = daily(db, tomorrow)
    assert (row["bandwidth"], row["bandwidth_low"]) == (50.0, False)


def test_a_night_the_watch_left_early_does_not_draw_the_cue_through_the_engine(db, settings):
    stored_history(db)
    store_night(db, DAY, 3.2, 55.0, worn_after=False)
    db.commit()
    recompute(db, settings, DAY.isoformat(), NOW)
    row = daily(db, DAY)
    assert (row["bandwidth"], row["bandwidth_low"]) == (50.0, False)
    assert row["bandwidth_trace"]["censored"] is True


def test_the_score_is_an_engine_key_and_never_reaches_the_summary_model(db, settings):
    assert BANDWIDTH_KEYS <= DAILY_KEYS
    stored_history(db)
    store_night(db, DAY, 6.0, 57.0)
    db.commit()
    recompute(db, settings, DAY.isoformat(), NOW)
    text = build_payload(db, settings, (DAY + timedelta(days=1)).isoformat()).text()
    assert "bandwidth" not in text
    assert "29.17" not in text


def shade_pixels(view: DayView) -> set:
    frame = render_books(view).poster
    top = TIER_BOARDS[1] - len(LAMP)
    return {
        frame.getpixel((DECOR_X + dx, top + dy))
        for dy, row in enumerate(LAMP)
        for dx, cell in enumerate(row)
        if cell == "y"
    }


def lamp(score: float | None, low: bool = False) -> tuple[int, int, int]:
    return lamp_shade(DayView(day_local="2026-11-16", bandwidth_lamp=score, bandwidth_low=low))


def test_the_lamp_is_red_at_the_cue_gold_on_an_ordinary_night_and_green_above():
    assert lamp(None) == GOLD, "no reading is the plain lamp, never a colour"
    assert lamp(50.0) == GOLD
    assert lamp(0.0) == lamp(33.33) == RED
    assert lamp(66.67) == lamp(83.33) == GREEN
    assert lamp(45.0, low=True) == lamp(None, low=True) == RED, "the cue decides, not the score"
    assert lamp(33.34) != GOLD and lamp(49.0) != RED


def test_between_the_stops_the_lamp_moves_one_way_through_orange_and_lime():
    """Hue angle in OKLCH rises from red through gold to green, never back."""
    scores = [100 / 3 + k * (100 / 3) / 40 for k in range(41)]
    hues = [to_oklch(lamp(score))[2] for score in scores]
    assert hues == sorted(hues)
    assert len({lamp(score) for score in scores}) >= 30
    orange, lime = lamp(41.67), lamp(58.33)
    assert orange[0] > orange[1] > orange[2] and orange[1] < GOLD[1]
    assert lime[1] > lime[0] > lime[2] and lime[0] < GOLD[0]
    for score in scores:
        assert to_oklch(lamp(score))[1] >= 0.9 * min(to_oklch(c)[1] for c in (RED, GOLD, GREEN))


def test_an_impossible_stored_lamp_score_is_no_reading():
    from app.config import load_settings
    from app.render.view import view_from_metrics

    for stored in (None, "41", True, -1, 100.5, float("nan")):
        view = view_from_metrics("2026-11-16", {"bandwidth_lamp": stored}, {}, load_settings())
        assert view.bandwidth_lamp is None, stored
    assert (
        view_from_metrics(
            "2026-11-16", {"bandwidth_lamp": 41.67}, {}, load_settings()
        ).bandwidth_lamp
        == 41.67
    )


@pytest.mark.parametrize("weekday", range(7))
def test_the_score_changes_the_lamps_shade_and_nothing_else(weekday):
    day = (date(2026, 11, 16) + timedelta(days=weekday)).isoformat()
    view = DayView(day_local=day, books_ytd=7)
    assert shade_pixels(view) == {GOLD}
    assert shade_pixels(replace(view, bandwidth_low=True, bandwidth_lamp=29.17)) == {RED}
    assert shade_pixels(replace(view, bandwidth_lamp=70.0)) == {GREEN}
    plain = render_books(view).poster
    for score, low in ((29.17, True), (41.67, False), (70.0, False)):
        coloured = render_books(replace(view, bandwidth_lamp=score, bandwidth_low=low)).poster
        changed = [
            (x, y)
            for x in range(64)
            for y in range(64)
            if plain.getpixel((x, y)) != coloured.getpixel((x, y))
        ]
        assert len(changed) == sum(row.count("y") for row in LAMP), score
