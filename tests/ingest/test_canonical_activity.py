"""Issue #4: one canonical activity, closed for the deferred cases.

R1 clustering is a pure function of the set of copies; R2 same-source copies merge only when
they overlap in time; R3 a payload withdraws what it no longer lists inside the span it
covers; R4 ids cannot collide; R5 one person, one night across source labels.
"""

from __future__ import annotations

import itertools
import json
import random

from app.config import DedupeConfig
from app.ingest import dedupe, store
from app.ingest.activities import active_activities, active_hr_samples
from app.ingest.dedupe import Copy
from app.metrics.engine import read_inputs
from tests.conftest import count, fixture_bytes, fixture_json, post_fixture
from tests.payloads import post, workout, workouts_payload
from tools import replay
from tools.replay import checksum, snapshot

CFG = DedupeConfig(start_window_min=5, duration_tolerance_pct=10)


def activities(db) -> list[tuple]:
    rows = db.execute("SELECT * FROM activities ORDER BY start_utc, id").fetchall()
    return [
        (
            r["id"],
            r["withdrawn_at"] is not None,
            sorted(e["external_id"] for e in json.loads(r["merged_from_json"])),
        )
        for r in rows
    ]


def sources(db) -> list[tuple]:
    rows = db.execute(
        "SELECT external_id, source_app, activity_id, withdrawn_at IS NOT NULL AS gone "
        "FROM activity_sources ORDER BY external_id, source_app"
    ).fetchall()
    return [tuple(r) for r in rows]


def assert_replay_equal(db, settings, tmp_path, name: str = "s.db") -> None:
    scratch = replay.replay(settings.storage.raw_dir, tmp_path / name, settings, None, db)
    try:
        assert checksum(snapshot(scratch)) == checksum(snapshot(db))
    finally:
        scratch.close()


def reposted(name: str) -> bytes:
    """The fixture's content in different bytes, so it is parsed again, not a duplicate."""
    return json.dumps(fixture_json(name), indent=2).encode()


# R1 -------------------------------------------------------------------------------------


def copy(id_: str, start_min: int, minutes: int, source: str, hr: int = 0) -> Copy:
    start = f"2026-09-22T11:{start_min:02d}:00Z"
    end_min = start_min + minutes
    end = f"2026-09-22T{11 + end_min // 60:02d}:{end_min % 60:02d}:00Z"
    return Copy(id_, source, start, end, minutes * 60, hr, None, {})


def shape(clusters: list[list[Copy]]) -> list[list[str]]:
    return [[c.external_id for c in members] for members in clusters]


def test_cluster_is_a_function_of_the_set_of_copies():
    chain = [
        copy("A", 0, 40, "Apple Watch", hr=20),
        copy("B", 4, 40, "Nike Run Club"),
        copy("C", 8, 40, "Strava"),
        copy("D", 12, 40, "Garmin"),
        copy("E", 50, 20, "Apple Watch", hr=10),
    ]
    results = {
        json.dumps(shape(dedupe.cluster(list(order), CFG)))
        for order in itertools.permutations(chain)
    }
    assert results == {json.dumps([["A", "B", "C", "D"], ["E"]])}


def test_three_way_chain_is_one_activity_in_every_arrival_order(settings, tmp_path):
    from dataclasses import replace

    from app.config import StorageConfig
    from app.db import open_db
    from app.ingest import health

    chain = fixture_json("workouts_v2_chain.json")["data"]["workouts"]
    digests = set()
    for n, order in enumerate(itertools.permutations(chain)):
        own = replace(settings, storage=StorageConfig(tmp_path / f"{n}.db", tmp_path / f"raw{n}"))
        conn = open_db(own.storage.db_path)
        try:
            for w in order:
                body = json.dumps({"data": {"workouts": [w]}}).encode()
                archived = health.archive_raw(conn, own.storage.raw_dir, body)
                health.ingest_archived(conn, body, archived.raw_archive_id, own)
            assert activities(conn) == [
                ("CHAIN-A-0001", False, ["CHAIN-A-0001", "CHAIN-B-0002", "CHAIN-C-0003"])
            ]
            digests.add(checksum(snapshot(conn)))
        finally:
            conn.close()
    assert len(digests) == 1


def test_a_bridge_arriving_last_joins_two_stored_activities(client, db, settings, tmp_path):
    chain = fixture_json("workouts_v2_chain.json")["data"]["workouts"]
    a, b, c = chain
    post(client, workouts_payload(a, note="a"))
    post(client, workouts_payload(c, note="c"))
    assert len(activities(db)) == 2

    post(client, workouts_payload(b, note="bridge"))

    assert activities(db) == [
        ("CHAIN-A-0001", False, ["CHAIN-A-0001", "CHAIN-B-0002", "CHAIN-C-0003"])
    ]
    assert {r[2] for r in sources(db)} == {"CHAIN-A-0001"}
    assert_replay_equal(db, settings, tmp_path)


def test_a_copy_that_moves_out_of_its_cluster_splits_it(client, db):
    watch = workout("W-1", "07:00", 40, "Apple Watch")
    phone = workout("P-1", "07:01", 39, "Nike Run Club", hr=False)
    post(client, workouts_payload(watch, phone))
    assert [a[0] for a in activities(db)] == ["W-1"]

    moved = workout("P-1", "09:00", 39, "Nike Run Club", hr=False)
    post(client, workouts_payload(moved, note="edited in the app"))

    assert [(a[0], a[2]) for a in activities(db)] == [("W-1", ["W-1"]), ("P-1", ["P-1"])]


# R2 -------------------------------------------------------------------------------------


def test_reissued_id_from_the_same_source_is_one_activity(client, db, settings, tmp_path):
    post_fixture(client, "workouts_v2_reissued_1.json")
    post_fixture(client, "workouts_v2_reissued_2.json")

    active = [a for a in activities(db) if not a[1]]
    assert active == [("REISSUE-W-0001", False, ["REISSUE-P-0001-B", "REISSUE-W-0001"])]
    assert ("REISSUE-P-0001", "Nike Run Club", "REISSUE-P-0001", 1) in sources(db)
    assert len(active_activities(db)) == 1
    assert_replay_equal(db, settings, tmp_path)


def test_same_source_overlapping_copies_merge_without_a_withdrawal(client, db):
    """The re-issued copy starts a minute later than the old one, so this one-workout push
    covers no span that holds the old copy and withdraws nothing: R2 alone merges them."""
    post_fixture(client, "workouts_v2_reissued_1.json")
    p1b = fixture_json("workouts_v2_reissued_2.json")["data"]["workouts"][1]
    p1b["start"] = "2026-09-22 07:02:00 -0400"
    p1b["end"] = "2026-09-22 07:41:00 -0400"
    post(client, workouts_payload(p1b, note="re-issued copy alone"))

    assert activities(db) == [
        ("REISSUE-W-0001", False, ["REISSUE-P-0001", "REISSUE-P-0001-B", "REISSUE-W-0001"])
    ]


def test_back_to_back_short_workouts_from_one_device_stay_two(client, db):
    first = workout("HIIT-1", "07:00", 3, "Apple Watch", name="HIIT")
    second = workout("HIIT-2", "07:04", 3, "Apple Watch", name="HIIT")
    resp = post(client, workouts_payload(first, second))

    assert resp.json()["workouts_merged"] == 0
    assert [a[0] for a in activities(db)] == ["HIIT-1", "HIIT-2"]


# R3 -------------------------------------------------------------------------------------


def test_workout_deleted_in_health_is_withdrawn_not_deleted(client, db, settings, tmp_path):
    post_fixture(client, "workouts_v2_window_full.json")
    resp = post_fixture(client, "workouts_v2_window_deleted.json")

    assert resp.json()["workouts_withdrawn"] == 1
    assert activities(db) == [
        ("WINDOW-MON-0001", False, ["WINDOW-MON-0001"]),
        ("WINDOW-WED-0002", True, ["WINDOW-WED-0002"]),
        ("WINDOW-FRI-0003", False, ["WINDOW-FRI-0003"]),
    ]
    wed = db.execute("SELECT * FROM activity_sources WHERE external_id = 'WINDOW-WED-0002'")
    row = wed.fetchone()
    received = db.execute("SELECT received_at_utc FROM raw_archive WHERE id = 2").fetchone()[0]
    assert row["withdrawn_at"] == received
    assert json.loads(row["provenance_json"])["type"] == "Functional Strength Training"
    assert (
        db.execute(
            "SELECT COUNT(*) FROM workout_hr_samples WHERE external_id = 'WINDOW-WED-0002'"
        ).fetchone()[0]
        == 15
    )
    assert [a["id"] for a in active_activities(db)] == ["WINDOW-MON-0001", "WINDOW-FRI-0003"]
    assert "WINDOW-WED-0002" not in active_hr_samples(db)
    assert [a.id for a in read_inputs(db, settings).activities] == [
        "WINDOW-MON-0001",
        "WINDOW-FRI-0003",
    ]
    assert_replay_equal(db, settings, tmp_path)


def test_withdrawn_workout_that_reappears_is_active_again(client, db, settings, tmp_path):
    post_fixture(client, "workouts_v2_window_full.json")
    post_fixture(client, "workouts_v2_window_deleted.json")
    assert post(client, reposted("workouts_v2_window_full.json")).json()["status"] == "ok"

    assert [a[1] for a in activities(db)] == [False, False, False]
    withdrawn = "SELECT COUNT(*) FROM activity_sources WHERE withdrawn_at IS NOT NULL"
    assert db.execute(withdrawn).fetchone()[0] == 0
    assert count(db, "activities") == 3
    assert len(active_activities(db)) == 3
    assert_replay_equal(db, settings, tmp_path)


def test_withdrawal_only_inside_the_span_the_payload_covers(client, db):
    post_fixture(client, "workouts_v2_window_full.json")
    mon, wed, _ = fixture_json("workouts_v2_window_full.json")["data"]["workouts"]

    resp = post(client, workouts_payload(mon, wed, note="Fri lies after this payload's span"))

    assert resp.json()["workouts_withdrawn"] == 0
    assert [a[1] for a in activities(db)] == [False, False, False]


def test_withdrawal_only_for_sources_the_payload_carries(client, db):
    post_fixture(client, "workouts_v2_window_full.json")
    phone = workout("P-1", "07:00", 39, "Nike Run Club", hr=False)
    post(client, workouts_payload(phone, note="phone app, 2026-09-22"))
    mon, _, fri = fixture_json("workouts_v2_window_full.json")["data"]["workouts"]

    post(client, workouts_payload(mon, fri, note="Watch only"))

    gone = [r[0] for r in sources(db) if r[3]]
    assert gone == ["WINDOW-WED-0002"]


def test_a_payload_with_no_workouts_withdraws_nothing(client, db):
    post_fixture(client, "workouts_v2_window_full.json")
    post_fixture(client, "metrics_v2_days.json")
    assert [a[1] for a in activities(db)] == [False, False, False]


def test_withdrawing_one_copy_leaves_the_activity_to_the_other(client, db, settings, tmp_path):
    watch = workout("W-1", "07:00", 40, "Apple Watch")
    phone = workout("P-1", "07:01", 39, "Nike Run Club", hr=False, name="Run (NRC)")
    later = workout("P-2", "09:00", 30, "Nike Run Club", hr=False)
    post(client, workouts_payload(watch, phone, later))
    early = workout("P-0", "06:00", 20, "Nike Run Club", hr=False)

    post(client, workouts_payload(early, later, note="NRC no longer lists P-1"))

    assert activities(db) == [
        ("P-0", False, ["P-0"]),
        ("W-1", False, ["W-1"]),
        ("P-1", True, ["P-1"]),
        ("P-2", False, ["P-2"]),
    ]
    assert_replay_equal(db, settings, tmp_path)


# R4 -------------------------------------------------------------------------------------


def test_canonical_id_held_by_another_activity_cannot_collide(client, db, settings, tmp_path):
    """Before issue #4 this hit the re-key skip: the 07:00 activity's canonical copy has the
    external id of the unrelated 18:00 activity, and the activity kept a stale id."""
    evening = workout("Z-1", "18:00", 30, "Strava", hr=False, name="Evening ride")
    post(client, workouts_payload(evening))
    phone = workout("Q-1", "07:00", 39, "Nike Run Club", hr=False)
    post(client, workouts_payload(phone, note="morning, phone"))
    watch = workout("Z-1", "07:01", 40, "Apple Watch")
    post(client, workouts_payload(watch, note="morning, Watch, same id as the ride"))

    assert activities(db) == [
        ("Z-1|Apple Watch", False, ["Q-1", "Z-1"]),
        ("Z-1|Strava", False, ["Z-1"]),
    ]
    assert sources(db) == [
        ("Q-1", "Nike Run Club", "Z-1|Apple Watch", 0),
        ("Z-1", "Apple Watch", "Z-1|Apple Watch", 0),
        ("Z-1", "Strava", "Z-1|Strava", 0),
    ]
    assert not hasattr(store, "_rekey")
    assert_replay_equal(db, settings, tmp_path)


# R5 -------------------------------------------------------------------------------------


def nights(db) -> list[tuple]:
    rows = db.execute(
        "SELECT wake_day_local, source, asleep_s FROM sleep_sessions ORDER BY start_utc"
    ).fetchall()
    return [tuple(r) for r in rows]


def test_same_night_under_a_changed_label_is_one_row(client, db, settings, tmp_path):
    post_fixture(client, "metrics_v2_sleep_label_a.json")
    post_fixture(client, "metrics_v2_sleep_label_b.json")

    assert nights(db) == [("2026-09-30", "Apple Watch|Oura", round(6.9 * 3600))]
    assert_replay_equal(db, settings, tmp_path)


def test_sessions_under_two_labels_that_barely_overlap_are_both_kept(client, db):
    post_fixture(client, "metrics_v2_sleep_label_a.json")
    nap = fixture_json("metrics_v2_sleep_label_b.json")
    row = nap["data"]["metrics"][0]["data"][0]
    row.update(
        sleepStart="2026-09-30 06:20:00 -0400",
        sleepEnd="2026-09-30 07:20:00 -0400",
        inBedStart="2026-09-30 06:20:00 -0400",
        inBedEnd="2026-09-30 07:20:00 -0400",
        asleep=0.8,
        totalSleep=0.8,
    )
    post(client, json.dumps(nap).encode())

    assert [n[1] for n in nights(db)] == ["Apple Watch", "Apple Watch|Oura"]


def test_short_session_absent_from_the_next_payload_of_its_source_is_gone(client, db):
    night = fixture_json("metrics_v2_sleep_label_a.json")
    row = night["data"]["metrics"][0]["data"][0]
    short = {
        **row,
        "asleep": 0.4,
        "totalSleep": 0.4,
        "sleepStart": "2026-09-30 13:00:00 -0400",
        "sleepEnd": "2026-09-30 13:25:00 -0400",
    }
    night["data"]["metrics"][0]["data"].append(short)
    post(client, json.dumps(night).encode())
    assert len(nights(db)) == 2

    post_fixture(client, "metrics_v2_sleep_label_a.json")

    assert nights(db) == [("2026-09-30", "Apple Watch", round(7.2 * 3600))]


def test_one_payload_with_one_night_under_two_labels_stores_the_fuller(client, db):
    a = fixture_json("metrics_v2_sleep_label_a.json")["data"]["metrics"][0]["data"][0]
    b = fixture_json("metrics_v2_sleep_label_b.json")["data"]["metrics"][0]["data"][0]
    metric = {"name": "sleep_analysis", "units": "hr", "data": [b, a]}
    post(client, json.dumps({"data": {"metrics": [metric]}}).encode())

    assert nights(db) == [("2026-09-30", "Apple Watch", round(7.2 * 3600))]


# Fixtures stay what the README says ------------------------------------------------------


def test_new_fixtures_are_listed_in_the_readme():
    readme = fixture_bytes("README.md").decode()
    for name in (
        "workouts_v2_reissued_1.json",
        "workouts_v2_reissued_2.json",
        "workouts_v2_chain.json",
        "workouts_v2_window_full.json",
        "workouts_v2_window_deleted.json",
        "metrics_v2_sleep_label_a.json",
        "metrics_v2_sleep_label_b.json",
    ):
        assert f"`{name}`" in readme, name


def test_random_sets_of_copies_cluster_the_same_in_any_order():
    rng = random.Random(4)
    apps = ["Apple Watch", "Nike Run Club", "Strava", "unknown"]
    for _ in range(200):
        copies = [
            copy(
                f"X{i}",
                rng.randrange(0, 40),
                rng.randrange(1, 19),
                rng.choice(apps),
                rng.choice([0, 5]),
            )
            for i in range(6)
        ]
        first = shape(dedupe.cluster(copies, CFG))
        rng.shuffle(copies)
        assert shape(dedupe.cluster(copies, CFG)) == first
