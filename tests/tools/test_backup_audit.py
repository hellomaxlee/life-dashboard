"""Audit gates for tools.backup: what --verify must see, what restore must refuse, and
retention edge cases."""

from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import BackupConfig
from tests.conftest import post_fixture
from tests.payloads import post, steps_payload
from tools import backup

NIGHT = datetime(2026, 10, 2, 7, 15, tzinfo=UTC)


@pytest.fixture
def good(client, db, settings, tmp_path) -> Path:
    post_fixture(client, "workouts_v2_run.json")
    post_fixture(client, "metrics_v2_days.json")
    return backup.create_backup(settings, tmp_path / "good").path


def variant(good: Path, name: str, *statements: str) -> Path:
    copy = good.with_name(name)
    shutil.copytree(good, copy)
    conn = sqlite3.connect(copy / backup.DB_NAME)
    for statement in statements:
        conn.execute(statement)
    conn.commit()
    conn.close()
    return copy


def test_verify_is_repeatable_on_an_untouched_backup(good, settings):
    assert backup.verify(good, settings) == []
    assert backup.verify(good, settings) == []


def test_a_day_with_a_summary_line_still_verifies(client, db, settings, tmp_path):
    post_fixture(client, "metrics_v2_days.json")
    day = db.execute("SELECT day_local FROM steps_daily LIMIT 1").fetchone()[0]
    line = json.dumps({"summary_device_line": "Rest day.", "summary_source": "rules"})
    db.execute(
        "INSERT INTO daily_metrics VALUES (?, ?) ON CONFLICT (day_local) DO UPDATE SET "
        "metrics_json = json_patch(metrics_json, excluded.metrics_json)",
        (day, line),
    )
    db.commit()
    made = backup.create_backup(settings, tmp_path / "with-summary").path
    assert backup.verify(made, settings) == []


def test_reading_a_backup_does_not_change_it(good, settings):
    assert backup.integrity(good / backup.DB_NAME) == "ok"
    backup.data_checksum(good / backup.DB_NAME)
    assert sorted(p.name for p in good.iterdir()) == ["life.db", "manifest.json", "raw"]
    assert backup.verify(good, settings) == []


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM ingest_log",
        "UPDATE raw_archive SET error = 'x', received_at_utc = '1999-01-01T00:00:00Z'",
        "DELETE FROM schema_version",
    ],
)
def test_verify_sees_changes_outside_the_data_tables(good, settings, statement):
    damaged = variant(good, "damaged", statement)
    problems = backup.verify(damaged, settings)
    assert any("db file sha256" in p for p in problems), problems


def test_verify_reports_a_backup_that_would_not_boot(good, settings):
    damaged = variant(good, "noboot", "DELETE FROM schema_version")
    problems = backup.verify(damaged, settings)
    assert any(p.startswith("boot: ") for p in problems), problems


def test_verify_sees_a_flipped_byte_in_the_db_copy(good, settings):
    damaged = good.with_name("flipped")
    shutil.copytree(good, damaged)
    raw = bytearray((damaged / backup.DB_NAME).read_bytes())
    offset = bytes(raw).find(b"001_initial")
    raw[offset + 1] ^= 0x02
    (damaged / backup.DB_NAME).write_bytes(bytes(raw))

    problems = backup.verify(damaged, settings)

    assert any("db file sha256" in p for p in problems), problems


def test_verify_sees_an_extra_raw_file_in_the_backup(good, settings):
    extra = good / "raw" / "health" / "20270101T000000Z_deadbeef.json"
    extra.write_bytes(steps_payload({"2026-09-29": 1}))

    problems = backup.verify(good, settings)

    assert f"raw listing: not in the manifest: health/{extra.name}" in problems


@pytest.mark.parametrize("content", [b"", None])
def test_truncated_db_copy_is_a_backup_error_with_exit_2(good, settings, capsys, content):
    db_copy = good / backup.DB_NAME
    db_copy.write_bytes(db_copy.read_bytes()[:5000] if content is None else content)

    with pytest.raises(backup.BackupError):
        backup.verify(good, settings)
    assert backup.main(["--verify", str(good)]) == 2
    assert "backup error" in capsys.readouterr().err
    with pytest.raises(backup.BackupError):
        backup.restore(good, good.with_name("restored"))


def insensitive_twin(path: Path) -> Path | None:
    twin = path.with_name(path.name.upper())
    return twin if twin != path and twin.exists() else None


def test_live_guard_holds_when_the_live_dir_is_spelled_in_another_case(good, db, settings, capsys):
    twin = insensitive_twin(settings.storage.db_path.parent)
    if twin is None:
        pytest.skip("filesystem is case-sensitive")
    before = backup.data_checksum(settings.storage.db_path)
    db.execute("UPDATE steps_daily SET steps = steps + 1")

    code = backup.main(["--restore", str(good), "--to", str(twin), "--overwrite"])

    assert code == 2
    assert "is the live db" in capsys.readouterr().err
    assert backup.data_checksum(settings.storage.db_path) != before


def test_restore_refuses_to_replace_a_raw_file_that_differs(good, tmp_path, capsys):
    target = tmp_path / "elsewhere"
    backup.restore(good, target)
    victim = sorted((target / "raw" / "health").iterdir())[0]
    victim.write_bytes(b"something else entirely")
    (target / backup.DB_NAME).unlink()

    with pytest.raises(backup.BackupError, match=victim.name):
        backup.restore(good, target)
    assert victim.read_bytes() == b"something else entirely"
    assert not (target / backup.DB_NAME).exists()

    assert backup.main(["--restore", str(good), "--to", str(target), "--overwrite"]) == 0
    assert victim.read_bytes() != b"something else entirely"


def nightly_settings(settings, tmp_path, keep: int):
    return replace(settings, backup=BackupConfig(tmp_path / "backups", "03:15", keep))


def test_future_stamped_backup_does_not_make_new_backups_delete_themselves(
    good, settings, tmp_path
):
    nightly = nightly_settings(settings, tmp_path, keep=1)
    future = backup.nightly(nightly, NIGHT + timedelta(days=4000))

    made = backup.nightly(nightly, NIGHT)
    later = backup.nightly(nightly, NIGHT + timedelta(days=1))

    assert made.path != later.path
    assert later.path.is_dir()
    assert not made.path.exists()
    assert future.path.is_dir()
    assert backup.verify(later.path, nightly) == []


def test_two_backups_in_the_same_second_both_succeed(good, settings, tmp_path):
    nightly = nightly_settings(settings, tmp_path, keep=5)

    first = backup.nightly(nightly, NIGHT)
    second = backup.nightly(nightly, NIGHT)

    assert first.path != second.path
    assert backup.list_backups(nightly.backup.dir) == [first.path, second.path]
    assert backup.backup_created_at(second.path) == NIGHT
    assert backup.verify(second.path, nightly) == []


def test_verify_latest_never_picks_a_partial(good, settings, tmp_path, capsys, monkeypatch):
    backups = tmp_path / "backups"
    monkeypatch.setenv("LIFE_BACKUP_DIR", str(backups))
    nightly = nightly_settings(settings, tmp_path, keep=5)
    backup.nightly(nightly, NIGHT)
    (backups / "life-20991231T000000Z.partial").mkdir()

    assert backup.main(["--verify", "latest"]) == 0
    assert "0 difference(s)" in capsys.readouterr().out


def test_a_push_between_the_two_copies_leaves_every_backed_up_row_with_its_file(
    client, good, settings, tmp_path, monkeypatch
):
    real_db, real_raw, landed = backup._copy_db, backup._copy_raw, []

    def push_once() -> None:
        if not landed:
            landed.append(post(client, steps_payload({"2026-08-01": 77})).json()["status"])

    def copy_db_then_push(*args, **kwargs):
        result = real_db(*args, **kwargs)
        push_once()
        return result

    def copy_raw_then_push(*args, **kwargs):
        result = real_raw(*args, **kwargs)
        push_once()
        return result

    monkeypatch.setattr(backup, "_copy_db", copy_db_then_push)
    monkeypatch.setattr(backup, "_copy_raw", copy_raw_then_push)

    made = backup.create_backup(settings, tmp_path / "during")

    assert landed == ["ok"]
    assert made.raw_missing == []
    assert backup.missing_raw_files(made.path / backup.DB_NAME, made.path / "raw") == []
    manifest = json.loads((made.path / backup.MANIFEST_NAME).read_text())
    assert manifest["raw_files"] == 3


def test_backup_fails_when_its_db_copy_does_not_pass_integrity_check(
    good, settings, tmp_path, monkeypatch
):
    monkeypatch.setattr(backup, "integrity", lambda path: "row 3 missing from index x")
    with pytest.raises(backup.BackupError, match="integrity_check"):
        backup.create_backup(settings, tmp_path / "corrupt")
    assert not (tmp_path / "corrupt").exists()
    assert not (tmp_path / "corrupt.partial").exists()
