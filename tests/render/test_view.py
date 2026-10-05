from __future__ import annotations

import json
from dataclasses import replace

from app.render.view import ClaudeUsage, DayView, view_from_metrics, week_start
from app.render.view_db import view_from_db
from app.timeutil import from_utc_iso
from tests.render import WEEK_41, load, rotation


def put_daily(db, day: str, metrics: dict) -> None:
    db.execute(
        "INSERT INTO daily_metrics (day_local, metrics_json) VALUES (?, ?)",
        (day, json.dumps(metrics)),
    )


def test_fixture_builds_the_expected_view(settings):
    view, now = load(WEEK_41, settings)
    assert now == from_utc_iso("2026-09-30T22:15:00Z")
    assert view.month_feature is not None and view.month_feature.month == "2026-09"
    assert view.city is not None and view.city.day_local == "2026-09-30"
    assert replace(view, month_feature=None, city=None) == DayView(
        day_local="2026-09-30",
        home_tz="America/New_York",
        week_target=3,
        week_dots=2,
        streak_weeks=4,
        today_dot=True,
        sleep_hours=7.4,
        sleep_target_hours=7.0,
        steps=8412,
        as_of_utc="2026-09-30T22:10:00Z",
        books_ytd=3,
        books_target=12,
        summary_line="Second dot this week, 7.4 h of sleep. What you repeat is what you become.",
        claude=ClaudeUsage(41.2, "2026-10-03T20:00:00Z", "2026-09-30T21:40:00Z"),
        stale_hours=24,
        city_stale_minutes=45,
    )


def test_wrong_types_become_missing_not_numbers(settings):
    daily = {
        "quality_workout": "yes",
        "sleep_hours": "7.4",
        "steps": True,
        "books_ytd": -1,
        "summary_device_line": "   ",
        "claude_week_used_pct": "41.2",
    }
    weekly = {"quality_workouts": None, "weeks_hit_streak": "4"}
    view = view_from_metrics("2026-09-30", daily, weekly, settings)
    assert view == view_from_metrics("2026-09-30", {}, {}, settings)
    assert view.week_dots is None and view.sleep_hours is None and view.summary_line is None


def test_week_starts_monday():
    assert week_start("2026-09-30") == "2026-09-28"
    assert week_start("2026-09-28") == "2026-09-28"
    assert week_start("2026-10-04") == "2026-09-28"


def test_empty_database_gives_an_all_missing_view_that_still_renders(db, settings):
    view = view_from_db(db, settings, "2026-10-02")
    assert view == view_from_metrics("2026-10-02", {}, {}, settings)
    clips = rotation(view, from_utc_iso("2026-10-02T16:00:00Z"))
    assert all(clip.poster.getbbox() is not None for clip in clips.values())


def test_view_from_db_reads_what_ingest_stores_today(db, settings):
    for sleep_id, asleep, source in (("a", 26640, "Watch"), ("b", 3600, "Phone")):
        db.execute(
            "INSERT INTO sleep_sessions (id, wake_day_local, start_utc, end_utc, asleep_s, source) "
            "VALUES (?, '2026-09-30', '2026-09-30T03:00:00Z', '2026-09-30T10:40:00Z', ?, ?)",
            (sleep_id, asleep, source),
        )
    db.execute("INSERT INTO steps_daily VALUES ('2026-09-30', 8412, 'aggregate')")
    for row_id, source, at, ok in (
        (1, "health", "2026-09-30T16:10:00Z", 1),
        (2, "health", "2026-09-30T22:10:00Z", 1),
        (3, "health", "2026-09-30T23:00:00Z", 0),
        (4, "claude_usage", "2026-09-30T23:30:00Z", 1),
        (5, "health", "2026-10-01T04:10:00Z", 1),
    ):
        db.execute(
            "INSERT INTO raw_archive (id, source, received_at_utc, sha256, path, byte_len, "
            "parsed_ok) VALUES (?, ?, ?, ?, 'x.json', 1, ?)",
            (row_id, source, at, f"sha{row_id}", ok),
        )
    put_daily(
        db,
        "2026-09-29",
        {
            "claude_week_used_pct": 41.2,
            "claude_week_resets_at": "2026-10-03T20:00:00Z",
            "claude_week_captured_at": "2026-09-29T21:40:00Z",
        },
    )
    put_daily(db, "2026-10-01", {"claude_week_used_pct": 99.0})

    view = view_from_db(db, settings, "2026-09-30")
    assert view.sleep_hours == 7.4
    assert view.steps == 8412
    assert view.as_of_utc == "2026-09-30T22:10:00Z"
    assert view.claude == ClaudeUsage(41.2, "2026-10-03T20:00:00Z", "2026-09-29T21:40:00Z")
    assert view.week_dots is None and view.streak_weeks is None
    assert view.today_dot is None and view.books_ytd is None and view.summary_line is None


def test_view_from_db_prefers_phase_2_and_3_keys_when_present(db, settings):
    db.execute(
        "INSERT INTO sleep_sessions (id, wake_day_local, start_utc, end_utc, asleep_s, source) "
        "VALUES ('a', '2026-09-30', '2026-09-30T03:00:00Z', '2026-09-30T10:40:00Z', 26640, 'W')"
    )
    put_daily(
        db,
        "2026-09-30",
        {
            "quality_workout": True,
            "sleep_hours": 7.1,
            "steps": 9000,
            "books_ytd": 3,
            "summary_device_line": "Second dot.",
        },
    )
    db.execute(
        "INSERT INTO weekly_metrics VALUES ('2026-09-28', ?)",
        (json.dumps({"quality_workouts": 2, "weeks_hit_streak": 4}),),
    )
    view = view_from_db(db, settings, "2026-09-30")
    assert (view.week_dots, view.streak_weeks, view.today_dot) == (2, 4, True)
    assert (view.sleep_hours, view.steps, view.books_ytd) == (7.1, 9000, 3)
    assert view.summary_line == "Second dot."
    assert view.claude == ClaudeUsage()
