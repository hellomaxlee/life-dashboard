"""A manual override credits a day's quality workout when the feed cannot (notes.txt
§ Architecture assumptions, Manual workout override). Hand arithmetic: lifts 40 min at 140
bpm are Z3 x3 = 120 >= bar 100 (a dot); a 20-minute lift at 140 is 60 (under the bar).

Week 2026-09-21..27 holds dots on Mon 21 and Wed 23, a 60-load lift on Thu 24, a no-HR
session on Sat 26, and nothing on Fri 25. A Workouts push on Mon 28 10:00Z closes it: two
dots, week_hit false, streak 0. Each case adds one override and reads what changes.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.metrics import manual
from app.metrics.engine import recompute
from app.metrics.keys import DAILY_KEYS
from app.render.view_db import view_from_db
from tests.metrics.golden import apply_inputs, metrics_rows
from tools.replay import snapshot

TODAY = "2026-09-29"
NOW = datetime(2026, 9, 29, 16, tzinfo=UTC)
RECORDED = datetime(2026, 9, 29, 13, 30, tzinfo=UTC)
WEEK = "2026-09-21"


def lift(id_: str, start_local: str, minutes: int) -> dict:
    return {
        "id": id_,
        "type": "lift",
        "start_local": start_local,
        "minutes": minutes,
        "hr": [[minutes, 140]],
    }


INPUTS = {
    "activities": [
        lift("L-21", "2026-09-21 18:00", 40),
        lift("L-23", "2026-09-23 18:00", 40),
        lift("L-24", "2026-09-24 18:00", 20),
        {
            "id": "W-26",
            "type": "walk",
            "start_local": "2026-09-26 09:00",
            "minutes": 30,
            "hr": None,
        },
    ],
    "health_pushes_utc": ["2026-09-28T10:00:00Z"],
}


@pytest.fixture
def seeded(db, settings):
    apply_inputs(db, INPUTS, settings)
    recompute(db, settings, TODAY, NOW)
    return db


def rows(conn) -> tuple[dict[str, dict], dict]:
    return metrics_rows(conn, "daily_metrics", "day_local"), metrics_rows(
        conn, "weekly_metrics", "week_start_local"
    )[WEEK]


def credit(conn, settings, day: str, note: str = "4 mile run") -> None:
    manual.add_override(conn, day, note, RECORDED)
    recompute(conn, settings, TODAY, NOW)


def test_the_baseline_week_has_two_dots_and_missed(seeded):
    daily, week = rows(seeded)
    assert (week["quality_workouts"], week["week_hit"], week["weeks_hit_streak"]) == (2, False, 0)
    assert daily["2026-09-25"]["quality_workout"] is False
    assert daily["2026-09-25"]["manual_workout"] is False
    assert daily["2026-09-25"]["manual_note"] is None
    assert {"manual_workout", "manual_note"} <= DAILY_KEYS


def test_an_override_on_an_empty_day_is_the_third_dot_and_the_streak_follows(seeded, settings):
    credit(seeded, settings, "2026-09-25")
    daily, week = rows(seeded)
    day = daily["2026-09-25"]
    assert day["quality_workout"] is True
    assert day["workout_count"] == 1
    assert day["workout_ids"] == []
    assert day["workout_load"] is None
    assert day["manual_workout"] is True and day["manual_note"] == "4 mile run"
    assert day["wins"] == ["workout"]
    assert day["load_trimp"] == 100.0
    assert (week["quality_workouts"], week["week_hit"], week["weeks_hit_streak"]) == (3, True, 1)
    assert week["week_complete_at"] == "2026-09-29T13:30:00Z"


def test_the_override_day_reads_as_a_done_dot_in_the_renderers_view(seeded, settings):
    credit(seeded, settings, "2026-09-25")
    view = view_from_db(seeded, settings, "2026-09-25")
    assert view.today_dot is True and view.workout_count == 1 and view.workout_load is None
    assert view.week_dots == 3


def test_an_override_on_a_no_hr_day_keeps_the_real_count_and_credits_the_bar(seeded, settings):
    credit(seeded, settings, "2026-09-26", "")
    daily, week = rows(seeded)
    day = daily["2026-09-26"]
    assert day["quality_workout"] is True and day["manual_workout"] is True
    assert day["manual_note"] == ""
    assert day["workout_count"] == 1 and day["workout_ids"] == ["W-26"]
    assert day["workout_load"] is None
    assert day["load_trimp"] == 100.0
    assert week["quality_workouts"] == 3 and week["week_hit"] is True


def test_an_override_on_an_under_bar_day_gives_the_dot_but_no_load_credit(seeded, settings):
    credit(seeded, settings, "2026-09-24")
    daily, week = rows(seeded)
    day = daily["2026-09-24"]
    assert day["quality_workout"] is True and day["manual_workout"] is True
    assert day["workout_load"] == 60.0
    assert day["load_trimp"] == 60.0
    assert week["quality_workouts"] == 3


def test_an_override_on_a_scored_quality_day_changes_nothing_but_the_manual_keys(seeded, settings):
    before_daily, before_week = rows(seeded)
    credit(seeded, settings, "2026-09-21")
    daily, week = rows(seeded)
    day = dict(daily["2026-09-21"])
    assert day.pop("manual_workout") is True and day.pop("manual_note") == "4 mile run"
    before = dict(before_daily["2026-09-21"])
    before.pop("manual_workout"), before.pop("manual_note")
    assert day == before
    assert week == before_week
    assert week["quality_workouts"] == 2


def test_a_real_quality_workout_landing_later_on_an_override_day_is_still_one_dot(seeded, settings):
    credit(seeded, settings, "2026-09-25")
    apply_inputs(
        seeded,
        {"activities": [lift("L-25", "2026-09-25 18:00", 40)]},
        settings,
    )
    recompute(seeded, settings, TODAY, NOW)
    daily, week = rows(seeded)
    day = daily["2026-09-25"]
    assert week["quality_workouts"] == 3
    assert day["workout_count"] == 1 and day["workout_ids"] == ["L-25"]
    assert day["workout_load"] == 120.0 and day["load_trimp"] == 120.0
    assert day["quality_workout"] is True and day["manual_workout"] is True
    assert week["week_complete_at"] == "2026-09-25T22:40:00Z"


def test_adding_again_replaces_the_note_and_removal_reverts_everything(seeded, settings):
    before = snapshot(seeded, derived=True, authored=False)
    credit(seeded, settings, "2026-09-25", "first note")
    credit(seeded, settings, "2026-09-25", "4 mile run")
    assert [row.note for row in manual.list_overrides(seeded)] == ["4 mile run"]
    assert rows(seeded)[1]["quality_workouts"] == 3

    assert manual.remove_override(seeded, "2026-09-25") is True
    assert manual.remove_override(seeded, "2026-09-25") is False
    recompute(seeded, settings, TODAY, NOW)
    assert snapshot(seeded, derived=True, authored=False) == before
    assert rows(seeded)[1]["quality_workouts"] == 2


def test_an_override_before_the_first_stored_day_gets_a_row_and_removal_clears_it(db, settings):
    manual.add_override(db, "2026-09-01", "hike", RECORDED)
    result = recompute(db, settings, TODAY, NOW)
    assert result.first_day == "2026-09-01"
    daily = metrics_rows(db, "daily_metrics", "day_local")
    assert daily["2026-09-01"]["quality_workout"] is True
    weeks = metrics_rows(db, "weekly_metrics", "week_start_local")
    assert weeks["2026-08-31"]["quality_workouts"] == 1

    manual.remove_override(db, "2026-09-01")
    assert recompute(db, settings, TODAY, NOW).first_day == "2026-09-01"
    day = metrics_rows(db, "daily_metrics", "day_local")["2026-09-01"]
    assert day["quality_workout"] is False and day["manual_workout"] is False
    assert metrics_rows(db, "weekly_metrics", "week_start_local")["2026-08-31"] == {
        **weeks["2026-08-31"],
        "quality_workouts": 0,
        "week_hit": False,
        "weeks_hit_streak": 0,
        "week_complete_at": None,
    }
    assert recompute(db, settings, TODAY, NOW).first_day == TODAY


def test_a_future_override_is_ignored_by_the_engine_and_refused_by_parse_day(db, settings):
    manual.add_override(db, "2026-10-03", "planned", RECORDED)
    recompute(db, settings, TODAY, NOW)
    assert "2026-10-03" not in metrics_rows(db, "daily_metrics", "day_local")
    with pytest.raises(ValueError, match="after today"):
        manual.parse_day("2026-10-03", settings, NOW)
    with pytest.raises(ValueError, match="not a YYYY-MM-DD"):
        manual.parse_day("29/09/2026", settings, NOW)
    assert manual.parse_day(" 2026-09-29 ", settings, NOW) == "2026-09-29"
    assert manual.read_overrides(db) == {
        date(2026, 10, 3): manual.Override("2026-10-03", "planned", "2026-09-29T13:30:00Z")
    }
