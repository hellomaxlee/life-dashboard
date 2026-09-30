from __future__ import annotations

import json

from tests.conftest import count, fixture_json, post_fixture
from tools.replay import DATA_TABLES, checksum, snapshot


def _counts(db) -> dict[str, int]:
    return {t: count(db, t) for t in DATA_TABLES}


def test_replaying_same_payload_changes_nothing(client, db):
    for name in ("workouts_v2_overlap.json", "metrics_v2_days.json", "batch_part1.json"):
        assert post_fixture(client, name).status_code == 200
    before_counts, before_sum = _counts(db), checksum(snapshot(db))
    assert before_counts["activities"] > 0 and before_counts["sleep_sessions"] > 0
    for name in ("workouts_v2_overlap.json", "metrics_v2_days.json", "batch_part1.json"):
        assert post_fixture(client, name).json()["status"] == "duplicate"
    assert _counts(db) == before_counts
    assert checksum(snapshot(db)) == before_sum


def test_reposting_same_content_with_different_bytes_changes_nothing(client, db):
    payload = fixture_json("workouts_v2_overlap.json")
    client.post("/ingest/health", content=json.dumps(payload).encode())
    before = checksum(snapshot(db))
    resp = client.post("/ingest/health", content=json.dumps(payload, indent=1).encode())
    assert resp.json()["status"] == "ok"
    assert count(db, "raw_archive") == 2
    assert checksum(snapshot(db)) == before


def test_batch_parts_compose_to_single_post(client, db, settings):
    from fastapi.testclient import TestClient

    from app.db import open_db
    from app.main import create_app

    post_fixture(client, "batch_part1.json")
    post_fixture(client, "batch_part2.json")
    split_sum = checksum(snapshot(db))
    assert count(db, "activities") == 4

    whole = fixture_json("batch_part1.json")
    whole["data"]["workouts"] += fixture_json("batch_part2.json")["data"]["workouts"]
    from dataclasses import replace

    from app.config import StorageConfig

    other = replace(
        settings,
        storage=StorageConfig(
            db_path=settings.storage.db_path.parent / "single.db",
            raw_dir=settings.storage.raw_dir.parent / "raw_single",
        ),
    )
    with TestClient(create_app(other)) as single:
        assert single.post("/ingest/health", content=json.dumps(whole).encode()).status_code == 200
    conn = open_db(other.storage.db_path)
    try:
        assert checksum(snapshot(conn)) == split_sum
    finally:
        conn.close()
