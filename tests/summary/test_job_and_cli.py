"""The daily_summary job and tools.summary. The real client is never built: there is no key
in the test environment, and a guard fails the test if one were constructed anyway."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.db import open_db
from app.jobs import scheduler as jobs
from app.jobs.scheduler import SUMMARY_JOB
from app.summary import run
from app.timeutil import local_day, now_utc
from tests.summary.conftest import golden_cases, seed
from tools import summary as summary_tool


@pytest.fixture(autouse=True)
def no_real_client(monkeypatch):
    monkeypatch.setattr(
        run.anthropic, "Anthropic", lambda **kw: pytest.fail("a real Anthropic client was built")
    )


def test_daily_summary_is_a_core_job_at_the_configured_time(jobs_settings):
    assert SUMMARY_JOB in jobs.CORE_JOBS
    scheduler = jobs.build_scheduler(jobs_settings, lambda: open_db(jobs_settings.storage.db_path))
    scheduler.start(paused=True)
    try:
        job = scheduler.get_job(SUMMARY_JOB)
        assert job.max_instances == 1 and job.coalesce
        assert jobs_settings.summary.time == "06:50"
        fire = job.trigger.get_next_fire_time(None, datetime(2026, 10, 2, 12, 0, tzinfo=UTC))
        assert fire == datetime(2026, 10, 3, 6, 50, tzinfo=ZoneInfo("America/New_York"))
        assert job.misfire_grace_time == jobs.SUMMARY_MISFIRE_GRACE_S
    finally:
        scheduler.shutdown(wait=False)


def test_job_writes_todays_line_and_a_second_run_does_not_call(db, jobs_settings):
    today = local_day(now_utc(), jobs_settings.home_tz)
    opened = []

    def open_conn():
        conn = open_db(jobs_settings.storage.db_path)
        opened.append(conn)
        return conn

    assert jobs.run_daily_summary(jobs_settings, open_conn) == "fallback"
    row = db.execute(
        "SELECT metrics_json FROM daily_metrics WHERE day_local = ?", (today,)
    ).fetchone()
    assert json.loads(row["metrics_json"])[run.DEVICE_KEY].strip()
    assert jobs.run_daily_summary(jobs_settings, open_conn) == "stored"
    assert len(opened) == 2


def test_cli_writes_then_reports_stored(db, settings, capsys):
    seed(db, golden_cases()[0])
    assert summary_tool.main(["--date", "2026-10-01"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("2026-10-01 [fallback] Yesterday made the third dot this week, load 118.")
    assert "attempt model: unavailable: no api key" in out
    assert summary_tool.main(["--date", "2026-10-01"]) == 0
    assert "[stored]" in capsys.readouterr().out


def test_cli_dry_run_prints_the_request_and_cap_check_and_writes_nothing(db, settings, capsys):
    seed(db, golden_cases()[0])
    assert summary_tool.main(["--date", "2026-10-01", "--dry-run"]) == 0
    out = capsys.readouterr().out
    body_text, _, tail = out.rpartition("\n}\n")
    body = json.loads(body_text + "\n}")
    assert body["model"] == "claude-opus-5-5"
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "month-to-date $0.0000 + worst case $" in tail
    assert "call allowed" in tail
    assert "api key: absent" in tail
    assert "cell: train / all-sources / alive / base" in tail
    assert "shown on 2026-10-01, describes 2026-09-30" in tail
    assert db.execute("SELECT COUNT(*) FROM summary_lines").fetchone()[0] == 0
    assert run.DEVICE_KEY not in json.loads(
        db.execute(
            "SELECT metrics_json FROM daily_metrics WHERE day_local = '2026-09-30'"
        ).fetchone()[0]
    )


def test_cli_spend_readback(db, settings, capsys):
    assert summary_tool.main(["--spend"]) == 0
    out = capsys.readouterr().out
    assert "0 call(s), month-to-date $0.0000 of cap $3.00, model claude-opus-5-5" in out


def test_cli_refuses_a_db_that_is_behind(settings, capsys, tmp_path):
    from app.db import connect

    conn = connect(settings.storage.db_path)
    conn.execute(
        "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at_utc TEXT NOT NULL, "
        "name TEXT NOT NULL)"
    )
    conn.execute("INSERT INTO schema_version VALUES (1, 'x', 'initial')")
    conn.close()
    assert summary_tool.main(["--spend"]) == 2
    assert "tools.migrate" in capsys.readouterr().err
