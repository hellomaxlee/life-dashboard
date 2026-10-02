"""`tools.replay --verify` compares a from-scratch replay with LIVE, and replay order is
raw_archive.id order whenever a db can say what that order was."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from app.ingest import health, store
from tests.conftest import post_fixture
from tests.payloads import post, steps, steps_payload
from tools import backup, replay

FIXTURES = ("workouts_v2_overlap.json", "metrics_v2_days.json", "malformed.json")


def verify_cli(tmp_path, capsys) -> tuple[int, str, str]:
    code = replay.main(["--verify", "--scratch", str(tmp_path / "verify-scratch.db")])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_verify_is_green_when_replay_rebuilds_live(client, db, settings, tmp_path, capsys):
    for name in FIXTURES:
        post_fixture(client, name)

    code, out, _ = verify_cli(tmp_path, capsys)

    assert code == 0
    assert "0 difference(s)" in out
    assert "unparsed" in out and "1 recorded payload" in out


def test_verify_goes_red_on_a_parser_mutant_the_old_three_steps_missed(
    client, db, settings, tmp_path, capsys, monkeypatch
):
    post_fixture(client, "metrics_v2_days.json")
    snap = tmp_path / "before.json"
    assert replay.main(["--snapshot", str(snap)]) == 0
    real_upsert = store.upsert_steps

    def doubled(conn, value):
        real_upsert(conn, type(value)(**{**value.__dict__, "value": value.value * 2}))

    monkeypatch.setattr(store, "upsert_steps", doubled)

    assert replay.main(["--since", "2000-01-01", "--scratch", str(tmp_path / "s.db")]) == 0
    assert replay.main(["--diff", str(snap)]) == 0
    capsys.readouterr()
    code, out, _ = verify_cli(tmp_path, capsys)

    assert code == 1
    assert "steps_daily: +" in out and "steps_daily: -" in out


def two_payloads_whose_sha_order_is_opposite_to_arrival() -> tuple[bytes, bytes]:
    n = 0
    while True:
        first = steps_payload({"2026-09-29": 2000}, note=f"a{n}")
        second = steps_payload({"2026-09-29": 2500}, note=f"b{n}")
        if hashlib.sha256(first).hexdigest() > hashlib.sha256(second).hexdigest():
            return first, second
        n += 1


def test_same_second_arrivals_replay_in_arrival_order(
    client, db, settings, tmp_path, capsys, monkeypatch
):
    first, second = two_payloads_whose_sha_order_is_opposite_to_arrival()
    monkeypatch.setattr(health, "now_utc", lambda: datetime(2026, 10, 2, 10, 0, tzinfo=UTC))
    post(client, first)
    post(client, second)
    assert steps(db) == {"2026-09-29": 2500}

    made = backup.create_backup(settings, tmp_path / "b1")

    assert backup.verify(made.path, settings) == []
    assert verify_cli(tmp_path, capsys)[0] == 0


def test_clock_stepped_backwards_replays_in_arrival_order(
    client, db, settings, tmp_path, capsys, monkeypatch
):
    monkeypatch.setattr(health, "now_utc", lambda: datetime(2026, 10, 2, 10, 0, tzinfo=UTC))
    post(client, steps_payload({"2026-09-29": 2000}))
    monkeypatch.setattr(health, "now_utc", lambda: datetime(2026, 10, 2, 9, 59, tzinfo=UTC))
    post(client, steps_payload({"2026-09-29": 2500}))
    assert steps(db) == {"2026-09-29": 2500}

    made = backup.create_backup(settings, tmp_path / "b1")

    assert backup.verify(made.path, settings) == []
    assert verify_cli(tmp_path, capsys)[0] == 0


def test_unrecorded_raw_file_is_named_and_neither_applied_nor_an_error(
    client, db, settings, tmp_path, capsys
):
    post(client, steps_payload({"2026-09-29": 2000}))
    orphan = settings.storage.raw_dir / "health" / "20270101T000000Z_deadbeef.json"
    orphan.write_bytes(steps_payload({"2026-09-29": 9999}))

    made = backup.create_backup(settings, tmp_path / "b1")
    capsys.readouterr()
    backup_code = backup.main(["--verify", str(made.path)])
    backup_out = capsys.readouterr().out
    code, out, _ = verify_cli(tmp_path, capsys)

    assert backup_code == 0
    assert "unrecorded" in backup_out and orphan.name in backup_out
    assert code == 0
    assert "1 unrecorded file" in out and orphan.name in out
    assert steps(db) == {"2026-09-29": 2000}


def test_recorded_payload_with_its_file_gone_is_a_difference(
    client, db, settings, tmp_path, capsys
):
    name = post(client, steps_payload({"2026-09-29": 2000})).json()["file"]
    (settings.storage.raw_dir / "health" / name).unlink()

    code, out, _ = verify_cli(tmp_path, capsys)

    assert code == 1
    assert f"missing raw file: health/{name}" in out


def test_rebuild_with_no_db_uses_filename_order_and_says_so(client, db, settings, tmp_path, capsys):
    post(client, steps_payload({"2026-09-29": 2000}))
    db.close()
    for suffix in ("", "-wal", "-shm"):
        (settings.storage.db_path.parent / (settings.storage.db_path.name + suffix)).unlink(
            missing_ok=True
        )

    code = replay.main(["--since", "2000-01-01", "--scratch", str(tmp_path / "rebuild.db")])
    captured = capsys.readouterr()

    assert code == 0
    assert "filename order" in captured.err and "approximate" in captured.err
    assert not settings.storage.db_path.exists()
