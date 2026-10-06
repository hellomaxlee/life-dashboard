"""The month feature is authored output, like the summary's keys: a replay cannot reproduce
it, so `--verify` leaves it out, `--rebuild-live` keeps it, and a backup carries it."""

from __future__ import annotations

from datetime import UTC, datetime

from app.db import CODE_SCHEMA_VERSION, table_names
from app.month import store
from tests.conftest import post_fixture
from tests.month.conftest import feature_object
from tools import backup, replay
from tools.replay import checksum, snapshot

MONTH = "2026-10"
NIGHT = datetime(2026, 10, 2, 7, 15, tzinfo=UTC)


def ingest(client) -> None:
    post_fixture(client, "workouts_v2_overlap.json")
    post_fixture(client, "metrics_v2_days.json")


def test_migration_adds_both_tables(db):
    assert CODE_SCHEMA_VERSION >= 6
    assert {"month_features", "month_feature_attempts"} <= set(table_names(db))
    assert "month_features" in replay.AUTHORED_TABLES
    assert not set(replay.AUTHORED_TABLES) & set(replay.DATA_TABLES + replay.DERIVED_TABLES)


def test_an_empty_feature_table_leaves_the_snapshot_as_it_was(client, db):
    ingest(client)
    before = snapshot(db)
    assert "month_features" not in before
    store.record_attempt(db, MONTH, "2026-10-04", 2, "rejected")
    assert checksum(snapshot(db)) == checksum(before)

    store.save_feature(db, MONTH, feature_object(MONTH), "model", "m", "raw")

    after = snapshot(db)
    assert [row["month_local"] for row in after["month_features"]] == [MONTH]
    assert checksum(after) != checksum(before)
    assert snapshot(db, authored=False) == before


def test_verify_stays_green_with_a_feature_and_rebuild_live_keeps_it(
    client, db, settings, tmp_path, capsys
):
    ingest(client)
    store.save_feature(db, MONTH, feature_object(MONTH), "model", "m", "raw")
    store.record_attempt(db, MONTH, "2026-10-03", 2, "rejected")
    stored = dict(db.execute("SELECT * FROM month_features").fetchone())

    assert replay.main(["--verify", "--scratch", str(tmp_path / "verify.db")]) == 0
    assert "0 difference(s)" in capsys.readouterr().out

    db.execute("UPDATE steps_daily SET steps = 1")
    assert replay.main(["--rebuild-live", "--scratch", str(tmp_path / "rebuild.db")]) == 0
    capsys.readouterr()

    assert dict(db.execute("SELECT * FROM month_features").fetchone()) == stored
    assert store.load_feature(db, MONTH).title == "Small Woods"
    assert len(store.attempts(db, MONTH)) == 1
    assert replay.main(["--verify", "--scratch", str(tmp_path / "verify2.db")]) == 0


def test_a_backup_restores_the_feature_and_verify_sees_it_change(
    client, db, settings, tmp_path, capsys
):
    ingest(client)
    store.save_feature(db, MONTH, feature_object(MONTH), "model", "m", "raw")
    made = backup.create_backup(settings, tmp_path / "b1", NIGHT)

    restored = backup.restore(made.path, tmp_path / "restored")
    assert backup.data_checksum(restored) == checksum(snapshot(db)) == made.data_checksum
    assert backup.verify(made.path, settings) == []

    changed = {**feature_object(MONTH), "title": "Slow Tides"}
    store.save_feature(db, MONTH, changed, "model", "m", "raw")
    problems = backup.verify(made.path, settings)
    assert problems and all("month_features" in line for line in problems)
