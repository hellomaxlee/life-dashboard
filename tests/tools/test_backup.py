from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import BackupConfig
from tests.conftest import post_fixture
from tools import backup, replay
from tools.replay import checksum, snapshot

FIXTURES = (
    "workouts_v2_overlap.json",
    "workouts_v2_no_hr.json",
    "metrics_v2_days.json",
    "metrics_v2_sleep_dst.json",
    "malformed.json",
)
NIGHT = datetime(2026, 10, 2, 7, 15, tzinfo=UTC)


@pytest.fixture
def live(client, db, settings):
    for name in FIXTURES:
        post_fixture(client, name)
    return checksum(snapshot(db))


def alter_one_row(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE steps_daily SET steps = steps + 1 WHERE rowid = 1")
    conn.commit()
    conn.close()


def test_backup_restore_matches_live_while_wal_is_open(live, db, settings, tmp_path):
    wal = Path(str(settings.storage.db_path) + "-wal")
    assert wal.stat().st_size > 0

    result = backup.create_backup(settings, tmp_path / "b1", NIGHT)
    restored = backup.restore(result.path, tmp_path / "restored")

    assert backup.integrity(restored) == "ok"
    assert backup.data_checksum(restored) == live == result.data_checksum
    manifest = json.loads((result.path / backup.MANIFEST_NAME).read_text())
    assert manifest["data_checksum"] == live
    assert manifest["created_at_utc"] == "2026-10-02T07:15:00Z"
    assert manifest["raw_files"] == len(FIXTURES) == result.raw_files
    live_raw = sorted(p.name for p in (settings.storage.raw_dir / "health").iterdir())
    restored_raw = sorted(p.name for p in (tmp_path / "restored" / "raw" / "health").iterdir())
    assert restored_raw == live_raw
    assert not (tmp_path / "b1.partial").exists()


def test_uncommitted_writes_are_not_in_the_backup(live, db, settings, tmp_path):
    db.execute("BEGIN")
    db.execute("UPDATE steps_daily SET steps = 1")
    result = backup.create_backup(settings, tmp_path / "b1")
    db.execute("ROLLBACK")
    assert result.data_checksum == live


def test_verify_passes_on_a_faithful_backup(live, settings, tmp_path, capsys):
    assert backup.main(["--out", str(tmp_path / "b1")]) == 0
    assert backup.main(["--verify", str(tmp_path / "b1")]) == 0
    assert "0 difference(s)" in capsys.readouterr().out


def test_verify_goes_red_when_one_backup_row_is_altered(live, settings, tmp_path, capsys):
    backup.create_backup(settings, tmp_path / "b1")
    alter_one_row(tmp_path / "b1" / backup.DB_NAME)

    assert backup.main(["--verify", str(tmp_path / "b1")]) == 1
    out = capsys.readouterr().out
    assert "live vs backup: steps_daily: +" in out
    assert "raw replay vs backup: steps_daily: -" in out
    assert "manifest: backup db checksum" in out


def test_verify_goes_red_when_live_moves_on(live, client, settings, tmp_path, capsys):
    backup.create_backup(settings, tmp_path / "b1")
    post_fixture(client, "workouts_v2_run.json")
    assert backup.main(["--verify", str(tmp_path / "b1"), "--no-replay"]) == 1
    assert "live vs backup: activities: +" in capsys.readouterr().out


def test_verify_goes_red_when_a_raw_file_is_altered_or_missing(live, settings, tmp_path):
    backup.create_backup(settings, tmp_path / "b1")
    files = sorted((tmp_path / "b1" / "raw" / "health").iterdir())
    files[0].write_bytes(b"{}")
    files[1].unlink()
    problems = backup.verify(tmp_path / "b1", settings, replay_check=False)
    assert any("sha256 mismatch" in p for p in problems)
    assert any("file missing" in p for p in problems)


def test_replaying_the_backed_up_raw_archive_reaches_the_same_checksum(live, settings, tmp_path):
    backup.create_backup(settings, tmp_path / "b1")
    scratch = replay.replay(tmp_path / "b1" / "raw", tmp_path / "scratch.db", settings)
    try:
        assert checksum(snapshot(scratch)) == live
    finally:
        scratch.close()


def test_restore_refuses_to_overwrite(live, settings, tmp_path, capsys, monkeypatch):
    backup.create_backup(settings, tmp_path / "b1")
    backup.restore(tmp_path / "b1", tmp_path / "restored")
    with pytest.raises(backup.BackupError):
        backup.restore(tmp_path / "b1", tmp_path / "restored")

    cli = ["--restore", str(tmp_path / "b1"), "--to"]
    assert backup.main([*cli, str(tmp_path / "restored")]) == 2
    assert backup.main([*cli, str(tmp_path / "restored"), "--overwrite"]) == 0

    live_dir = settings.storage.db_path.parent
    assert backup.main([*cli, str(live_dir)]) == 2
    assert backup.main([*cli, str(live_dir), "--overwrite"]) == 2
    assert "is the live db" in capsys.readouterr().err
    assert backup.data_checksum(settings.storage.db_path) == live


def test_overwrite_live_replaces_db_and_drops_stale_wal(live, db, settings, tmp_path):
    backup.create_backup(settings, tmp_path / "b1")
    db.execute("UPDATE steps_daily SET steps = steps + 1")
    wal = Path(str(settings.storage.db_path) + "-wal")
    frames_of_the_newer_db = wal.read_bytes()
    db.close()
    wal.write_bytes(frames_of_the_newer_db)

    live_dir = settings.storage.db_path.parent
    assert backup.main(["--restore", str(tmp_path / "b1"), "--to", str(live_dir)]) == 2
    args = ["--restore", str(tmp_path / "b1"), "--to", str(live_dir), "--overwrite-live"]
    assert backup.main(args) == 0

    assert not wal.exists()
    assert backup.data_checksum(settings.storage.db_path) == live


def test_nightly_keeps_the_newest_n_and_clears_partials(live, settings, tmp_path):
    nightly = replace(settings, backup=BackupConfig(tmp_path / "backups", "03:15", 3))
    abandoned = nightly.backup.dir / "life-20260101T000000Z.partial"
    abandoned.mkdir(parents=True)
    os.utime(abandoned, (time.time() - 7200, time.time() - 7200))
    (nightly.backup.dir / "keep-me").mkdir()
    for day in range(5):
        backup.nightly(nightly, NIGHT + timedelta(days=day))

    names = sorted(p.name for p in nightly.backup.dir.iterdir())
    assert names == [
        "keep-me",
        "life-20261004T071500Z",
        "life-20261005T071500Z",
        "life-20261006T071500Z",
    ]
    newest = backup.list_backups(nightly.backup.dir)[-1]
    assert backup.verify(newest, nightly) == []


def test_unchanged_raw_files_are_hard_linked_to_the_previous_backup(live, settings, tmp_path):
    first = backup.create_backup(settings, tmp_path / "life-20261002T071500Z")
    second = backup.create_backup(settings, tmp_path / "life-20261003T071500Z")
    assert first.raw_linked == 0
    assert second.raw_linked == second.raw_files == len(FIXTURES)

    for src in (settings.storage.raw_dir / "health").iterdir():
        rel = Path("raw") / "health" / src.name
        assert (first.path / rel).stat().st_ino == (second.path / rel).stat().st_ino
        assert (first.path / rel).stat().st_ino != src.stat().st_ino
        assert (second.path / rel).read_bytes() == src.read_bytes()


def test_backup_errors_are_reported_not_raised(settings, tmp_path, capsys):
    assert backup.main(["--out", str(tmp_path / "b1")]) == 2
    assert "no database" in capsys.readouterr().err
    assert not (tmp_path / "b1").exists()
    assert backup.main(["--verify", str(tmp_path / "nothing-here")]) == 2


def test_a_missing_raw_file_does_not_stop_backups(live, settings, tmp_path, capsys):
    nightly = replace(settings, backup=BackupConfig(tmp_path / "backups", "03:15", 3))
    gone = sorted((settings.storage.raw_dir / "health").iterdir())[0]
    gone.unlink()

    for day in range(3):
        result = backup.nightly(nightly, NIGHT + timedelta(days=day))

    names = sorted(p.name for p in nightly.backup.dir.iterdir())
    assert names == ["life-20261002T071500Z", "life-20261003T071500Z", "life-20261004T071500Z"]
    assert result.raw_missing == [f"health/{gone.name}"]
    manifest = json.loads((result.path / backup.MANIFEST_NAME).read_text())
    assert manifest["raw_missing"] == [f"health/{gone.name}"]
    assert result.data_checksum == live
    assert backup.main(["--verify", str(result.path)]) == 1
    out = capsys.readouterr().out
    assert f"file missing: {gone.name}" in out
    assert f"manifest: raw file was already missing at backup time: health/{gone.name}" in out


def test_a_failed_backup_leaves_no_partial_behind(live, settings, tmp_path, monkeypatch):
    def disk_full(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(backup, "_copy_raw", disk_full)
    with pytest.raises(OSError):
        backup.create_backup(settings, tmp_path / "backups" / "b1")
    assert list((tmp_path / "backups").iterdir()) == []


def test_a_corrupt_file_in_the_previous_backup_is_not_linked_forward(live, settings, tmp_path):
    first = backup.create_backup(settings, tmp_path / "life-20261002T071500Z")
    victim = sorted((first.path / "raw" / "health").iterdir())[0]
    good = victim.read_bytes()
    victim.write_bytes(b"x" * len(good))

    second = backup.create_backup(settings, tmp_path / "life-20261003T071500Z")

    assert (second.path / "raw" / "health" / victim.name).read_bytes() == good
    assert second.raw_linked == len(FIXTURES) - 1
    assert backup.verify(second.path, settings) == []


def test_prune_spares_a_partial_that_may_still_be_written(tmp_path):
    fresh = tmp_path / "life-20261002T071500Z.partial"
    stale = tmp_path / "life-20261001T071500Z.partial"
    (fresh / "raw" / "health").mkdir(parents=True)
    (stale / "raw" / "health").mkdir(parents=True)
    two_hours_ago = time.time() - 7200
    for path in (stale, stale / "raw", stale / "raw" / "health"):
        os.utime(path, (two_hours_ago, two_hours_ago))

    removed = backup.prune(tmp_path, 3)

    assert removed == [stale]
    assert fresh.is_dir() and not stale.exists()


def test_reading_a_missing_db_raises_and_creates_nothing(tmp_path):
    with pytest.raises(backup.BackupError):
        backup.integrity(tmp_path / "nope.db")
    with pytest.raises(backup.BackupError):
        backup.data_checksum(tmp_path / "nope.db")
    assert list(tmp_path.iterdir()) == []


def test_verify_goes_red_when_raw_archive_state_differs(live, settings, tmp_path, capsys):
    backup.create_backup(settings, tmp_path / "b1")
    conn = sqlite3.connect(tmp_path / "b1" / backup.DB_NAME)
    conn.execute("UPDATE raw_archive SET parsed_ok = 0 WHERE id = 1")
    conn.commit()
    conn.close()

    assert backup.main(["--verify", str(tmp_path / "b1"), "--no-replay"]) == 1
    out = capsys.readouterr().out
    assert 'live vs backup: raw_archive: - {"id": 1, "parsed_ok": 0' in out
    assert 'live vs backup: raw_archive: + {"id": 1, "parsed_ok": 1' in out
