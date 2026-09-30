from __future__ import annotations

import hashlib

from tests.conftest import count, fixture_bytes, post_fixture


def test_raw_bytes_written_verbatim(client, db, settings):
    body = fixture_bytes("workouts_v2_run.json")
    resp = post_fixture(client, "workouts_v2_run.json")
    assert resp.status_code == 200, resp.text
    row = db.execute("SELECT * FROM raw_archive").fetchone()
    stored = (settings.storage.raw_dir / "health" / resp.json()["file"]).read_bytes()
    assert stored == body
    assert row["sha256"] == hashlib.sha256(body).hexdigest()
    assert row["byte_len"] == len(body)
    assert row["parsed_ok"] == 1
    assert row["path"].endswith(resp.json()["file"])


def test_malformed_is_archived_and_422(client, db, settings):
    resp = post_fixture(client, "malformed.json")
    assert resp.status_code == 422
    row = db.execute("SELECT * FROM raw_archive").fetchone()
    assert row["parsed_ok"] == 0
    assert "malformed" in row["error"]
    files = list((settings.storage.raw_dir / "health").glob("*.json"))
    assert len(files) == 1
    assert files[0].read_bytes() == fixture_bytes("malformed.json")
    assert count(db, "activities") == 0


def test_duplicate_sha_short_circuits(client, db, settings):
    first = post_fixture(client, "workouts_v2_run.json")
    second = post_fixture(client, "workouts_v2_run.json")
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == {
        "status": "duplicate",
        "raw_archive_id": first.json()["raw_archive_id"],
    }
    assert count(db, "raw_archive") == 1
    assert count(db, "ingest_log") == 1
    assert len(list((settings.storage.raw_dir / "health").glob("*.json"))) == 1


def test_token_required_when_configured(settings, monkeypatch):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from app.main import create_app

    guarded = replace(settings, health_export_token="s3cret")
    with TestClient(create_app(guarded)) as client:
        assert post_fixture(client, "workouts_v2_run.json").status_code == 401
        ok = post_fixture(client, "workouts_v2_run.json", authorization="Bearer s3cret")
        assert ok.status_code == 200
        dup = post_fixture(client, "workouts_v2_run.json", **{"x-api-key": "s3cret"})
        assert dup.json()["status"] == "duplicate"
