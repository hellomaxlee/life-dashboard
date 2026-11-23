from __future__ import annotations

from tests.conftest import post_fixture


def _activity(db):
    return db.execute("SELECT * FROM activities").fetchone()


def test_full_span_is_complete(client, db):
    post_fixture(client, "workouts_v2_run.json")
    a = _activity(db)
    assert a["hr_sample_count"] == 20
    assert a["hr_span_s"] == 38 * 60
    assert a["hr_incomplete"] == 0
    assert a["duration_s"] == 2400


def test_recovery_only_series_is_flagged(client, db):
    post_fixture(client, "workouts_v2_recovery_only.json")
    a = _activity(db)
    assert a["hr_sample_count"] == 13
    assert a["hr_span_s"] == 120
    assert a["hr_incomplete"] == 1
    assert a["hr_span_s"] < 0.25 * a["duration_s"]


def test_no_samples_is_flagged(client, db):
    post_fixture(client, "workouts_v2_no_hr.json")
    a = _activity(db)
    assert a["hr_sample_count"] == 0
    assert a["hr_span_s"] == 0
    assert a["hr_incomplete"] == 1
    assert a["avg_hr"] is None
