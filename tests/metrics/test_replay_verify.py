"""`tools.replay --verify` and the derived rows: compared under live's last clock, red when
live's metrics differ from a from-scratch recompute, left out with a note when they cannot
be reproduced."""

from __future__ import annotations

import json
from datetime import timedelta

from app.metrics.engine import recompute
from tests.conftest import post_fixture
from tests.metrics.golden import metrics_rows
from tests.payloads import T0, post, steps_payload, tick_clock
from tools import replay

FIXTURES = ("workouts_v2_run.json", "metrics_v2_days.json")
AFTER_INGEST = T0 + timedelta(minutes=1, seconds=30)


def verify_cli(tmp_path, capsys) -> tuple[int, str]:
    code = replay.main(["--verify", "--scratch", str(tmp_path / "verify-scratch.db")])
    return code, capsys.readouterr().out


def ingest(client, monkeypatch):
    """Two pushes archived at T0 and T0 + 1 min."""
    tick_clock(monkeypatch)
    for name in FIXTURES:
        assert post_fixture(client, name).status_code == 200


def test_verify_compares_the_derived_rows_once_the_engine_has_run(
    client, db, settings, tmp_path, capsys, monkeypatch
):
    ingest(client, monkeypatch)
    recompute(db, settings, now=AFTER_INGEST)
    assert metrics_rows(db, "daily_metrics", "day_local")["2026-09-22"]["quality_workout"] is True

    code, out = verify_cli(tmp_path, capsys)

    assert code == 0, out
    assert "0 difference(s)" in out
    assert "derived rows not compared" not in out


def test_verify_goes_red_when_a_stored_metric_is_not_what_the_data_says(
    client, db, settings, tmp_path, capsys, monkeypatch
):
    ingest(client, monkeypatch)
    recompute(db, settings, now=AFTER_INGEST)
    row = metrics_rows(db, "daily_metrics", "day_local")["2026-09-22"]
    row["quality_workout"] = False
    db.execute(
        "UPDATE daily_metrics SET metrics_json = ? WHERE day_local = ?",
        (json.dumps(row, sort_keys=True), "2026-09-22"),
    )

    code, out = verify_cli(tmp_path, capsys)

    assert code == 1
    assert "daily_metrics: +" in out and "daily_metrics: -" in out


def test_verify_goes_red_when_the_load_bar_history_differs(
    client, db, settings, tmp_path, capsys, monkeypatch
):
    ingest(client, monkeypatch)
    recompute(db, settings, now=AFTER_INGEST)
    db.execute(
        "INSERT INTO load_bar_history (effective_from_week, value, source_ids_json, "
        "decided_at_utc) VALUES ('2026-09-28', 90, '[]', '2026-09-22T12:00:00Z')"
    )

    code, out = verify_cli(tmp_path, capsys)

    assert code == 1
    assert "load_bar_history: +" in out


def test_verify_leaves_derived_rows_out_with_a_note_when_the_engine_never_ran(
    client, db, settings, tmp_path, capsys, monkeypatch
):
    ingest(client, monkeypatch)

    code, out = verify_cli(tmp_path, capsys)

    assert code == 0
    assert "note: metrics: derived rows not compared: the metrics engine has not run" in out


def test_verify_leaves_derived_rows_out_when_live_has_a_newer_payload(
    client, db, settings, tmp_path, capsys, monkeypatch
):
    ingest(client, monkeypatch)
    recompute(db, settings, now=AFTER_INGEST)
    assert post(client, steps_payload({"2026-09-25": 3000})).json()["status"] == "ok"

    code, out = verify_cli(tmp_path, capsys)

    assert code == 0, out
    assert "derived rows not compared: live holds a payload received" in out
    assert "tools.metrics --recompute" in out


def test_snapshot_and_diff_include_the_derived_rows(
    client, db, settings, tmp_path, capsys, monkeypatch
):
    ingest(client, monkeypatch)
    recompute(db, settings, now=AFTER_INGEST)
    snap = tmp_path / "snap.json"
    assert replay.main(["--snapshot", str(snap)]) == 0
    saved = json.loads(snap.read_text())
    assert "load_bar_history" in saved
    assert any("quality_workout" in r["metrics_json"] for r in saved["daily_metrics"])
    recompute(db, settings, now=AFTER_INGEST + timedelta(days=1))
    capsys.readouterr()
    assert replay.main(["--diff", str(snap)]) == 1
    assert "daily_metrics: +" in capsys.readouterr().out
