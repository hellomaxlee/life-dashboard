"""QA finding 3: the summary's keys are authored, not derived from raw; `tools.replay --verify`
must stay green after a summary is written and still go red on an edited engine key."""

from __future__ import annotations

import json
from datetime import timedelta

from app.metrics.engine import recompute
from app.summary.run import write_summary
from tests.conftest import post_fixture
from tests.payloads import T0, tick_clock
from tools import replay

AFTER_INGEST = T0 + timedelta(minutes=1, seconds=30)


def verify(tmp_path, capsys) -> tuple[int, str]:
    code = replay.main(["--verify", "--scratch", str(tmp_path / "verify-scratch.db")])
    return code, capsys.readouterr().out


def ingest_and_summarise(client, db, settings, monkeypatch) -> None:
    tick_clock(monkeypatch)
    for name in ("workouts_v2_run.json", "metrics_v2_days.json"):
        assert post_fixture(client, name).status_code == 200
    recompute(db, settings, now=AFTER_INGEST)
    write_summary(db, settings, "2026-09-23", now=AFTER_INGEST)
    write_summary(db, settings, "2026-12-25", now=AFTER_INGEST)


def test_verify_stays_green_after_a_summary(client, db, settings, tmp_path, capsys, monkeypatch):
    ingest_and_summarise(client, db, settings, monkeypatch)
    row = db.execute("SELECT metrics_json FROM daily_metrics WHERE day_local = '2026-09-23'")
    assert "summary_device_line" in json.loads(row.fetchone()[0])

    code, out = verify(tmp_path, capsys)

    assert code == 0, out
    assert "0 difference(s)" in out


def test_verify_still_goes_red_on_an_edited_engine_key_next_to_a_summary(
    client, db, settings, tmp_path, capsys, monkeypatch
):
    ingest_and_summarise(client, db, settings, monkeypatch)
    row = json.loads(
        db.execute(
            "SELECT metrics_json FROM daily_metrics WHERE day_local = '2026-09-22'"
        ).fetchone()[0]
    )
    row["load_trimp"] = (row.get("load_trimp") or 0) + 1
    db.execute(
        "UPDATE daily_metrics SET metrics_json = ? WHERE day_local = '2026-09-22'",
        (json.dumps(row, sort_keys=True),),
    )

    code, out = verify(tmp_path, capsys)

    assert code == 1
    assert "daily_metrics: +" in out
