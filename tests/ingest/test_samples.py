"""Issue #3: a real Health Metrics push carries samples, not day sums. The parser reduces
them to one value per home day and builds nights from sleep-stage segments.

Golden arithmetic for `fixtures/health/metrics_v2_samples.json` (all times -0400):

Steps, per (day, source) sum, the day takes the LARGEST source total:
  09-21 Watch  200 + 1200 + 800 + 1500.25 + 1499.75 + 2000 = 7200   iPhone 1000+2000+1500 = 4500
         -> 7200 "Apple Watch (summed)"      (sum across sources would be 11700)
  09-22 Watch  500 + 300 = 800                                       iPhone 3000+4000+2600 = 9600
         -> 9600 "iPhone (summed)"           (sum across sources would be 10400)
  09-23 Watch  2500 + 2500 + 2500 = 7500 (one at 23:30, 03:30Z next day, still 09-23)
         iPhone 7500 -> a tie, the alphabetically first label wins: 7500 "Apple Watch (summed)"
Daylight (minutes, Watch only): 09-21 0.5 + 0.5 + 20 + 14 = 35.0; 09-22 61.5; 09-23 none.
HRV (daily mean): 09-21 (40 + 50 + 36) / 3 = 42.0; 09-22 55; 09-23 (48 + 52) / 2 = 50.0.
Resting HR (latest of the day): 52, 51, 54. VO2 max: 09-22 has 48.9 at 18:19 and 49.2 at
19:41, listed in reverse order -> 49.2 (timestamp order, not file order).

Night 09-21 23:05 -> 09-22 07:00 (wake day 09-22), segments in minutes:
  Core 30, Deep 45, Core 70, Awake 10, REM 80, [10 min with no segment], Core 120, Deep 30,
  REM 50, Awake 10, Core 20
  core  = 30 + 70 + 120 + 20 = 240 min = 14400 s
  deep  = 45 + 30            =  75 min =  4500 s
  rem   = 80 + 50            = 130 min =  7800 s
  awake = 10 + 10            =  20 min =  1200 s
  asleep = core + deep + rem = 445 min = 26700 s (7.42 h); never Awake
  in_bed = 23:05 -> 07:00 span = 7 h 55 min = 28500 s = 26700 + 1200 + 600 (the silence)
Nap 09-22 14:00 -> 14:25 "Asleep" (unspecified): 1500 s, a session of its own (7 h after
the night, more than the 60-min gap), same wake day, no stage breakdown.
Night 09-22 23:30 -> 09-23 06:30 (wake day 09-23): Core 150 + Deep 60 + REM 60 + Core 150
  = 420 min = 25200 s asleep, 25200 s in bed, no awake segment.
"""

from __future__ import annotations

import json
import time

from app.ingest.parse import parse_payload
from tests.conftest import count, fixture_json, post_fixture
from tests.payloads import post
from tools import replay
from tools.replay import checksum, snapshot

TZ = "America/New_York"


def steps(db) -> list[tuple]:
    rows = db.execute("SELECT day_local, steps, source FROM steps_daily ORDER BY day_local")
    return [tuple(r) for r in rows]


def wellness(db, metric: str) -> list[tuple]:
    rows = db.execute(
        "SELECT day_local, value, source FROM wellness_daily WHERE metric = ? ORDER BY day_local",
        (metric,),
    )
    return [tuple(r) for r in rows]


def nights(db) -> list[dict]:
    rows = db.execute("SELECT * FROM sleep_sessions ORDER BY end_utc").fetchall()
    return [dict(r) for r in rows]


def test_steps_take_the_largest_source_total_per_home_day(client, db):
    resp = post_fixture(client, "metrics_v2_samples.json")
    assert resp.status_code == 200, resp.text
    assert steps(db) == [
        ("2026-09-21", 7200, "Apple Watch (summed)"),
        ("2026-09-22", 9600, "iPhone (summed)"),
        ("2026-09-23", 7500, "Apple Watch (summed)"),
    ]


def test_wellness_samples_reduce_by_their_rule(client, db):
    post_fixture(client, "metrics_v2_samples.json")
    assert wellness(db, "time_in_daylight") == [
        ("2026-09-21", 35.0, "Apple Watch (summed)"),
        ("2026-09-22", 61.5, "Apple Watch (summed)"),
    ]
    assert wellness(db, "heart_rate_variability") == [
        ("2026-09-21", 42.0, "Apple Watch (daily mean)"),
        ("2026-09-22", 55.0, "Apple Watch (daily mean)"),
        ("2026-09-23", 50.0, "Apple Watch (daily mean)"),
    ]
    assert wellness(db, "resting_heart_rate") == [
        ("2026-09-21", 52.0, "Apple Watch (latest)"),
        ("2026-09-22", 51.0, "Apple Watch (latest)"),
        ("2026-09-23", 54.0, "Apple Watch (latest)"),
    ]
    assert wellness(db, "vo2_max") == [("2026-09-22", 49.2, "Apple Watch (latest)")]


def test_standalone_heart_rate_is_ignored_quietly(client, db):
    resp = post_fixture(client, "metrics_v2_samples.json")
    assert resp.json()["unknown_metrics"] == []
    assert resp.json()["metrics_rows"] == 3 + 3 + 9
    log = db.execute("SELECT unknown_metrics FROM ingest_log").fetchone()
    assert log["unknown_metrics"] == "[]"


def test_night_is_built_from_stage_segments(client, db):
    post_fixture(client, "metrics_v2_samples.json")
    first, nap, second = nights(db)
    assert first["wake_day_local"] == "2026-09-22"
    assert (first["start_utc"], first["end_utc"]) == (
        "2026-09-22T03:05:00Z",
        "2026-09-22T11:00:00Z",
    )
    assert (first["in_bed_s"], first["asleep_s"]) == (28500, 26700)
    assert (first["core_s"], first["deep_s"], first["rem_s"], first["awake_s"]) == (
        14400,
        4500,
        7800,
        1200,
    )
    assert first["source"] == "Apple Watch"
    assert nap["wake_day_local"] == "2026-09-22"
    assert (nap["start_utc"], nap["end_utc"]) == ("2026-09-22T18:00:00Z", "2026-09-22T18:25:00Z")
    assert (nap["in_bed_s"], nap["asleep_s"]) == (1500, 1500)
    assert (nap["core_s"], nap["deep_s"], nap["rem_s"], nap["awake_s"]) == (None,) * 4
    assert second["wake_day_local"] == "2026-09-23"
    assert (second["in_bed_s"], second["asleep_s"], second["awake_s"]) == (25200, 25200, None)
    longest = db.execute(
        "SELECT MAX(asleep_s) AS s FROM sleep_sessions WHERE wake_day_local = '2026-09-22'"
    ).fetchone()
    assert longest["s"] == 26700


def test_dst_night_and_late_nap_from_segments(client, db):
    """Fall-back night 2026-10-31 23:20 EDT -> 2026-11-01 07:26 EST: Core 130 min (to 01:30
    EDT), Deep 60 (01:30 EDT -> 01:30 EST), REM 90, Awake 10, Core 256 (03:10 -> 07:26).
    asleep = 130 + 60 + 90 + 256 = 536 min = 32160 s; in bed = 03:20Z -> 12:26Z = 32760 s.
    Steps on 11-01: 100 (01:30 EDT) + 200 (01:30 EST) + 700 = 1000; 10-31 23:50 EDT: 50."""
    resp = post_fixture(client, "metrics_v2_samples_dst.json")
    assert resp.status_code == 200, resp.text
    nap, night = nights(db)
    assert (nap["wake_day_local"], nap["end_utc"]) == ("2026-10-30", "2026-10-31T03:30:00Z")
    assert night["wake_day_local"] == "2026-11-01"
    assert (night["start_utc"], night["end_utc"]) == (
        "2026-11-01T03:20:00Z",
        "2026-11-01T12:26:00Z",
    )
    assert (night["in_bed_s"], night["asleep_s"]) == (32760, 32160)
    assert (night["core_s"], night["deep_s"], night["rem_s"], night["awake_s"]) == (
        23160,
        3600,
        5400,
        600,
    )
    assert steps(db) == [
        ("2026-10-31", 50, "Apple Watch (summed)"),
        ("2026-11-01", 1000, "Apple Watch (summed)"),
    ]


def test_sample_payload_reposted_changes_nothing(client, db):
    post_fixture(client, "metrics_v2_samples.json")
    before = checksum(snapshot(db))
    resp = post_fixture(client, "metrics_v2_samples.json")
    assert resp.json()["status"] == "duplicate"
    payload = fixture_json("metrics_v2_samples.json")
    resp = client.post("/ingest/health", content=json.dumps(payload, indent=2).encode())
    assert resp.json()["status"] == "ok"
    assert checksum(snapshot(db)) == before
    assert count(db, "sleep_sessions") == 3


def test_verify_passes_on_a_db_built_from_both_shapes(client, db, settings, tmp_path, capsys):
    for name in ("metrics_v2_days.json", "metrics_v2_samples.json", "metrics_v2_samples_dst.json"):
        assert post_fixture(client, name).status_code == 200
    assert (
        count(db, "sleep_sessions") == 1 + 3 + 2 - 1
    )  # the samples' 09-23 night replaces the summary's
    code = replay.main(["--verify", "--scratch", str(tmp_path / "verify-scratch.db")])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "0 difference(s)" in out


def segments_only(payload: dict, keep) -> bytes:
    for metric in payload["data"]["metrics"]:
        if metric["name"] == "sleep_analysis":
            metric["data"] = [row for row in metric["data"] if keep(row)]
    return json.dumps(payload).encode()


def test_corrected_night_from_segments_replaces_the_first_report(client, db):
    post_fixture(client, "metrics_v2_samples.json")
    corrected = fixture_json("metrics_v2_samples.json")
    post(
        client, segments_only(corrected, lambda row: row["endDate"] != "2026-09-22 07:00:00 -0400")
    )

    rows = nights(db)
    assert [(r["wake_day_local"], r["end_utc"]) for r in rows] == [
        ("2026-09-22", "2026-09-22T10:40:00Z"),
        ("2026-09-22", "2026-09-22T18:25:00Z"),
        ("2026-09-23", "2026-09-23T10:30:00Z"),
    ]
    assert (rows[0]["in_bed_s"], rows[0]["asleep_s"]) == (28500 - 1200, 26700 - 1200)


def next_window(payload: dict) -> bytes:
    """The push a day later: its window opens at 09-22 00:00, so the 09-22 night arrives
    without its two segments from before midnight, and 09-21 is gone from every metric."""
    for metric in payload["data"]["metrics"]:
        metric["data"] = [row for row in metric["data"] if not row["date"].startswith("2026-09-21")]
    return json.dumps(payload).encode()


def test_night_cut_at_the_window_start_does_not_replace_the_whole_night(
    client, db, settings, tmp_path
):
    post_fixture(client, "metrics_v2_samples.json")
    later = fixture_json("metrics_v2_samples.json")
    post(client, next_window(later))

    rows = nights(db)
    assert [(r["wake_day_local"], r["start_utc"], r["in_bed_s"]) for r in rows] == [
        ("2026-09-22", "2026-09-22T03:05:00Z", 28500),
        ("2026-09-22", "2026-09-22T18:00:00Z", 1500),
        ("2026-09-23", "2026-09-23T03:30:00Z", 25200),
    ]
    assert steps(db)[0] == ("2026-09-21", 7200, "Apple Watch (summed)")
    scratch = replay.replay(settings.storage.raw_dir, tmp_path / "s.db", settings, None, db)
    try:
        assert checksum(snapshot(scratch)) == checksum(snapshot(db))
    finally:
        scratch.close()


def test_a_cut_night_is_stored_when_nothing_better_is_known(client, db):
    later = fixture_json("metrics_v2_samples.json")
    post(client, next_window(later))

    first = nights(db)[0]
    assert (first["wake_day_local"], first["start_utc"]) == ("2026-09-22", "2026-09-22T04:20:00Z")
    assert first["in_bed_s"] == 28500 - 75 * 60


def test_summarised_rows_keep_their_plain_source_label(client, db):
    post_fixture(client, "metrics_v2_days.json")
    assert wellness(db, "resting_heart_rate")[0] == ("2026-09-21", 52.0, "Apple Watch")
    assert steps(db)[0] == ("2026-09-21", 8421, "aggregate")


def real_sized_payload(days: int = 7) -> dict:
    """~100k rows the way a real push has them: per-second step and daylight slices from
    two sources, HRV each hour, one resting HR a day, and a night of ~20 segments."""
    E = " -0400"
    steps_rows, light_rows, hrv_rows, rhr_rows, hr_rows, sleep_rows = [], [], [], [], [], []
    for d in range(days):
        day = f"2026-09-{21 + d:02d}"
        for n in range(12000):
            second = 25200 + n * 5
            stamp = f"{day} {second // 3600:02d}:{(second // 60) % 60:02d}:{second % 60:02d}{E}"
            source = "Someone’s Apple Watch|Someone’s iPhone" if n % 7 else "Someone’s Apple Watch"
            steps_rows.append({"date": stamp, "qty": 0.41666666666666669, "source": source})
        for n in range(2100):
            second = 30000 + n
            stamp = f"{day} {second // 3600:02d}:{(second // 60) % 60:02d}:{second % 60:02d}{E}"
            light_rows.append({"date": stamp, "qty": 0.016666666666666666, "source": "Watch"})
        for h in range(0, 24, 2):
            stamp = f"{day} {h:02d}:13:00{E}"
            end = f"{day} {h:02d}:14:00{E}"
            hrv_rows.append(
                {
                    "date": stamp,
                    "start": stamp,
                    "end": end,
                    "qty": 40 + h,
                    "source": "Watch",
                    "heartbeatSeries": [{"date": stamp, "timeSinceStart": 0.8}] * 60,
                }
            )
        rhr_rows.append(
            {
                "date": f"{day} 00:02:00{E}",
                "start": f"{day} 00:02:00{E}",
                "end": f"{day} 23:58:00{E}",
                "qty": 52,
                "source": "Watch",
            }
        )
        for n in range(400):
            second = 21600 + n * 150
            stamp = f"{day} {second // 3600:02d}:{(second // 60) % 60:02d}:{second % 60:02d}{E}"
            hr_rows.append(
                {
                    "date": stamp,
                    "start": stamp,
                    "end": stamp,
                    "Min": 60,
                    "Avg": 70,
                    "Max": 80,
                    "context": "Not Set",
                    "source": "Watch",
                }
            )
        for n in range(20):
            start = f"{day} {(n * 20) // 60:02d}:{(n * 20) % 60:02d}:00{E}"
            end = f"{day} {((n + 1) * 20) // 60:02d}:{((n + 1) * 20) % 60:02d}:00{E}"
            sleep_rows.append(
                {
                    "date": start,
                    "start": start,
                    "startDate": start,
                    "end": end,
                    "endDate": end,
                    "qty": 0.333,
                    "value": ("Core", "Deep", "REM", "Awake")[n % 4],
                    "source": "Watch",
                }
            )
    return {
        "data": {
            "metrics": [
                {"name": "step_count", "units": "count", "data": steps_rows},
                {"name": "time_in_daylight", "units": "min", "data": light_rows},
                {"name": "heart_rate_variability", "units": "ms", "data": hrv_rows},
                {"name": "resting_heart_rate", "units": "count/min", "data": rhr_rows},
                {"name": "heart_rate", "units": "count/min", "data": hr_rows},
                {"name": "sleep_analysis", "units": "hr", "data": sleep_rows},
            ]
        }
    }


def test_a_real_sized_push_parses_in_seconds():
    payload = real_sized_payload()
    body = json.dumps(payload, indent=2, ensure_ascii=False).encode()
    rows = sum(len(m["data"]) for m in payload["data"]["metrics"])
    assert rows > 100_000 and len(body) > 15_000_000
    started = time.perf_counter()
    parsed = parse_payload(json.loads(body), TZ)
    elapsed = time.perf_counter() - started
    print(f"\nparsed {rows} rows / {len(body) / 1e6:.1f} MB in {elapsed:.2f} s")
    assert elapsed < 15, f"parse took {elapsed:.1f} s"
    assert len(parsed.steps) == 7 and len(parsed.sleep) == 7
    assert round(parsed.steps[0].value) == round((12000 - 1715) * 0.41666666666666669)
    assert parsed.steps[0].source == "Someone’s Apple Watch|Someone’s iPhone (summed)"
    assert parsed.unknown_metrics == []


def two_segments(gap_min: int) -> dict:
    """Core 2026-09-22 23:00 -> 09-23 01:00, then Core for 2 h starting `gap_min` later."""

    def stamp(minute: int) -> str:
        day, minute = divmod(minute, 1440)
        return f"2026-09-{22 + day:02d} {minute // 60:02d}:{minute % 60:02d}:00 -0400"

    first = (23 * 60, 25 * 60)
    second = (first[1] + gap_min, first[1] + gap_min + 120)
    rows = [
        {
            "date": stamp(start),
            "start": stamp(start),
            "startDate": stamp(start),
            "end": stamp(end),
            "endDate": stamp(end),
            "qty": (end - start) / 60,
            "value": "Core",
            "source": "Apple Watch",
        }
        for start, end in (first, second)
    ]
    return {"data": {"metrics": [{"name": "sleep_analysis", "units": "hr", "data": rows}]}}


def test_a_gap_of_exactly_the_threshold_keeps_one_session():
    """Gap 60 min = ingest.sleep_gap_min: one session; asleep 2 h + 2 h = 14400 s, in bed
    23:00 -> 04:00 = 5 h = 18000 s (the silent hour is in bed, not asleep)."""
    sessions = parse_payload(two_segments(60), TZ, 60).sleep
    assert [(s.asleep_s, s.in_bed_s) for s in sessions] == [(14400, 18000)]


def test_a_gap_one_minute_over_the_threshold_splits_the_session():
    """Gap 61 min > 60: two sessions of 7200 s each, both waking on 09-23."""
    sessions = parse_payload(two_segments(61), TZ, 60).sleep
    assert [(s.wake_day_local, s.asleep_s, s.in_bed_s) for s in sessions] == [
        ("2026-09-23", 7200, 7200),
        ("2026-09-23", 7200, 7200),
    ]


def test_the_configured_gap_reaches_the_parser(client, db, settings):
    """Through POST /ingest/health with the shipped config (60): the 61-minute gap splits."""
    assert settings.ingest.sleep_gap_min == 60
    post(client, json.dumps(two_segments(61)).encode())
    assert count(db, "sleep_sessions") == 2
