from __future__ import annotations

import io
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from app.config import REPO_ROOT, ClaudeUsageConfig, Settings
from app.ingest.claude_usage import read_usage_file
from tests.conftest import count
from tools import claude_usage_hook, replay
from tools.replay import checksum, snapshot

FIXTURES = REPO_ROOT / "fixtures" / "claude_usage"
HOOK = REPO_ROOT / "tools" / "claude_usage_hook.py"


@pytest.fixture
def usage_settings(settings: Settings, tmp_path: Path) -> Settings:
    usage = ClaudeUsageConfig(path=tmp_path / "claude_usage.json", stale_hours=24)
    return replace(settings, claude_usage=usage)


def run_hook(fixture: str, out: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO((FIXTURES / fixture).read_text()))
    assert claude_usage_hook.main(["--out", str(out)]) == 0


def write_usage(path: Path, captured_at_utc: str, rate_limits: dict) -> None:
    path.write_text(json.dumps({"captured_at_utc": captured_at_utc, "rate_limits": rate_limits}))


def day_metrics(db, day: str) -> dict:
    row = db.execute("SELECT metrics_json FROM daily_metrics WHERE day_local = ?", (day,))
    return json.loads(row.fetchone()["metrics_json"])


def test_hook_keeps_only_rate_limits(usage_settings, monkeypatch):
    out = usage_settings.claude_usage.path
    run_hook("statusline_week_41.json", out, monkeypatch)
    record = json.loads(out.read_text())
    feed = json.loads((FIXTURES / "statusline_week_41.json").read_text())
    assert set(record) == {"captured_at_utc", "rate_limits"}
    assert record["rate_limits"] == feed["rate_limits"]


def test_hook_without_rate_limits_keeps_last_reading(usage_settings, monkeypatch):
    out = usage_settings.claude_usage.path
    run_hook("statusline_no_limits.json", out, monkeypatch)
    assert not out.exists()
    run_hook("statusline_week_41.json", out, monkeypatch)
    before = out.read_bytes()
    run_hook("statusline_no_limits.json", out, monkeypatch)
    assert out.read_bytes() == before


def test_hook_as_status_line_command_is_silent_on_garbage(tmp_path):
    out = tmp_path / "usage.json"
    for stdin in ("not json", "[]", ""):
        done = subprocess.run(
            [sys.executable, str(HOOK), "--out", str(out)],
            input=stdin,
            capture_output=True,
            text=True,
        )
        assert (done.returncode, done.stdout, done.stderr) == (0, "", "")
    assert not out.exists()


def test_golden_seven_day_reading_lands_in_daily_metrics(db, usage_settings):
    feed = json.loads((FIXTURES / "statusline_week_41.json").read_text())
    write_usage(usage_settings.claude_usage.path, "2026-10-01T14:03:22Z", feed["rate_limits"])
    result = read_usage_file(db, usage_settings)
    assert result.status == "ok"
    assert day_metrics(db, "2026-10-01") == {
        "claude_week_used_pct": 41.2,
        "claude_week_resets_at": "2026-10-05T16:00:00Z",
        "claude_week_captured_at": "2026-10-01T14:03:22Z",
    }


def test_raw_file_archived_verbatim_and_reread_is_noop(db, usage_settings):
    path = usage_settings.claude_usage.path
    write_usage(path, "2026-10-01T14:03:22Z", {"seven_day": {"used_percentage": 41.2}})
    first = read_usage_file(db, usage_settings)
    row = db.execute("SELECT * FROM raw_archive").fetchone()
    assert row["source"] == "claude_usage"
    assert row["parsed_ok"] == 1
    assert row["path"].startswith("claude_usage/")
    archived = usage_settings.storage.raw_dir / row["path"]
    assert archived.read_bytes() == path.read_bytes()
    before = checksum(snapshot(db))
    second = read_usage_file(db, usage_settings)
    assert (first.status, second.status) == ("ok", "duplicate")
    assert count(db, "raw_archive") == 1
    assert checksum(snapshot(db)) == before
    assert day_metrics(db, "2026-10-01")["claude_week_resets_at"] is None


def test_day_is_new_york_local_and_latest_capture_wins(db, usage_settings):
    path = usage_settings.claude_usage.path
    write_usage(path, "2026-10-02T03:30:00Z", {"seven_day": {"used_percentage": 50}})
    read_usage_file(db, usage_settings)
    write_usage(path, "2026-10-02T01:00:00Z", {"seven_day": {"used_percentage": 44}})
    read_usage_file(db, usage_settings)
    assert count(db, "daily_metrics") == 1
    assert day_metrics(db, "2026-10-01")["claude_week_used_pct"] == 50.0
    write_usage(path, "2026-10-02T03:45:00Z", {"seven_day": {"used_percentage": 51.5}})
    read_usage_file(db, usage_settings)
    assert day_metrics(db, "2026-10-01")["claude_week_used_pct"] == 51.5


def test_reading_merges_into_existing_metrics_row(db, usage_settings):
    db.execute("INSERT INTO daily_metrics VALUES ('2026-10-01', '{\"steps\": 8421}')")
    path = usage_settings.claude_usage.path
    write_usage(path, "2026-10-01T14:03:22Z", {"seven_day": {"used_percentage": 41.2}})
    read_usage_file(db, usage_settings)
    metrics = day_metrics(db, "2026-10-01")
    assert metrics["steps"] == 8421
    assert metrics["claude_week_used_pct"] == 41.2


def test_missing_file_and_missing_window_store_nothing(db, usage_settings):
    assert read_usage_file(db, usage_settings).status == "missing"
    path = usage_settings.claude_usage.path
    write_usage(path, "2026-10-01T14:03:22Z", {"five_hour": {"used_percentage": 73.5}})
    result = read_usage_file(db, usage_settings)
    assert result.status == "no_data"
    assert count(db, "raw_archive") == 1
    assert count(db, "daily_metrics") == 0


def test_malformed_file_is_archived_and_flagged(db, usage_settings):
    usage_settings.claude_usage.path.write_text('{"captured_at_utc": ')
    result = read_usage_file(db, usage_settings)
    assert result.status == "malformed"
    row = db.execute("SELECT parsed_ok, error FROM raw_archive").fetchone()
    assert row["parsed_ok"] == 0
    assert "malformed" in row["error"]
    assert count(db, "daily_metrics") == 0


def test_replay_rebuilds_usage_metrics(db, usage_settings, tmp_path):
    path = usage_settings.claude_usage.path
    write_usage(path, "2026-10-01T14:03:22Z", {"seven_day": {"used_percentage": 41.2}})
    read_usage_file(db, usage_settings)
    write_usage(path, "2026-10-02T15:00:00Z", {"seven_day": {"used_percentage": 47}})
    read_usage_file(db, usage_settings)
    live = snapshot(db)
    assert len(live["daily_metrics"]) == 2
    scratch = replay.replay(usage_settings.storage.raw_dir, tmp_path / "scratch.db", usage_settings)
    try:
        assert checksum(snapshot(scratch)) == checksum(live)
    finally:
        scratch.close()
