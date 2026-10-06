"""A manual override reaches the summary as `day.manual_workout`; the fallback copy for such
a day never claims heart-rate load, and the model's prompt is told what the flag means."""

from __future__ import annotations

from app.summary.fallback import candidates, fact_clause, fallback_line
from app.summary.gate import Recent, check_device_line
from app.summary.prompt import STABLE_SYSTEM_PROMPT
from tests.summary.conftest import payload_for

WEEK = {"quality_workouts": 2, "week_hit": None, "weeks_hit_streak": 3, "load_bar": 100.0}


def manual_case(workout_load: float | None, workout_count: int = 1) -> dict:
    return {
        "cell": "train / all-sources / alive / base",
        "day": "2026-09-30",
        "daily_metrics": {
            "quality_workout": True,
            "workout_count": workout_count,
            "workout_load": workout_load,
            "manual_workout": True,
            "manual_note": "4 mile run",
            "sleep_hours": 7.4,
            "sleep_win": True,
            "steps": 8412,
            "books_ytd": 3,
            "book_finished_today": False,
            "wins": ["sleep", "workout"],
            "load_trimp": 100.0,
            "load_acute": 80.2,
            "load_chronic": 70.5,
            "load_balance": 1.14,
        },
        "weekly_metrics": WEEK,
    }


def test_the_payload_carries_the_flag_but_never_the_note(db, settings):
    payload = payload_for(db, settings, manual_case(None))
    assert payload.data["day"]["manual_workout"] is True
    assert "4 mile" not in payload.text() and "manual_note" not in payload.text()
    assert payload.cell.day_type == "train"
    assert payload.cell.completeness == "all-sources"


def test_fallback_for_a_hand_credited_day_says_so_and_never_speaks_of_load(db, settings):
    for load in (None, 60.0):
        payload = payload_for(db, settings, manual_case(load))
        clause = fact_clause(payload)
        assert "by hand" in clause, clause
        assert "load" not in clause.lower() and "heart" not in clause.lower()
        for line in candidates(payload):
            assert "load" not in line.lower(), line
            assert "heart rate" not in line.lower(), line
        result = fallback_line(payload, Recent(), 0.5)
        assert result.gate.ok and not result.last_resort
        assert check_device_line(result.line, payload, Recent(), 0.5).ok


def test_a_scored_quality_day_with_an_override_keeps_the_load_wording(db, settings):
    payload = payload_for(db, settings, manual_case(118.0))
    clause = fact_clause(payload)
    assert "load 118" in clause and "by hand" not in clause


def test_the_prompt_names_the_flag():
    assert "day.manual_workout" in STABLE_SYSTEM_PROMPT
    assert "logged by hand" in STABLE_SYSTEM_PROMPT
