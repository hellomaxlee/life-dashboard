"""Kill-mid-sync drill. Red before the parsed_ok fix in app/ingest/health.py::archive_raw:
a re-post of bytes whose first parse never finished answered `duplicate` and stored nothing."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime

import pytest

from app.ingest import claude_usage
from app.ingest.health import archive_raw
from tests.conftest import count, fixture_bytes, post_fixture
from tools import drill
from tools.claude_usage_hook import usage_record
from tools.replay import checksum, snapshot


@pytest.mark.parametrize("stage", drill.STAGES)
def test_sigkill_at_stage_loses_nothing(stage, tmp_path):
    result = drill.run_stage(stage, drill.DEFAULT_PAYLOAD, tmp_path)
    assert result.failures == []
    assert result.integrity == "ok"
    assert result.raw_on_disk
    assert result.final_checksum == result.clean_checksum
    expected_flags = {"before_row": [], "after_commit": [1]}.get(stage, [0])
    assert result.parsed_ok_after_kill == expected_flags


def test_drill_cli_reports_and_exits_zero(capsys):
    assert drill.main(["--stage", "mid_transaction"]) == 0
    assert "mid_transaction  PASS" in capsys.readouterr().out


def test_drill_goes_red_when_the_raw_file_is_gone(tmp_path, monkeypatch):
    real_kill = drill.kill_at_stage

    def kill_then_delete_raw(stage, payload, workdir):
        real_kill(stage, payload, workdir)
        shutil.rmtree(workdir / "raw")

    monkeypatch.setattr(drill, "kill_at_stage", kill_then_delete_raw)
    result = drill.run_stage("after_commit", drill.DEFAULT_PAYLOAD, tmp_path)
    assert "raw payload is not on disk verbatim" in result.failures


def test_repost_of_archived_but_unparsed_payload_is_parsed(client, db, settings):
    body = fixture_bytes("workouts_v2_run.json")
    archived = archive_raw(db, settings.storage.raw_dir, body)
    assert not archived.duplicate
    assert db.execute("SELECT parsed_ok FROM raw_archive").fetchone()[0] == 0

    resp = post_fixture(client, "workouts_v2_run.json")

    assert resp.json()["status"] == "ok"
    assert resp.json()["raw_archive_id"] == archived.raw_archive_id
    assert count(db, "raw_archive") == 1
    assert db.execute("SELECT parsed_ok FROM raw_archive").fetchone()[0] == 1
    assert count(db, "activities") == 1
    assert len(list((settings.storage.raw_dir / "health").glob("*.json"))) == 1
    assert post_fixture(client, "workouts_v2_run.json").json()["status"] == "duplicate"


def test_repost_of_unparsed_payload_matches_clean_run(client, db, settings, tmp_path):
    archive_raw(db, settings.storage.raw_dir, fixture_bytes("workouts_v2_overlap.json"))
    post_fixture(client, "workouts_v2_overlap.json")
    clean = drill.clean_run_checksum(fixture_bytes("workouts_v2_overlap.json"), tmp_path / "clean")
    assert checksum(snapshot(db)) == clean


def test_unparsed_usage_file_is_read_again(db, jobs_settings):
    feed = json.loads(
        (drill.REPO_ROOT / "fixtures" / "claude_usage" / "statusline_week_41.json").read_text()
    )
    record = usage_record(feed, datetime(2026, 10, 1, 14, 0, tzinfo=UTC))
    jobs_settings.claude_usage.path.write_text(json.dumps(record))
    body = jobs_settings.claude_usage.path.read_bytes()
    archive_raw(db, jobs_settings.storage.raw_dir, body, source=claude_usage.SOURCE)

    result = claude_usage.read_usage_file(db, jobs_settings)

    assert result.status == "ok"
    assert db.execute("SELECT parsed_ok FROM raw_archive").fetchone()[0] == 1
    assert count(db, "daily_metrics") == 1
    assert claude_usage.read_usage_file(db, jobs_settings).status == "duplicate"
