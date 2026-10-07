"""`tools.workout`: add, list and remove a manual override on the live db, recomputing the
metrics rows each time and printing the day's and its week's rows."""

from __future__ import annotations

import json
from datetime import date, timedelta

from app.metrics import manual
from app.metrics.calendar import week_start
from app.timeutil import local_day, now_utc
from tests.metrics.golden import metrics_rows
from tools import workout


def today(settings) -> str:
    return local_day(now_utc(), settings.home_tz)


def printed_rows(out: str) -> tuple[dict, dict]:
    daily = weekly = None
    for line in out.splitlines():
        if line.startswith("daily_metrics "):
            daily = json.loads(line.split(": ", 1)[1])
        elif line.startswith("weekly_metrics "):
            weekly = json.loads(line.split(": ", 1)[1])
    assert daily is not None and weekly is not None, out
    return daily, weekly


def test_add_defaults_to_today_recomputes_and_prints_both_rows(db, settings, capsys):
    assert workout.main(["--note", "4 mile run"]) == 0
    out = capsys.readouterr().out
    day = today(settings)
    assert f"credited {day} with a quality workout by hand: 4 mile run" in out
    daily, weekly = printed_rows(out)
    assert daily["quality_workout"] is True and daily["manual_workout"] is True
    assert daily["manual_note"] == "4 mile run" and daily["workout_count"] == 1
    assert weekly["quality_workouts"] == 1
    assert metrics_rows(db, "daily_metrics", "day_local")[day]["manual_workout"] is True
    monday = week_start(date.fromisoformat(day)).isoformat()
    assert f"weekly_metrics {monday}:" in out


def test_add_with_a_date_then_list_then_remove_reverts(db, settings, capsys):
    yesterday = (date.fromisoformat(today(settings)) - timedelta(days=1)).isoformat()
    assert workout.main(["--date", yesterday, "--note", "  long   hike  "]) == 0
    capsys.readouterr()

    assert workout.main(["--list"]) == 0
    listed = capsys.readouterr().out
    assert listed.startswith(f"{yesterday}  long hike  recorded ")

    assert workout.main(["--date", yesterday, "--remove"]) == 0
    out = capsys.readouterr().out
    assert f"removed the override for {yesterday}" in out
    daily, weekly = printed_rows(out)
    assert daily["quality_workout"] is False and daily["manual_workout"] is False
    assert weekly["quality_workouts"] == 0
    assert manual.list_overrides(db) == []

    assert workout.main(["--list"]) == 0
    assert capsys.readouterr().out.strip() == "no manual workout overrides"


def test_refusals(db, settings, capsys):
    tomorrow = (date.fromisoformat(today(settings)) + timedelta(days=1)).isoformat()
    assert workout.main(["--date", tomorrow, "--note", "planned"]) == 2
    assert "after today" in capsys.readouterr().err
    assert workout.main(["--date", "yesterday", "--note", "x"]) == 2
    assert "not a YYYY-MM-DD" in capsys.readouterr().err
    assert workout.main(["--remove"]) == 1
    assert "no override to remove" in capsys.readouterr().err
    assert manual.list_overrides(db) == []


def test_list_shows_judged_days_with_verdict_and_reason(db, settings, capsys):
    from app.metrics import judge

    judge.store(
        db,
        judge.Judgement(
            "2026-10-06", "yes", 0.8, "max 160 with 9000 steps", "fake", "h", "2026-10-06T15:00:00Z"
        ),
    )
    judge.store(
        db,
        judge.Judgement(
            "2026-10-05",
            "failed",
            0.0,
            "",
            "fake",
            "h2",
            "2026-10-06T15:00:00Z",
            "stop_reason max_tokens",
        ),
    )
    db.commit()
    assert workout.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "no manual workout overrides" in out
    assert "2026-10-06  judged yes (0.80)  max 160 with 9000 steps" in out
    assert "2026-10-05  judged failed (0.00)  stop_reason max_tokens" in out


def test_clean_reasons_rewrites_stored_tails_and_prints_the_count(db, settings, capsys):
    from app.metrics import judge

    judge.store(
        db,
        judge.Judgement("2026-10-06", "yes", 0.8, "a session.}", "m", "h", "2026-10-06T15:00:00Z"),
    )
    judge.store(
        db,
        judge.Judgement("2026-10-05", "no", 0.2, "no effort.", "m", "h2", "2026-10-06T15:00:00Z"),
    )
    db.commit()
    assert workout.main(["--clean-reasons"]) == 0
    assert capsys.readouterr().out.strip() == "cleaned 1 judged reason(s)"
    rows = {j.day_local: j for j in judge.list_judgements(db)}
    assert rows["2026-10-06"].reason == "a session." and rows["2026-10-06"].inputs_hash == "h"
    assert rows["2026-10-05"].reason == "no effort."
    assert workout.main(["--clean-reasons"]) == 0
    assert capsys.readouterr().out.strip() == "cleaned 0 judged reason(s)"
