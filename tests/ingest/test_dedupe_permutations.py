"""Issue #4 R1 and R6, exhaustively.

Clustering: three and four copies of one run from different apps, 4 minutes apart (so the
ends of the chain are outside the 5-minute window and only the chain joins them), each in its
own push. Every arrival order and every single failure-then-recovery point gives ONE
checksum, equal to its own replay.

Withdrawal and re-issue: a payload set whose result depends on order by design (last received
wins), so the property is per order: every single failure-then-recovery history equals the
no-failure run of the same order, and its replay.
"""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from app.config import StorageConfig
from app.db import open_db
from app.ingest import health
from tests.conftest import fixture_bytes
from tests.payloads import T0, workout, workouts_payload
from tools import replay
from tools.replay import checksum, snapshot

APPS = ("Apple Watch", "Nike Run Club", "Strava", "Garmin Connect")


def chain_payloads(n: int) -> tuple[bytes, ...]:
    copies = [
        workout(f"C-{i}", f"07:{4 * i:02d}", 40, APPS[i], hr=i == 0, name=f"Run {i}")
        for i in range(n)
    ]
    return tuple(workouts_payload(c, note=f"copy {i}") for i, c in enumerate(copies))


def run_history(
    workdir: Path,
    settings,
    payloads: tuple[bytes, ...],
    order: tuple[int, ...],
    fail: int | None,
    gap: int,
) -> str:
    """Arrive in `order`. The payload at position `fail` is archived but dies before it is
    parsed; it is re-posted after `gap` more payloads have landed."""
    workdir.mkdir(parents=True)
    own = replace(settings, storage=StorageConfig(workdir / "life.db", workdir / "raw"))
    conn = open_db(own.storage.db_path)
    try:

        def arrive(index: int, parse: bool, at: int) -> None:
            body = payloads[index]
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


def histories(n: int):
    for order in itertools.permutations(range(n)):
        yield order, None, 0
        for fail in range(n):
            for gap in range(n - fail):
                yield order, fail, gap


def label(order, fail, gap) -> str:
    return f"{''.join(map(str, order))}-{'clean' if fail is None else f'f{fail}g{gap}'}"


def all_histories(settings, tmp_path, payloads) -> dict[tuple, str]:
    return {
        h: run_history(tmp_path / label(*h), settings, payloads, *h)
        for h in histories(len(payloads))
    }


def assert_one_result(results: dict[tuple, str]) -> None:
    distinct: dict[str, tuple] = {}
    for history, digest in results.items():
        distinct.setdefault(digest, history)
    assert len(distinct) == 1, f"histories that disagree: {list(distinct.values())}"


def test_three_copies_chained_four_minutes_apart_give_one_result(settings, tmp_path):
    results = all_histories(settings, tmp_path, chain_payloads(3))
    assert len(results) == 6 * (1 + 6)
    assert_one_result(results)


def test_four_copies_chained_four_minutes_apart_give_one_result(settings, tmp_path):
    results = all_histories(settings, tmp_path, chain_payloads(4))
    assert len(results) == 24 * (1 + 10)
    assert_one_result(results)


WITHDRAWAL_SET = (
    fixture_bytes("workouts_v2_window_full.json"),
    fixture_bytes("workouts_v2_window_deleted.json"),
    fixture_bytes("workouts_v2_reissued_1.json"),
    fixture_bytes("workouts_v2_reissued_2.json"),
)


def test_withdrawal_and_reissue_recovery_equals_the_run_without_failure(settings, tmp_path):
    results = all_histories(settings, tmp_path, WITHDRAWAL_SET)
    assert len(results) == 24 * (1 + 10)
    for order in itertools.permutations(range(len(WITHDRAWAL_SET))):
        clean = results[(order, None, 0)]
        bad = [h for h, digest in results.items() if h[0] == order and digest != clean]
        assert not bad, f"recoveries that differ from the clean run of {order}: {bad}"
    by_order = {results[(o, None, 0)] for o in itertools.permutations(range(len(WITHDRAWAL_SET)))}
    assert len(by_order) > 1, "the set must exercise order-dependent withdrawal"
