from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import CODE_SCHEMA_VERSION, open_db
from app.jobs.scheduler import CORE_JOBS
from app.main import create_app
from tests.conftest import fixture_bytes, post_fixture
from tools import drill

FIXTURES = ("workouts_v2_overlap.json", "metrics_v2_days.json", "metrics_v2_sleep_dst.json")


def with_scheduler(settings):
    return replace(settings, scheduler=replace(settings.scheduler, enabled=True))


def assert_scheduler_is_back(app) -> None:
    assert app.state.scheduler.running
    assert {job.id for job in app.state.scheduler.get_jobs()} == CORE_JOBS


def test_clean_restart_keeps_data_and_brings_jobs_back(jobs_settings):
    settings = with_scheduler(jobs_settings)
    first = create_app(settings)
    with TestClient(first) as client:
        assert_scheduler_is_back(first)
        for name in FIXTURES:
            assert post_fixture(client, name).json()["status"] == "ok"
    before = drill.data_checksum(settings)

    second = create_app(settings)
    with TestClient(second) as client:
        assert_scheduler_is_back(second)
        assert second.state.scheduler is not first.state.scheduler
        assert client.get("/healthz").status_code == 200
        assert drill.data_checksum(settings) == before
        for name in FIXTURES:
            assert post_fixture(client, name).json()["status"] == "duplicate"
    assert drill.data_checksum(settings) == before


def test_kill_9_after_a_push_restarts_onto_the_wal_with_nothing_lost(tmp_path):
    payload = drill.DEFAULT_PAYLOAD
    clean = drill.clean_run_checksum(payload.read_bytes(), tmp_path / "clean")
    drill.kill_at_stage("after_commit", payload, tmp_path / "killed")
    settings = with_scheduler(drill.drill_settings(tmp_path / "killed"))
    wal = Path(str(settings.storage.db_path) + "-wal")
    assert wal.stat().st_size > 0

    app = create_app(settings)
    with TestClient(app) as client:
        assert_scheduler_is_back(app)
        assert client.get("/healthz").status_code == 200
        assert drill.data_checksum(settings) == clean
        resp = client.post("/ingest/health", content=payload.read_bytes())
        assert resp.json()["status"] == "duplicate"
        more = client.post("/ingest/health", content=fixture_bytes("metrics_v2_days.json"))
        assert more.json()["status"] == "ok"
    assert drill._parsed_flags(settings) == ("ok", [1, 1])


def test_a_db_ahead_of_the_code_exits_non_zero_instead_of_serving_503s(settings, caplog):
    conn = open_db(settings.storage.db_path)
    conn.execute(
        "INSERT INTO schema_version (version, applied_at_utc, name) VALUES (?, ?, ?)",
        (CODE_SCHEMA_VERSION + 1, "2026-10-07T00:00:00Z", "from_newer_code"),
    )
    conn.commit()
    conn.close()
    with pytest.raises(SystemExit) as exited:
        create_app(settings)
    assert exited.value.code != 0
    assert f"ahead of this code's {CODE_SCHEMA_VERSION}" in caplog.text
