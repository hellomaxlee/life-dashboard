"""Late recovery must be indistinguishable from a history with no failure: merged activities
are a pure function of the set of attached copies, and a recovery never trusts a later raw
file it has not checked."""

from __future__ import annotations

import hashlib
import itertools
import json
import sqlite3
import subprocess
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from app.config import StorageConfig
from app.db import open_db
from app.ingest import claude_usage, health
from tests.payloads import (
    T0,
    fail_next_stores,
    post,
    steps,
    steps_payload,
    tick_clock,
    workout,
    workouts_payload,
)
from tools import claude_usage_hook, replay
from tools.replay import checksum, snapshot

WATCH = workout("W-1", "07:01", 40, "Apple Watch")
PHONE = workout("P-1", "07:00", 39, "Nike Run Club", hr=False, name="Run (NRC)")
THIRD = workout("Q-1", "07:02", 40, "Strava", hr=False, name="Morning Run")


def activities(db) -> list[tuple]:
    rows = db.execute("SELECT * FROM activities ORDER BY id").fetchall()
    return [
        (
            r["id"],
            r["type"],
            r["start_utc"],
            r["duration_s"],
            [e["external_id"] for e in json.loads(r["merged_from_json"])],
        )
        for r in rows
    ]


def replay_checksum(db, settings, tmp_path) -> str:
    scratch = replay.replay(settings.storage.raw_dir, tmp_path / "scratch.db", settings, None, db)
    try:
        return checksum(snapshot(scratch))
    finally:
        scratch.close()


def test_late_recovery_of_a_merged_activity_matches_replay(
    client, db, settings, tmp_path, monkeypatch
):
    first, second = workouts_payload(WATCH, note="A"), workouts_payload(PHONE, WATCH, note="B")
    tick_clock(monkeypatch)
    fail_next_stores(monkeypatch)
    assert post(client, first).status_code == 500
    assert post(client, second).json()["status"] == "ok"
    assert post(client, first).json()["status"] == "ok"

    assert activities(db) == [("W-1", "Running", "2026-09-22T11:01:00Z", 2400, ["W-1", "P-1"])]
    assert replay_checksum(db, settings, tmp_path) == checksum(snapshot(db))


def test_canonical_copy_without_heart_rate_is_the_earliest_then_the_lowest_id(client, db):
    later = workout("A-9", "07:02", 40, "Strava", hr=False, name="Later")
    earlier = workout("Z-1", "07:00", 40, "Nike Run Club", hr=False, name="Earlier")
    post(client, workouts_payload(later))
    post(client, workouts_payload(earlier, note="second"))
    assert activities(db) == [("Z-1", "Earlier", "2026-09-22T11:00:00Z", 2400, ["Z-1", "A-9"])]

    tie_low = workout("B-1", "09:00", 30, "Strava", hr=False, name="Low id")
    tie_high = workout("C-1", "09:00", 30, "Nike Run Club", hr=False, name="High id")
    post(client, workouts_payload(tie_high, note="third"))
    post(client, workouts_payload(tie_low, note="fourth"))
    assert activities(db)[0] == ("B-1", "Low id", "2026-09-22T13:00:00Z", 1800, ["B-1", "C-1"])


def test_rekeyed_activity_keeps_its_sources_and_heart_rate_samples(client, db):
    post(client, workouts_payload(PHONE))
    assert [a[0] for a in activities(db)] == ["P-1"]

    post(client, workouts_payload(WATCH, note="watch arrives"))

    assert [a[0] for a in activities(db)] == ["W-1"]
    sources = db.execute("SELECT external_id, activity_id FROM activity_sources ORDER BY 1")
    assert [tuple(r) for r in sources] == [("P-1", "W-1"), ("W-1", "W-1")]
    samples = db.execute(
        "SELECT DISTINCT c.activity_id FROM workout_hr_samples s JOIN activity_sources c "
        "ON (c.external_id, c.source_app) = (s.external_id, s.source_app)"
    ).fetchall()
    assert [r[0] for r in samples] == ["W-1"]
    assert db.execute("SELECT hr_sample_count FROM activities").fetchone()[0] == 20


PAYLOADS = (
    workouts_payload(WATCH, note="watch only"),
    workouts_payload(PHONE, WATCH, note="both"),
    workouts_payload(THIRD, note="third app"),
    steps_payload({"2026-09-22": 8000}),
)


def run_history(workdir: Path, settings, order: tuple[int, ...], fail: int | None, gap: int) -> str:
    """Arrive in `order`. The payload at position `fail` is archived but dies before it is
    parsed (the kill state); it is re-posted after `gap` more payloads have landed."""
    workdir.mkdir(parents=True)
    own = replace(settings, storage=StorageConfig(workdir / "life.db", workdir / "raw"))
    conn = open_db(own.storage.db_path)
    try:

        def arrive(index: int, parse: bool, at: int) -> None:
            body = PAYLOADS[index]
            moment = T0 + timedelta(minutes=at)
            archived = health.archive_raw(conn, own.storage.raw_dir, body, received_at=moment)
            if parse and not archived.duplicate:
                health.ingest_archived(conn, body, archived.raw_archive_id, own)

        for position, index in enumerate(order):
            arrive(index, parse=position != fail, at=position)
            if fail is not None and position == fail + gap:
                arrive(order[fail], parse=True, at=100)
        live = checksum(snapshot(conn))
        scratch = replay.replay(own.storage.raw_dir, workdir / "scratch.db", own, None, conn)
        try:
            assert checksum(snapshot(scratch)) == live, (order, fail, gap)
        finally:
            scratch.close()
        return live
    finally:
        conn.close()


def test_every_arrival_order_and_every_single_failure_recovery_gives_one_result(settings, tmp_path):
    results: dict[tuple, str] = {}
    for order in itertools.permutations(range(len(PAYLOADS))):
        results[(order, None, 0)] = run_history(
            tmp_path / f"{''.join(map(str, order))}-clean", settings, order, None, 0
        )
        for fail in range(len(order)):
            for gap in range(len(order) - fail):
                name = f"{''.join(map(str, order))}-f{fail}g{gap}"
                results[(order, fail, gap)] = run_history(
                    tmp_path / name, settings, order, fail, gap
                )
    assert len(results) == 24 + 24 * 10
    distinct = {}
    for history, digest in results.items():
        distinct.setdefault(digest, history)
    assert len(distinct) == 1, f"histories that disagree: {list(distinct.values())}"


def later_file(settings, body: bytes) -> Path:
    files = (settings.storage.raw_dir / "health").glob("*.json")
    return next(f for f in files if f.read_bytes() == body)


def stranded_then_two_later(client, monkeypatch) -> tuple[bytes, bytes]:
    late = steps_payload({"2026-09-28": 1000})
    middle = steps_payload({"2026-09-29": 2500})
    tick_clock(monkeypatch)
    fail_next_stores(monkeypatch)
    assert post(client, late).status_code == 500
    assert post(client, middle).json()["status"] == "ok"
    assert post(client, steps_payload({"2026-09-30": 3000})).json()["status"] == "ok"
    return late, middle


def test_recovery_refuses_a_later_raw_file_that_was_edited(client, db, settings, monkeypatch):
    late, middle = stranded_then_two_later(client, monkeypatch)
    later_file(settings, middle).write_bytes(middle.replace(b"2500", b"9999"))

    retry = post(client, late)

    assert retry.status_code == 500
    assert "sha256" in retry.json()["error"]
    assert steps(db) == {"2026-09-29": 2500, "2026-09-30": 3000}
    row = db.execute("SELECT parsed_ok, error FROM raw_archive WHERE id = 1").fetchone()
    assert row["parsed_ok"] == 0 and "sha256" in row["error"]


def test_garbage_in_a_later_raw_file_is_not_reported_as_a_malformed_push(
    client, db, settings, monkeypatch
):
    late, middle = stranded_then_two_later(client, monkeypatch)
    later_file(settings, middle).write_bytes(b"\x00garbage")

    retry = post(client, late)

    assert retry.status_code == 500
    assert retry.json()["status"] == "error"
    assert steps(db) == {"2026-09-29": 2500, "2026-09-30": 3000}


def test_replay_verify_refuses_an_altered_raw_file(client, db, settings, tmp_path, capsys):
    body = steps_payload({"2026-09-29": 2500})
    name = post(client, body).json()["file"]
    (settings.storage.raw_dir / "health" / name).write_bytes(body.replace(b"2500", b"2500 "))

    code = replay.main(["--verify", "--scratch", str(tmp_path / "s.db")])
    out = capsys.readouterr().out

    assert code == 1
    assert f"altered raw file: health/{name}" in out
    assert "steps_daily: +" in out


def test_racing_row_that_is_not_parsed_yet_is_parsed_not_called_a_duplicate(
    client, db, settings, monkeypatch
):
    body = steps_payload({"2026-09-29": 7})
    real_write, raced = health._write_durably, []

    def write_then_another_process_inserts_the_row(target, data):
        real_write(target, data)
        if not raced:
            raced.append(True)
            other = sqlite3.connect(settings.storage.db_path)
            other.execute(
                "INSERT INTO raw_archive (source, received_at_utc, sha256, path, byte_len, "
                "parsed_ok) VALUES ('health', '2026-10-02T00:00:00Z', ?, ?, ?, 0)",
                (hashlib.sha256(data).hexdigest(), f"health/{target.name}", len(data)),
            )
            other.commit()
            other.close()

    monkeypatch.setattr(health, "_write_durably", write_then_another_process_inserts_the_row)

    first = post(client, body)

    assert (first.status_code, first.json()["status"]) == (200, "ok")
    assert steps(db) == {"2026-09-29": 7}
    assert post(client, body).json()["status"] == "duplicate"


def test_health_recovery_reapplies_only_health_rows(client, db, settings, tmp_path, monkeypatch):
    usage = replace(settings.claude_usage, path=tmp_path / "claude_usage.json")
    with_usage = replace(settings, claude_usage=usage)
    late = steps_payload({"2026-09-28": 1000})
    tick_clock(monkeypatch)
    fail_next_stores(monkeypatch)
    assert post(client, late).status_code == 500
    record = {
        "captured_at_utc": "2026-10-01T14:00:01Z",
        "rate_limits": {"seven_day": {"used_percentage": 41.0, "resets_at": 1759680000}},
    }
    usage.path.write_text(json.dumps(record))
    assert claude_usage.read_usage_file(db, with_usage).status == "ok"
    for archived in (settings.storage.raw_dir / "claude_usage").iterdir():
        archived.unlink()
    assert post(client, steps_payload({"2026-09-29": 2500})).json()["status"] == "ok"

    retry = post(client, late)

    assert (retry.status_code, retry.json()["status"]) == (200, "ok")
    assert retry.json()["reapplied"] == 1
    metrics = json.loads(db.execute("SELECT metrics_json FROM daily_metrics").fetchone()[0])
    assert metrics["claude_week_used_pct"] == 41.0


def test_hook_with_out_and_no_value_is_silent(tmp_path):
    hook = Path(claude_usage_hook.__file__)
    run = subprocess.run(
        [sys.executable, str(hook), "--out"],
        input=b'{"rate_limits": {"seven_day": {"used_percentage": 1}}}',
        capture_output=True,
        cwd=tmp_path,
    )
    assert (run.returncode, run.stdout, run.stderr) == (0, b"", b"")


def test_later_payload_that_no_longer_applies_is_a_recovery_error_not_a_malformed_push(
    client, db, settings, monkeypatch
):
    late, _ = stranded_then_two_later(client, monkeypatch)
    real_parse, calls = health.parse_payload, []

    def parses_only_the_late_payload(payload, tz, *args):
        calls.append(1)
        if len(calls) > 1:
            raise ValueError("payload has no 'data' object")
        return real_parse(payload, tz, *args)

    monkeypatch.setattr(health, "parse_payload", parses_only_the_late_payload)

    retry = post(client, late)

    assert retry.status_code == 500
    assert retry.json()["status"] == "error"
    assert "cannot re-apply raw_archive 2" in retry.json()["error"]
    assert steps(db) == {"2026-09-29": 2500, "2026-09-30": 3000}
