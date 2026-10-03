from __future__ import annotations

from tests.conftest import count, post_fixture


def test_sleep_belongs_to_wake_day(client, db):
    resp = post_fixture(client, "metrics_v2_days.json")
    assert resp.status_code == 200, resp.text
    s = db.execute("SELECT * FROM sleep_sessions").fetchone()
    assert s["wake_day_local"] == "2026-09-23"
    assert s["start_utc"] == "2026-09-23T03:10:00Z"
    assert s["end_utc"] == "2026-09-23T10:40:00Z"
    assert s["asleep_s"] == round(7.15 * 3600)
    assert s["in_bed_s"] == round(7.5 * 3600)
    assert (s["core_s"], s["deep_s"], s["rem_s"], s["awake_s"]) == (14400, 4140, 7200, 1260)
    assert s["source"] == "Apple Watch"


def test_watch_summary_row_takes_total_sleep_when_asleep_is_zero(client, db):
    """The real Summarize Data row from an Apple Watch: `asleep` (unspecified stage) and
    `inBed` are 0, the night is in `totalSleep` and the stages."""
    resp = post_fixture(client, "metrics_v2_sleep_watch_summary.json")
    assert resp.status_code == 200, resp.text
    s = db.execute("SELECT * FROM sleep_sessions").fetchone()
    assert s["wake_day_local"] == "2026-10-01"
    assert s["asleep_s"] == round(8.7 * 3600)
    assert (s["core_s"], s["deep_s"], s["rem_s"]) == (18180, 5400, 7740)


def test_dst_fall_back_and_late_nap(client, db):
    resp = post_fixture(client, "metrics_v2_sleep_dst.json")
    assert resp.status_code == 200, resp.text
    rows = db.execute("SELECT * FROM sleep_sessions ORDER BY end_utc").fetchall()
    assert [r["wake_day_local"] for r in rows] == ["2026-10-30", "2026-11-01"]
    nap, night = rows
    assert nap["end_utc"] == "2026-10-31T03:30:00Z"
    assert night["start_utc"] == "2026-11-01T03:20:00Z"
    assert night["end_utc"] == "2026-11-01T12:26:00Z"
    assert night["source"] == "aggregate"


def test_daily_metrics_land_on_their_days(client, db):
    resp = post_fixture(client, "metrics_v2_days.json")
    assert resp.json()["unknown_metrics"] == ["mindful_minutes"]
    steps = db.execute("SELECT day_local, steps FROM steps_daily ORDER BY day_local").fetchall()
    assert [(r["day_local"], r["steps"]) for r in steps] == [
        ("2026-09-21", 8421),
        ("2026-09-22", 11230),
        ("2026-09-23", 6712),
    ]
    metrics = db.execute("SELECT DISTINCT metric FROM wellness_daily ORDER BY metric").fetchall()
    assert [m["metric"] for m in metrics] == [
        "heart_rate_variability",
        "resting_heart_rate",
        "time_in_daylight",
        "vo2_max",
    ]
    assert count(db, "wellness_daily") == 12
    hrv = db.execute(
        "SELECT value, units FROM wellness_daily WHERE metric = 'heart_rate_variability' "
        "AND day_local = '2026-09-23'"
    ).fetchone()
    assert (hrv["value"], hrv["units"]) == (39.9, "ms")
    log = db.execute("SELECT unknown_metrics, metrics_rows FROM ingest_log").fetchone()
    assert log["unknown_metrics"] == '["mindful_minutes"]'
    assert log["metrics_rows"] == 16


def test_display_names_map_to_snake_case():
    from app.ingest.parse import normalize_metric_name

    assert normalize_metric_name("Step Count") == "step_count"
    assert normalize_metric_name("Heart Rate Variability") == "heart_rate_variability"
    assert normalize_metric_name("Resting Heart Rate") == "resting_heart_rate"
    assert normalize_metric_name("VO2 Max") == "vo2_max"
    assert normalize_metric_name("Time in Daylight") == "time_in_daylight"
    assert normalize_metric_name("Sleep Analysis") == "sleep_analysis"
