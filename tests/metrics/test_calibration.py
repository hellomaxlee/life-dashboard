"""The load bar: first calibration, re-calibration every period, kept when runs are few,
stored history never rewritten."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from app.config import WorkoutConfig
from app.metrics.calendar import add_months
from app.metrics.calibration import (
    BarEntry,
    RunLoad,
    bar_for_week,
    is_calibration_run,
    median,
    plan_calibration,
)
from app.metrics.keys import round_to_step


def run(day: str, load: float, hour: int = 11) -> RunLoad:
    return RunLoad(f"R-{day}", f"{day}T{hour:02d}:00:00Z", load)


def test_rounding_to_five_is_half_up():
    assert round_to_step(112.5, 5) == 115.0
    assert round_to_step(112.4, 5) == 110.0
    assert round_to_step(117.5, 5) == 120.0


def test_median_of_even_count_is_the_mean_of_the_middle_two():
    assert median([100, 130, 110, 120]) == 115


def test_calibration_run_needs_type_distance_and_a_load():
    assert is_calibration_run("run", 4 * 1609.344, 120.0)
    assert not is_calibration_run("run", 4 * 1609.344 - 1, 120.0)
    assert not is_calibration_run("ride", 20000.0, 120.0)
    assert not is_calibration_run("run", None, 120.0)
    assert not is_calibration_run("run", 7000.0, None)


def test_fewer_than_min_runs_leaves_the_placeholder(settings):
    runs = [run("2026-09-28", 120), run("2026-09-30", 110)]
    assert plan_calibration([], runs, settings, date(2026, 10, 23)) == []
    assert bar_for_week([], "2026-10-05", settings.workout.load_bar) == (100.0, "placeholder")


def test_first_calibration_uses_the_first_three_runs_in_time_order(settings):
    runs = [
        run("2026-10-02", 130),
        run("2026-09-28", 120),
        run("2026-09-30", 110),
        run("2026-10-04", 200),
    ]
    entries = plan_calibration([], runs, settings, date(2026, 10, 23))
    assert entries == [
        BarEntry(
            "2026-10-05",
            120.0,
            ("R-2026-09-28", "R-2026-09-30", "R-2026-10-02"),
            "2026-10-02T11:00:00Z",
        )
    ]
    assert bar_for_week(entries, "2026-09-28", 100.0) == (100.0, "placeholder")
    assert bar_for_week(entries, "2026-10-05", 100.0) == (120.0, "calibrated")


def test_a_decision_on_a_sunday_night_utc_monday_still_applies_the_week_after_the_local_week(
    settings,
):
    runs = [run("2026-09-28", 120), run("2026-09-30", 110), run("2026-10-05", 130, hour=2)]
    entries = plan_calibration([], runs, settings, date(2026, 10, 23))
    assert entries[0].effective_from_week == "2026-10-05"


def test_recalibration_every_period_from_the_trailing_window(settings):
    first = [run("2026-01-05", 100), run("2026-01-07", 100), run("2026-01-09", 100)]
    later = [run("2026-02-10", 140), run("2026-03-10", 150), run("2026-04-01", 160)]
    entries = plan_calibration([], first + later, settings, date(2026, 7, 15))
    assert [e.effective_from_week for e in entries] == ["2026-01-12", "2026-04-13"]
    second = entries[1]
    assert second.value == 150.0
    assert second.source_ids == ("R-2026-02-10", "R-2026-03-10", "R-2026-04-01")
    assert second.decided_at_utc == "2026-04-09T04:00:00Z"


def test_recalibration_is_kept_with_too_few_runs_and_checked_again_next_period(settings):
    first = [run("2026-01-05", 100), run("2026-01-07", 100), run("2026-01-09", 100)]
    sparse = [run("2026-03-01", 150), run("2026-03-02", 150)]
    dense = [run("2026-05-01", 130), run("2026-06-01", 130), run("2026-07-01", 130)]
    entries = plan_calibration([], first + sparse + dense, settings, date(2026, 7, 15))
    assert [(e.effective_from_week, e.value) for e in entries] == [
        ("2026-01-12", 100.0),
        ("2026-07-13", 130.0),
    ]


def test_an_unchanged_median_records_nothing(settings):
    first = [run("2026-01-05", 100), run("2026-01-07", 100), run("2026-01-09", 100)]
    same = [run("2026-02-01", 100), run("2026-03-01", 100), run("2026-04-01", 100)]
    entries = plan_calibration([], first + same, settings, date(2026, 7, 15))
    assert len(entries) == 1


def test_stored_history_is_kept_even_when_the_data_would_decide_otherwise(settings):
    stored = [BarEntry("2026-10-05", 90.0, ("manual",), "2026-10-02T11:00:00Z")]
    runs = [run("2026-09-28", 120), run("2026-09-30", 110), run("2026-10-02", 130)]
    entries = plan_calibration(stored, runs, settings, date(2026, 10, 23))
    assert entries == stored


def test_nothing_is_decided_before_today(settings):
    first = [run("2026-01-05", 100), run("2026-01-07", 100), run("2026-01-09", 100)]
    later = [run("2026-02-10", 140), run("2026-03-10", 150), run("2026-04-01", 160)]
    entries = plan_calibration([], first + later, settings, date(2026, 4, 8))
    assert len(entries) == 1


def test_period_length_comes_from_config(settings):
    tuned = replace(settings, workout=WorkoutConfig(100.0, 1, 3))
    first = [run("2026-01-05", 100), run("2026-01-07", 100), run("2026-01-09", 100)]
    later = [run("2026-01-20", 140), run("2026-01-25", 150), run("2026-02-01", 160)]
    entries = plan_calibration([], first + later, tuned, date(2026, 3, 1))
    assert [e.effective_from_week for e in entries] == ["2026-01-12", "2026-02-16"]


def test_add_months_clamps_the_day():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)
    assert add_months(date(2026, 4, 9), -3) == date(2026, 1, 9)
