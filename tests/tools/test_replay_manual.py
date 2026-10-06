"""Manual workout overrides are authored input: `--verify` copies them into the scratch db
so the derived rows match, and never compares the table; `--rebuild-live` keeps them and
the recompute that follows still credits the day; a backup carries them."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.db import CODE_SCHEMA_VERSION, table_names
from app.metrics import manual
from app.metrics.engine import recompute
from tests.conftest import post_fixture
from tools import backup, replay
from tools.replay import checksum, snapshot

DAY = "2026-10-01"
NIGHT = datetime(2026, 10, 2, 7, 15, tzinfo=UTC)


def ingest(client) -> None:
    post_fixture(client, "workouts_v2_overlap.json")
    post_fixture(client, "metrics_v2_days.json")


def day_row(db) -> dict:
    query = "SELECT metrics_json FROM daily_metrics WHERE day_local = ?"
    row = db.execute(query, (DAY,)).fetchone()
    return json.loads(row["metrics_json"])


def test_migration_008_adds_the_table_as_an_authored_input(db):
    assert CODE_SCHEMA_VERSION >= 8
    assert "manual_workouts" in table_names(db)
    assert "manual_workouts" in replay.AUTHORED_TABLES
    assert replay.AUTHORED_INPUTS == ("manual_workouts",)
    assert not set(replay.AUTHORED_TABLES) & set(replay.DATA_TABLES + replay.DERIVED_TABLES)


def test_an_empty_override_table_leaves_the_snapshot_as_it_was(client, db):
    ingest(client)
    before = snapshot(db)
    assert "manual_workouts" not in before
    manual.add_override(db, DAY, "4 mile run")
    after = snapshot(db)
    assert [row["day_local"] for row in after["manual_workouts"]] == [DAY]
    assert checksum(after) != checksum(before)
    assert snapshot(db, authored=False) == before


def test_verify_compares_the_derived_rows_and_stays_green_with_an_override(
    client, db, settings, tmp_path, capsys
):
    ingest(client)
    manual.add_override(db, DAY, "4 mile run")
    recompute(db, settings)
    assert day_row(db)["quality_workout"] is True

    assert replay.main(["--verify", "--scratch", str(tmp_path / "verify.db")]) == 0
    out = capsys.readouterr().out
    assert "0 difference(s)" in out
    assert "derived rows not compared" not in out


def test_rebuild_live_keeps_the_override_and_the_day_stays_credited(
    client, db, settings, tmp_path, capsys
):
    ingest(client)
    manual.add_override(db, DAY, "4 mile run")
    recompute(db, settings)
    stored = dict(db.execute("SELECT * FROM manual_workouts").fetchone())

    db.execute("UPDATE steps_daily SET steps = 1")
    assert replay.main(["--rebuild-live", "--scratch", str(tmp_path / "rebuild.db")]) == 0
    capsys.readouterr()

    assert dict(db.execute("SELECT * FROM manual_workouts").fetchone()) == stored
    assert day_row(db)["quality_workout"] is True and day_row(db)["manual_workout"] is True
    assert replay.main(["--verify", "--scratch", str(tmp_path / "verify2.db")]) == 0


def test_a_backup_restores_the_override_and_verify_sees_it_change(
    client, db, settings, tmp_path, capsys
):
    ingest(client)
    manual.add_override(db, DAY, "4 mile run")
    made = backup.create_backup(settings, tmp_path / "b1", NIGHT)

    restored = backup.restore(made.path, tmp_path / "restored")
    assert backup.data_checksum(restored) == checksum(snapshot(db)) == made.data_checksum
    assert backup.verify(made.path, settings) == []

    manual.add_override(db, DAY, "changed my mind: 5 miles")
    problems = backup.verify(made.path, settings)
    assert problems and all("manual_workouts" in line for line in problems)
