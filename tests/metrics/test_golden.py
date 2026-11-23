"""Every hand-written golden case under fixtures/golden passes through the real engine."""

from __future__ import annotations

import pytest

from tests.metrics.golden import golden_files, load_golden, run_golden

CASES = golden_files()


@pytest.mark.parametrize("path", CASES, ids=[p.stem for p in CASES])
def test_golden_case(db, settings, path):
    case = load_golden(path)
    problems = run_golden(db, settings, case)
    assert not problems, "\n".join([case["name"], *problems])


def test_there_are_golden_cases_for_every_rule():
    names = {p.stem for p in CASES}
    assert {
        "train_all_alive_base_third_dot",
        "rest_all_alive_base_sleep",
        "train_no_hr_alive_peak",
        "rest_sleep_missing_broken_base",
        "train_never_started_off",
        "race_week_two_dots_wed",
        "sunday_workout_arrives_monday",
        "calibration_mid_history",
        "ewma_seven_day_rest",
        "books_read_at_and_date_added",
        "wellness_band_crossing",
        "wellness_rhr_and_daylight",
    } <= names
