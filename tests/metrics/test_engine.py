"""The engine's db behaviour: determinism, key merging, rollover rows, the renderer's
view reading what it writes, and day attribution."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.db import open_db
from app.metrics.engine import last_run, recompute
from app.metrics.keys import DAILY_KEYS, WEEKLY_KEYS
from app.render.view_db import view_from_db
from tests.metrics.golden import GOLDEN_DIR, apply_inputs, load_golden, metrics_rows, run_golden
from tools.replay import snapshot

THIRD_DOT = GOLDEN_DIR / "train_all_alive_base_third_dot.json"
CALIBRATION = GOLDEN_DIR / "calibration_mid_history.json"


def derived(conn) -> dict:
    return snapshot(conn, derived=True)


def test_recompute_twice_changes_nothing(db, settings):
    case = load_golden(THIRD_DOT)
    assert run_golden(db, settings, case) == []
    first = derived(db)
    result = recompute(db, settings, case["today_local"], datetime(2026, 10, 2, 16, tzinfo=UTC))
    assert derived(db) == first
    assert (result.days_written, result.weeks_written, result.calibrations_added) == (0, 0, 0)


def test_a_fresh_db_recomputed_under_the_same_clock_matches_live(db, settings, tmp_path):
    case = load_golden(CALIBRATION)
    assert run_golden(db, settings, case) == []
    scratch = open_db(tmp_path / "scratch.db")
    try:
        apply_inputs(scratch, case["inputs"], settings)
        for stage in case["stages"]:
            if stage.get("add"):
                apply_inputs(scratch, stage["add"], settings)
        last = case["stages"][-1]
        recompute(scratch, settings, last["today_local"], datetime.fromisoformat(last["now_utc"]))
        assert derived(scratch) == derived(db)
    finally:
        scratch.close()


def test_other_modules_keys_survive_a_recompute(db, settings):
    db.execute(
        "INSERT INTO daily_metrics (day_local, metrics_json) VALUES (?, ?)",
        ("2026-10-02", json.dumps({"claude_week_used_pct": 41.2, "summary_device_line": "hi"})),
    )
    db.execute(
        "INSERT INTO weekly_metrics (week_start_local, metrics_json) VALUES (?, ?)",
        ("2026-09-28", json.dumps({"somebody_elses": 1})),
    )
    assert run_golden(db, settings, load_golden(THIRD_DOT)) == []
    day = metrics_rows(db, "daily_metrics", "day_local")["2026-10-02"]
    week = metrics_rows(db, "weekly_metrics", "week_start_local")["2026-09-28"]
    assert day["claude_week_used_pct"] == 41.2 and day["summary_device_line"] == "hi"
    assert week["somebody_elses"] == 1
    assert DAILY_KEYS <= set(day) and WEEKLY_KEYS <= set(week)


def test_an_empty_db_still_gets_todays_and_this_weeks_rows(db, settings):
    result = recompute(db, settings, "2026-10-07", datetime(2026, 10, 7, 16, tzinfo=UTC))
    assert (result.first_day, result.days_written, result.weeks_written) == ("2026-10-07", 1, 1)
    day = metrics_rows(db, "daily_metrics", "day_local")["2026-10-07"]
    week = metrics_rows(db, "weekly_metrics", "week_start_local")["2026-10-05"]
    assert day["books_ytd"] == 0 and day["quality_workout"] is False and day["wins"] == []
    assert day["load_trimp"] == 0.0 and day["load_balance"] is None
    assert week == {
        "quality_workouts": 0,
        "week_hit": None,
        "weeks_hit_streak": 0,
        "week_complete_at": None,
        "load_bar": 100.0,
        "load_bar_source": "placeholder",
    }
    assert last_run(db) == ("2026-10-07", "2026-10-07T16:00:00Z")


def test_rollover_writes_the_new_day_and_week_rows_the_renderer_looks_up(db, settings):
    case = load_golden(THIRD_DOT)
    assert run_golden(db, settings, case) == []
    recompute(db, settings, "2026-10-05", datetime(2026, 10, 5, 4, 5, tzinfo=UTC))
    view = view_from_db(db, settings, "2026-10-05")
    assert view.books_ytd == 1
    assert view.week_dots == 0
    assert view.streak_weeks == 2
    assert view.today_dot is False
    assert view.sleep_hours is None


def test_the_renderer_reads_the_engines_values(db, settings):
    assert run_golden(db, settings, load_golden(THIRD_DOT)) == []
    view = view_from_db(db, settings, "2026-10-02")
    assert (view.today_dot, view.sleep_hours, view.steps) == (True, 7.1, 8200)
    assert (view.week_dots, view.streak_weeks, view.books_ytd) == (3, 2, 1)


def test_a_workout_belongs_to_the_local_day_and_week_of_its_start(db, settings):
    apply_inputs(
        db,
        {
            "activities": [
                {
                    "id": "late",
                    "type": "lift",
                    "start_local": "2026-10-04 23:30",
                    "minutes": 60,
                    "hr": [[60, 140]],
                }
            ],
            "health_pushes_utc": ["2026-10-05T10:00:00Z"],
        },
        settings,
    )
    recompute(db, settings, "2026-10-06", datetime(2026, 10, 6, 16, tzinfo=UTC))
    days = metrics_rows(db, "daily_metrics", "day_local")
    weeks = metrics_rows(db, "weekly_metrics", "week_start_local")
    assert days["2026-10-04"]["quality_workout"] is True
    assert days["2026-10-05"]["workout_count"] == 0
    assert weeks["2026-09-28"]["quality_workouts"] == 1
    assert weeks["2026-10-05"]["quality_workouts"] == 0


def test_a_future_workout_is_not_counted_before_its_day(db, settings):
    apply_inputs(
        db,
        {
            "activities": [
                {
                    "id": "tomorrow",
                    "type": "lift",
                    "start_local": "2026-10-07 07:00",
                    "minutes": 40,
                    "hr": [[40, 140]],
                }
            ]
        },
        settings,
    )
    recompute(db, settings, "2026-10-06", datetime(2026, 10, 6, 16, tzinfo=UTC))
    weeks = metrics_rows(db, "weekly_metrics", "week_start_local")
    assert weeks["2026-10-05"]["quality_workouts"] == 0
    assert "2026-10-07" not in metrics_rows(db, "daily_metrics", "day_local")


def test_a_failed_recompute_writes_nothing(db, settings, monkeypatch):
    assert run_golden(db, settings, load_golden(THIRD_DOT)) == []
    before = derived(db)
    monkeypatch.setattr("app.metrics.engine.compute_rows", lambda *a: 1 / 0)
    try:
        recompute(db, settings, "2026-10-09", datetime(2026, 10, 9, 16, tzinfo=UTC))
    except ZeroDivisionError:
        pass
    assert not db.in_transaction
    assert derived(db) == before
    assert last_run(db) == ("2026-10-02", "2026-10-02T16:00:00Z")
