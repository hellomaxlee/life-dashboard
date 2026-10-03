"""Bartek's QA rulings on the metrics engine (2026-10-02), each with the case that was wrong.

1. Only a push that carried workouts closes a week (a metrics-only push landing first must
   not close it on two dots).
2. Sleep is the sum of the wake day's night fragments (starting 18:00 the evening before to
   12:00 on the wake day); naps outside that window do not count.
3. Load balance needs 28 days of history.
4. A re-calibration check that keeps the bar writes no history row; the check is recorded
   in metrics_state.
5. A push before Monday 00:00 never closes the week.
Plus: a real Health Auto Export run is stored as type "Running", which must calibrate.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app.ingest import health
from app.metrics.calibration import is_calibration_run
from app.metrics.engine import recompute
from app.metrics.load import load_rows
from tests.metrics.golden import apply_inputs, history_rows, metrics_rows
from tests.payloads import post, steps_payload

WEEK = "2026-09-28"
LIFTS = {
    "activities": [
        {
            "id": f"L-{d}",
            "type": "lift",
            "start_local": f"{d} 18:00",
            "minutes": 40,
            "hr": [[40, 140]],
        }
        for d in ("2026-09-28", "2026-09-30")
    ]
}


def hae_workout(id_: str, start_local: str, minutes: int, bpm: int = 140) -> dict:
    start = datetime.strptime(start_local, "%Y-%m-%d %H:%M")

    def stamp(offset: int) -> str:
        return (start + timedelta(minutes=offset)).strftime("%Y-%m-%d %H:%M:00 -0400")

    return {
        "id": id_,
        "name": "Traditional Strength Training",
        "start": stamp(0),
        "end": stamp(minutes),
        "duration": minutes * 60,
        "heartRateData": [
            {"date": stamp(i), "Min": bpm - 5, "Avg": bpm, "Max": bpm + 5, "source": "Apple Watch"}
            for i in range(minutes)
        ],
    }


def push_at(client, monkeypatch, body: bytes, at: datetime) -> None:
    monkeypatch.setattr(health, "now_utc", lambda: at)
    assert post(client, body).json()["status"] == "ok"


def workouts_body(*workouts: dict) -> bytes:
    return json.dumps({"data": {"workouts": list(workouts)}}).encode()


def week(db) -> dict:
    return metrics_rows(db, "weekly_metrics", "week_start_local")[WEEK]


SUNDAY_PUSH = datetime(2026, 10, 4, 22, 0, tzinfo=UTC)
MONDAY_0600 = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
MONDAY_0605 = datetime(2026, 10, 5, 10, 5, tzinfo=UTC)
FIRST_TWO = workouts_body(
    hae_workout("W-0928", "2026-09-28 18:00", 40), hae_workout("W-0930", "2026-09-30 18:00", 40)
)
WITH_SUNDAY = workouts_body(
    hae_workout("W-0928", "2026-09-28 18:00", 40),
    hae_workout("W-0930", "2026-09-30 18:00", 40),
    hae_workout("W-1004", "2026-10-04 21:30", 40),
)


def test_a_metrics_push_landing_before_the_workouts_push_does_not_close_the_week(
    client, db, settings, monkeypatch
):
    push_at(client, monkeypatch, FIRST_TWO, SUNDAY_PUSH)
    push_at(client, monkeypatch, steps_payload({"2026-10-04": 9000}), MONDAY_0600)

    recompute(db, settings, now=MONDAY_0600 + timedelta(minutes=2))
    between = week(db)
    push_at(client, monkeypatch, WITH_SUNDAY, MONDAY_0605)
    recompute(db, settings, now=MONDAY_0605 + timedelta(minutes=2))
    after = week(db)

    assert (between["quality_workouts"], between["week_hit"], between["weeks_hit_streak"]) == (
        2,
        None,
        0,
    )
    assert (after["quality_workouts"], after["week_hit"], after["weeks_hit_streak"]) == (3, True, 1)


def test_the_workouts_push_first_then_the_metrics_push_also_hits(client, db, settings, monkeypatch):
    push_at(client, monkeypatch, FIRST_TWO, SUNDAY_PUSH)
    push_at(client, monkeypatch, WITH_SUNDAY, MONDAY_0600)
    recompute(db, settings, now=MONDAY_0600 + timedelta(minutes=2))
    first = week(db)
    push_at(client, monkeypatch, steps_payload({"2026-10-04": 9000}), MONDAY_0605)
    recompute(db, settings, now=MONDAY_0605 + timedelta(minutes=2))

    assert (first["week_hit"], first["weeks_hit_streak"]) == (True, 1)
    assert (week(db)["week_hit"], week(db)["weeks_hit_streak"]) == (True, 1)


def test_metrics_only_pushes_leave_the_week_open_until_the_grace_runs_out(db, settings):
    apply_inputs(
        db,
        {**LIFTS, "health_pushes_utc": [{"at": "2026-10-05T10:00:00Z", "workouts": 0}]},
        settings,
    )
    recompute(db, settings, now=datetime(2026, 10, 5, 15, 59, tzinfo=UTC))
    assert week(db)["week_hit"] is None
    recompute(db, settings, now=datetime(2026, 10, 5, 16, 0, tzinfo=UTC))
    assert week(db)["week_hit"] is False


def test_a_push_an_hour_before_monday_does_not_close_the_week(db, settings):
    apply_inputs(db, {**LIFTS, "health_pushes_utc": ["2026-10-05T03:00:00Z"]}, settings)
    recompute(db, settings, now=datetime(2026, 10, 5, 5, 0, tzinfo=UTC))
    assert (week(db)["week_hit"], week(db)["weeks_hit_streak"]) == (None, 0)


def test_a_night_split_by_a_long_gap_is_summed_and_a_nap_is_not(db, settings):
    apply_inputs(
        db,
        {
            "sleep": [
                {
                    "wake_day": "2026-10-06",
                    "start_local": "2026-10-05 22:30",
                    "end_local": "2026-10-06 01:30",
                    "asleep_h": 3.0,
                },
                {
                    "wake_day": "2026-10-06",
                    "start_local": "2026-10-06 02:31",
                    "end_local": "2026-10-06 06:31",
                    "asleep_h": 4.0,
                },
                {
                    "wake_day": "2026-10-06",
                    "start_local": "2026-10-06 14:00",
                    "end_local": "2026-10-06 15:00",
                    "asleep_h": 1.0,
                },
                {
                    "wake_day": "2026-10-07",
                    "start_local": "2026-10-06 17:30",
                    "end_local": "2026-10-06 18:30",
                    "asleep_h": 1.0,
                },
                {
                    "wake_day": "2026-10-07",
                    "start_local": "2026-10-06 23:00",
                    "end_local": "2026-10-07 05:30",
                    "asleep_h": 6.5,
                },
            ]
        },
        settings,
    )
    recompute(db, settings, now=datetime(2026, 10, 7, 16, tzinfo=UTC))
    days = metrics_rows(db, "daily_metrics", "day_local")
    assert (days["2026-10-06"]["sleep_hours"], days["2026-10-06"]["sleep_win"]) == (7.0, True)
    assert (days["2026-10-07"]["sleep_hours"], days["2026-10-07"]["sleep_win"]) == (6.5, False)


def test_two_sources_reporting_the_same_night_are_not_added_together(db, settings):
    night = {
        "wake_day": "2026-10-06",
        "start_local": "2026-10-05 23:00",
        "end_local": "2026-10-06 06:30",
    }
    apply_inputs(db, {"sleep": [{**night, "asleep_h": 7.2}]}, settings)
    db.execute(
        "INSERT INTO sleep_sessions (id, wake_day_local, start_utc, end_utc, asleep_s, source) "
        "VALUES ('phone', '2026-10-06', '2026-10-06T03:05:00Z', '2026-10-06T10:30:00Z', 25200, "
        "'iPhone')"
    )
    recompute(db, settings, now=datetime(2026, 10, 6, 16, tzinfo=UTC))
    assert metrics_rows(db, "daily_metrics", "day_local")["2026-10-06"]["sleep_hours"] == 7.2


def test_balance_needs_28_days_of_history():
    rows = load_rows([100.0] * 30)
    assert rows[26].balance is None
    assert rows[27].balance is not None


def test_a_kept_recheck_writes_no_history_row_and_is_recorded(db, settings):
    run = {"type": "run", "minutes": 40, "distance_mi": 4.2, "hr": [[40, 140]]}
    days = ("2026-01-05", "2026-01-07", "2026-01-09", "2026-02-02", "2026-03-02", "2026-04-01")
    apply_inputs(
        db,
        {"activities": [{**run, "id": f"R-{d}", "start_local": f"{d} 07:00"} for d in days]},
        settings,
    )
    recompute(db, settings, "2026-04-20", datetime(2026, 4, 20, 16, tzinfo=UTC))

    assert [r["value"] for r in history_rows(db)] == [120.0]
    state = dict(db.execute("SELECT key, value FROM metrics_state").fetchall())
    assert state["last_calibration_check"] == "2026-04-09 kept 120.0 from 3 run(s)"


def test_a_real_health_auto_export_run_name_counts_for_calibration():
    four_miles = 4 * 1609.344
    for name in ("run", "Running", "Outdoor Run", "Indoor Run"):
        assert is_calibration_run(name, four_miles, 120.0), name
    for name in ("Rowing", "Pruning", "Traditional Strength Training", "Cycling"):
        assert not is_calibration_run(name, four_miles, 120.0), name
