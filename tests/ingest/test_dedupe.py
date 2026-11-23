from __future__ import annotations

import json

import pytest

from tests.conftest import count, fixture_json, post_fixture

WATCH_ID = "A1B2C3D4-0001-4000-8000-000000000001"
PHONE_ID = "PHONE-APP-0001-4000-8000-000000000002"


def test_watch_and_phone_copies_are_one_activity(client, db):
    resp = post_fixture(client, "workouts_v2_overlap.json")
    assert resp.status_code == 200, resp.text
    assert resp.json()["workouts_seen"] == 2
    assert resp.json()["workouts_merged"] == 1
    assert count(db, "activities") == 1
    activity = db.execute("SELECT * FROM activities").fetchone()
    assert activity["id"] == WATCH_ID
    sources = db.execute(
        "SELECT source_app, external_id FROM activity_sources ORDER BY source_app"
    ).fetchall()
    assert [(s["source_app"], s["external_id"]) for s in sources] == [
        ("Apple Watch", WATCH_ID),
        ("Nike Run Club", PHONE_ID),
    ]
    assert activity["hr_sample_count"] == 20
    assert activity["hr_incomplete"] == 0
    assert activity["avg_hr"] is not None
    assert activity["distance_m"] == pytest.approx(4.21 * 1609.344)
    merged = json.loads(activity["merged_from_json"])
    assert {m["source_app"] for m in merged} == {"Apple Watch", "Nike Run Club"}
    assert merged[0]["external_id"] == WATCH_ID


def test_phone_copy_arriving_first_still_yields_one_activity(client, db):
    payload = fixture_json("workouts_v2_overlap.json")
    payload["data"]["workouts"].reverse()
    watch, phone = payload["data"]["workouts"][1], payload["data"]["workouts"][0]
    client.post("/ingest/health", content=json.dumps({"data": {"workouts": [phone]}}).encode())
    client.post("/ingest/health", content=json.dumps({"data": {"workouts": [watch]}}).encode())
    assert count(db, "activities") == 1
    activity = db.execute("SELECT * FROM activities").fetchone()
    assert activity["id"] == WATCH_ID
    assert activity["hr_sample_count"] == 20
    assert activity["distance_m"] == pytest.approx(4.21 * 1609.344)
    assert count(db, "activity_sources") == 2


def test_far_apart_workouts_stay_separate(client, db):
    post_fixture(client, "batch_part1.json")
    assert count(db, "activities") == 2
    assert count(db, "activity_sources") == 2


def test_mutant_dedupe_disabled_yields_two_activities(client, db, monkeypatch):
    monkeypatch.setenv("DEDUPE_DISABLED", "1")
    post_fixture(client, "workouts_v2_overlap.json")
    assert count(db, "activities") == 2
