"""The scheduled recompute, the tools.metrics CLI and the [metrics] boot checks."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import DEFAULT_CONFIG_PATH, BackupConfig, load_settings
from app.db import connect_live
from app.jobs import scheduler as jobs
from app.metrics.job import RECOMPUTE_JOB, ROLLOVER_JOB, run_recompute
from app.timeutil import local_day, now_utc
from tests.metrics.golden import GOLDEN_DIR, apply_inputs, load_golden, metrics_rows
from tools import metrics as metrics_tool


def test_both_metrics_jobs_are_registered_with_their_triggers(settings, tmp_path):
    jobs_settings = replace(settings, backup=BackupConfig(tmp_path / "backups", "03:15", 3))
    moment = datetime(2026, 10, 2, 16, tzinfo=UTC)
    scheduler = jobs.build_scheduler(jobs_settings, lambda: None, now=moment)
    by_id = {job.id: job for job in scheduler.get_jobs()}
    assert {RECOMPUTE_JOB, ROLLOVER_JOB} <= set(by_id)
    interval = by_id[RECOMPUTE_JOB].trigger
    assert isinstance(interval, IntervalTrigger)
    assert interval.interval == timedelta(minutes=jobs_settings.metrics.recompute_minutes)
    assert by_id[RECOMPUTE_JOB].next_run_time == moment + jobs.RECOMPUTE_FIRST_DELAY
    cron = by_id[ROLLOVER_JOB].trigger
    assert isinstance(cron, CronTrigger)
    assert str(cron.fields[5]) == "0" and str(cron.fields[6]) == "5"


def test_run_recompute_opens_its_own_connection_and_writes_rows(db, settings):
    apply_inputs(db, load_golden(GOLDEN_DIR / "train_never_started_off.json")["inputs"], settings)
    opened = []

    def open_conn():
        conn = connect_live(settings.storage.db_path)
        opened.append(conn)
        return conn

    result = run_recompute(settings, open_conn)
    assert result.days_written >= 1 and result.weeks_written >= 1
    assert len(opened) == 1
    with pytest.raises(Exception, match="closed"):
        opened[0].execute("SELECT 1")
    today = local_day(now_utc(), settings.home_tz)
    assert result.today_local == today
    assert today in metrics_rows(db, "daily_metrics", "day_local")


def test_cli_recompute_show_and_history(db, settings, capsys):
    apply_inputs(db, load_golden(GOLDEN_DIR / "train_never_started_off.json")["inputs"], settings)

    assert metrics_tool.main(["--recompute", "--today", "2026-10-13"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"recomputed 2026-10-12\.\.2026-10-13 as of 2026-10-14T03:59:59Z", out)

    assert metrics_tool.main(["--show", "2026-10-13"]) == 0
    out = capsys.readouterr().out
    assert '"workout_load": 104.0' in out and "weekly_metrics 2026-10-12" in out

    assert metrics_tool.main(["--history"]) == 0
    assert "placeholder 100.0" in capsys.readouterr().out


def test_cli_refuses_a_db_behind_the_code(settings, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("LIFE_DB_PATH", str(tmp_path / "missing.db"))
    assert metrics_tool.main(["--recompute"]) == 2
    assert "tools.migrate" in capsys.readouterr().err


BAD = [
    (r"^recompute_minutes = 15", "recompute_minutes = 0", "metrics.recompute_minutes"),
    (r'^rollover_time = "00:05"', 'rollover_time = "5 past"', "metrics.rollover_time"),
    (
        r"^week_close_grace_hours = 12",
        "week_close_grace_hours = -1",
        "metrics.week_close_grace_hours",
    ),
    (r"^max_sample_gap_s = 300", "max_sample_gap_s = 0", "metrics.max_sample_gap_s"),
    (
        r'^wellness_priority = \["hrv_ms", "resting_hr", "daylight_min"\]',
        'wellness_priority = ["hrv_ms", "steps"]',
        "metrics.wellness_priority",
    ),
    (
        r'^wellness_priority = \["hrv_ms", "resting_hr", "daylight_min"\]',
        'wellness_priority = ["hrv_ms", "hrv_ms"]',
        "metrics.wellness_priority",
    ),
]


@pytest.mark.parametrize(("pattern", "replacement", "key"), BAD)
def test_bad_metrics_config_stops_boot_and_names_the_key(
    tmp_path, monkeypatch, pattern, replacement, key
):
    text, n = re.subn(pattern, replacement, DEFAULT_CONFIG_PATH.read_text(), count=1, flags=re.M)
    assert n == 1
    config = tmp_path / "config.toml"
    config.write_text(text)
    monkeypatch.delenv("LIFE_BACKUP_DIR", raising=False)
    with pytest.raises(ValueError, match=re.escape(key)):
        load_settings(config)


def test_a_config_without_a_metrics_section_boots_with_the_defaults(tmp_path, monkeypatch):
    text = re.sub(
        r"^\[metrics\].*?(?=^\[device\])", "", DEFAULT_CONFIG_PATH.read_text(), flags=re.M | re.S
    )
    assert "[metrics]" not in text
    config = tmp_path / "config.toml"
    config.write_text(text)
    monkeypatch.delenv("LIFE_BACKUP_DIR", raising=False)
    loaded = load_settings(config)
    assert loaded.metrics.recompute_minutes == 15
    assert loaded.metrics.wellness_priority == ("hrv_ms", "resting_hr", "daylight_min")
