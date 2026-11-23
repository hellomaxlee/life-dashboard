"""The bar Max set on 2026-10-05: 30 minutes mostly in zone 2, so 60, and never moved by the
run-based calibration."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from app.config import load_settings
from app.metrics.calibration import bar_for_week, plan_calibration
from tests.metrics.test_calibration import run


def test_the_shipped_bar_is_sixty_and_calibration_is_off():
    shipped = load_settings()
    assert shipped.workout.load_bar == 60.0
    assert shipped.workout.calibrate is False


def test_with_calibration_off_three_long_runs_leave_the_bar_alone(settings):
    fixed = replace(settings, workout=replace(settings.workout, load_bar=60.0, calibrate=False))
    runs = [run("2026-09-28", 120), run("2026-09-30", 110), run("2026-10-02", 130)]
    assert plan_calibration([], runs, fixed, date(2026, 10, 23)) == []
    assert bar_for_week([], "2026-10-05", fixed.workout.load_bar) == (60.0, "placeholder")
    assert plan_calibration([], runs, settings, date(2026, 10, 23)) != [], "on: it would move"
