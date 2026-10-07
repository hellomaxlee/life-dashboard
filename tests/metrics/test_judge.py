"""The judged workout: a fake client, the engine's precedence, and the idempotence rules."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import anthropic
import httpx
import pytest

from app.config import Settings
from app.metrics import judge, manual
from app.metrics.engine import recompute
from app.metrics.job import judge_before
from app.metrics.judge_prompt import (
    PROMPT_NUMBERS,
    REASON_MAX,
    SYSTEM_PROMPT,
    ZONE_FLOORS,
    request_body,
)
from app.metrics.zones import zone_floors
from tests.metrics.golden import apply_inputs

NOW = datetime(2026, 10, 6, 15, 0, tzinfo=UTC)
DAY = "2026-10-06"


@dataclass
class FakeResponse:
    text: str
    stop_reason: str = "end_turn"
    usage: object = field(
        default_factory=lambda: type("U", (), {"input_tokens": 50, "output_tokens": 20})()
    )

    @property
    def content(self) -> list:
        """A thinking block first, as the live model returns, then the text block."""
        return [
            type("K", (), {"type": "thinking", "thinking": "", "text": ""})(),
            type("T", (), {"type": "text", "text": self.text})(),
        ]


class FakeMessages:
    def __init__(self, verdict: dict | str, stop_reason: str = "end_turn") -> None:
        self.verdict, self.calls, self.stop_reason = verdict, 0, stop_reason

    def create(self, **request: object) -> FakeResponse:
        self.calls += 1
        self.last = request
        text = self.verdict if isinstance(self.verdict, str) else json.dumps(self.verdict)
        return FakeResponse(text, self.stop_reason)


class FakeClient:
    def __init__(self, verdict: dict | str, stop_reason: str = "end_turn") -> None:
        self.messages = FakeMessages(verdict, stop_reason)


class DownClient:
    """An API that raises on every call."""

    class messages:
        calls = 0

        @classmethod
        def create(cls, **request: object) -> FakeResponse:
            cls.calls += 1
            raise anthropic.APIConnectionError(request=httpx.Request("POST", "http://x"))


def seed(db: sqlite3.Connection, hr_max: float = 160) -> None:
    for metric, value in (
        ("heart_rate_max", hr_max),
        ("heart_rate_avg", 88),
        ("resting_heart_rate", 60),
    ):
        db.execute(
            "INSERT OR REPLACE INTO wellness_daily (day_local, metric, value, units, source) "
            "VALUES (?, ?, ?, 'count/min', 'test')",
            (DAY, metric, value),
        )
    db.execute(
        "INSERT OR REPLACE INTO steps_daily (day_local, steps, source) VALUES (?, 9000, 'test')",
        (DAY,),
    )
    db.commit()


def day_row(db: sqlite3.Connection) -> dict:
    row = db.execute(
        "SELECT metrics_json FROM daily_metrics WHERE day_local = ?", (DAY,)
    ).fetchone()
    return json.loads(row["metrics_json"])


YES = {"workout": True, "confidence": 0.8, "reason": "max 160 with 9000 steps"}


def test_true_verdict_credits_the_day_once(db: sqlite3.Connection, settings: Settings) -> None:
    settings = replace(settings, judge_model="fake")
    seed(db)
    client = FakeClient(YES)
    judge.judge_window(db, settings, client, set(), NOW)
    recompute(db, settings, now=NOW)
    row = day_row(db)
    assert row["quality_workout"] and row["judged_workout"] and row["workout_count"] == 1
    assert row["judged_reason"] == YES["reason"]
    week = db.execute(
        "SELECT metrics_json FROM weekly_metrics ORDER BY week_start_local DESC"
    ).fetchone()
    assert json.loads(week["metrics_json"])["quality_workouts"] == 1
    assert client.messages.calls == 1


def test_same_inputs_never_judged_twice_but_a_new_max_is(
    db: sqlite3.Connection, settings: Settings
) -> None:
    settings = replace(settings, judge_model="fake")
    seed(db)
    client = FakeClient(YES)
    judge.judge_window(db, settings, client, set(), NOW)
    judge.judge_window(db, settings, client, set(), NOW)
    assert client.messages.calls == 1
    seed(db, hr_max=175)
    judge.judge_window(db, settings, client, set(), NOW)
    assert client.messages.calls == 2


def test_manual_override_takes_precedence_without_a_second_dot(
    db: sqlite3.Connection, settings: Settings
) -> None:
    settings = replace(settings, judge_model="fake")
    seed(db)
    manual.add_override(db, DAY, "by hand", NOW)
    judge.store(db, judge.Judgement(DAY, "yes", 0.9, "r", "fake", "h", "2026-10-06T15:00:00Z"))
    recompute(db, settings, now=NOW)
    row = day_row(db)
    assert row["manual_workout"] and not row["judged_workout"] and row["workout_count"] == 1


def test_false_verdict_and_ungrounded_reason_change_nothing(
    db: sqlite3.Connection, settings: Settings
) -> None:
    settings = replace(settings, judge_model="fake")
    seed(db)
    judge.judge_window(db, settings, FakeClient({**YES, "workout": False}), set(), NOW)
    recompute(db, settings, now=NOW)
    assert not day_row(db)["quality_workout"]
    seed(db, hr_max=170)
    bad = FakeClient({**YES, "reason": "ran 45 minutes at 150 bpm"})
    run = judge.judge_window(db, settings, bad, set(), NOW)
    assert run.verdicts == [] and run.failed == 1
    recompute(db, settings, now=NOW)
    assert not day_row(db)["quality_workout"]


def test_cap_blocks_the_call_and_payload_has_no_samples(
    db: sqlite3.Connection, settings: Settings
) -> None:
    settings = replace(settings, judge_model="fake")
    seed(db)
    client = FakeClient(YES)
    capped = replace(settings, summary=replace(settings.summary, monthly_cap_usd=0.0))
    judge.judge_window(db, capped, client, set(), NOW)
    assert client.messages.calls == 0
    judge.judge_window(db, settings, client, set(), NOW)
    body = json.dumps(client.messages.last)
    assert "samples" not in body and "T15:" not in body and ":00:" not in body


def test_grounding_tolerates_bare_zone_numbers_and_the_prompt_asks_for_words(
    db: sqlite3.Connection, settings: Settings
) -> None:
    inputs = {"heart_rate_max": 160.0, "steps": 9000}
    assert judge.grounded("max 160 into zone 3 with 9000 steps", inputs)
    assert judge.grounded("max 160 into zone three", inputs)
    assert not judge.grounded("about 45 minutes near zone 3", inputs)
    assert not judge.grounded("zone 6 effort", inputs)
    assert "zone three, never zone 3" in SYSTEM_PROMPT


def test_a_scored_day_pushed_since_the_last_recompute_is_never_judged(
    db: sqlite3.Connection, settings: Settings
) -> None:
    settings = replace(settings, judge_model="fake")
    seed(db)
    apply_inputs(
        db,
        {
            "activities": [
                {
                    "id": "L-06",
                    "type": "lift",
                    "start_local": "2026-10-06 07:00",
                    "minutes": 40,
                    "hr": [[40, 140]],
                }
            ]
        },
        settings,
    )
    client = FakeClient(YES)
    assert judge_before(db, settings, client).judged == 0
    assert client.messages.calls == 0
    recompute(db, settings, now=NOW)
    row = day_row(db)
    assert row["quality_workout"] and not row["judged_workout"] and row["workout_count"] == 1
    db.execute("DELETE FROM activities")
    db.execute("DELETE FROM workout_hr_samples")
    assert judge_before(db, settings, client).judged == 1
    assert client.messages.calls == 1


def test_judge_payload_is_day_level_aggregates_only(
    db: sqlite3.Connection, settings: Settings
) -> None:
    """Privacy: what reaches the model is the day's whole-day numbers under INPUT_KEYS,
    never a sample list, a sub-day timestamp, or a key outside the allowlist."""
    settings = replace(settings, judge_model="fake")
    seed(db)
    db.execute(
        "INSERT OR REPLACE INTO wellness_daily (day_local, metric, value, units, source) "
        "VALUES (?, 'blood_glucose', 95, 'mg/dL', 'test')",
        (DAY,),
    )
    client = FakeClient(YES)
    judge.judge_window(db, settings, client, set(), NOW)
    request = client.messages.last
    assert set(request) == {"model", "max_tokens", "system", "messages", "output_config"}
    assert len(request["messages"]) == 1
    text = request["messages"][0]["content"]
    assert isinstance(text, str)
    keys = [line.split(":", 1)[0] for line in text.splitlines()]
    assert keys == list(judge.INPUT_KEYS)
    assert "blood_glucose" not in text and "samples" not in json.dumps(request)
    assert not re.search(r"\d{2}:\d{2}", text) and "T" + DAY[:2] not in text
    assert not re.search(r"[\[{]", text)


def test_a_judged_yes_on_a_scored_day_is_one_dot_not_two(
    db: sqlite3.Connection, settings: Settings
) -> None:
    settings = replace(settings, judge_model="fake")
    seed(db)
    judge.store(db, judge.Judgement(DAY, "yes", 0.9, "r", "fake", "h", "2026-10-06T15:00:00Z"))
    apply_inputs(
        db,
        {
            "activities": [
                {
                    "id": "L-06",
                    "type": "lift",
                    "start_local": "2026-10-06 07:00",
                    "minutes": 40,
                    "hr": [[40, 140]],
                }
            ]
        },
        settings,
    )
    recompute(db, settings, now=NOW)
    row = day_row(db)
    assert row["quality_workout"] and not row["judged_workout"] and row["workout_count"] == 1
    assert row["workout_load"] is not None and row["workout_load"] >= row.get("load_bar", 0)
    week = db.execute(
        "SELECT metrics_json FROM weekly_metrics ORDER BY week_start_local DESC"
    ).fetchone()
    assert json.loads(week["metrics_json"])["quality_workouts"] == 1


def seed_minutes(db: sqlite3.Connection, start: datetime, bpms: list[float]) -> None:
    from datetime import timedelta

    for i, bpm in enumerate(bpms):
        minute = (start + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.execute(
            "INSERT OR REPLACE INTO hr_minutes (day_local, minute_utc, hr_min, hr_avg, hr_max) "
            "VALUES (?, ?, ?, ?, ?)",
            (DAY, minute, bpm - 3, bpm, bpm + 3),
        )


def test_bouts_describe_a_22_minute_effort_and_a_2_minute_spike_without_timestamps(
    db: sqlite3.Connection, settings: Settings
) -> None:
    settings = replace(settings, judge_model="fake")
    seed(db)
    quiet = [70.0] * 10
    seed_minutes(db, datetime(2026, 10, 6, 11, 0, tzinfo=UTC), quiet + [125.0] * 22 + quiet)
    seed_minutes(db, datetime(2026, 10, 6, 18, 0, tzinfo=UTC), quiet + [165.0] * 2 + quiet)
    inputs = judge.day_inputs(db, DAY, settings)
    assert inputs["hr_resolution"] == "minutes"
    assert inputs["bouts"] == [
        {
            "duration_min": 22,
            "avg_hr": 125,
            "peak_hr": 125,
            "z1": 0,
            "z2": 22,
            "z3": 0,
            "z4": 0,
            "z5": 0,
            "load": 44,
        },
        {
            "duration_min": 2,
            "avg_hr": 165,
            "peak_hr": 165,
            "z1": 0,
            "z2": 0,
            "z3": 0,
            "z4": 2,
            "z5": 0,
            "load": 8,
        },
    ]
    client = FakeClient(YES)
    judge.judge_window(db, settings, client, set(), NOW)
    request = client.messages.last
    text = request["messages"][0]["content"]
    assert "bout 1: duration_min 22" in text and "bout 2: duration_min 2" in text
    assert not re.search(r"\d{2}:\d{2}|T\d{2}|2026-10-06T", text)
    assert text.count("bout ") == 2 and "70" not in text.replace("steps: 9000", "")
    assert "fitness coach" in request["system"] and request["max_tokens"] <= 600


def test_gaps_of_three_minutes_join_a_bout_and_four_split_it(
    db: sqlite3.Connection, settings: Settings
) -> None:
    seed(db)
    seed_minutes(
        db, datetime(2026, 10, 6, 11, 0, tzinfo=UTC), [120.0] * 5 + [70.0] * 3 + [120.0] * 5
    )
    seed_minutes(
        db, datetime(2026, 10, 6, 15, 0, tzinfo=UTC), [120.0] * 5 + [70.0] * 4 + [120.0] * 5
    )
    bouts = judge.day_bouts(db, DAY, settings)
    assert [b["duration_min"] for b in bouts] == [13, 5, 5]


def test_a_later_no_never_revokes_a_yes(db: sqlite3.Connection, settings: Settings) -> None:
    settings = replace(settings, judge_model="fake")
    seed(db)
    judge.judge_window(db, settings, FakeClient(YES), set(), NOW)
    seed(db, hr_max=150)
    no = FakeClient({**YES, "workout": False, "reason": "resolution too low"})
    assert judge.judge_window(db, settings, no, set(), NOW).verdicts == []
    assert no.messages.calls == 1
    kept = judge.stored(db, DAY)
    assert kept.verdict == "yes" and kept.reason == YES["reason"]
    run = judge.judge_window(db, settings, no, set(), NOW)
    assert run.verdicts == [] and run.skipped == 1 and no.messages.calls == 1
    judge.deny(db, DAY, NOW)
    seed(db, hr_max=170)
    yes = FakeClient({**YES, "reason": "max 170 with 9000 steps"})
    judge.judge_window(db, settings, yes, set(), NOW)
    assert judge.stored(db, DAY).verdict == "yes"


def test_golden_bout_load_by_hand_and_the_zone_five_floor(
    db: sqlite3.Connection, settings: Settings
) -> None:
    """Floors at max 189: Z1 95, Z2 113, Z3 132, Z4 151, Z5 170. A 20-minute bout of
    5 min at 135 (Z3), 10 at 150 (Z3), 5 at 160 (Z4): load 15*3 + 5*4 = 65, avg 2975/20 = 149.
    A separate 2-minute spike at exactly 170 sits on the Z5 floor: load 2*5 = 10."""
    seed(db)
    quiet = [70.0] * 10
    effort = [135.0] * 5 + [150.0] * 10 + [160.0] * 5
    seed_minutes(db, datetime(2026, 10, 6, 11, 0, tzinfo=UTC), quiet + effort + quiet)
    seed_minutes(db, datetime(2026, 10, 6, 18, 0, tzinfo=UTC), quiet + [170.0] * 2 + quiet)
    assert judge.day_bouts(db, DAY, settings) == [
        {
            "duration_min": 20,
            "avg_hr": 149,
            "peak_hr": 160,
            "z1": 0,
            "z2": 0,
            "z3": 15,
            "z4": 5,
            "z5": 0,
            "load": 65,
        },
        {
            "duration_min": 2,
            "avg_hr": 170,
            "peak_hr": 170,
            "z1": 0,
            "z2": 0,
            "z3": 0,
            "z4": 0,
            "z5": 2,
            "load": 10,
        },
    ]


def test_golden_two_halves_two_minutes_apart_are_one_bout(
    db: sqlite3.Connection, settings: Settings
) -> None:
    """10 min at 150, 2 quiet minutes, 10 min at 150: one 22-minute bout whose zone minutes
    count only the 20 above the floor, load 20*3 = 60 (the load bar), avg 150."""
    seed(db)
    quiet = [70.0] * 10
    seed_minutes(
        db,
        datetime(2026, 10, 6, 11, 0, tzinfo=UTC),
        quiet + [150.0] * 10 + [70.0] * 2 + [150.0] * 10 + quiet,
    )
    assert judge.day_bouts(db, DAY, settings) == [
        {
            "duration_min": 22,
            "avg_hr": 150,
            "peak_hr": 150,
            "z1": 0,
            "z2": 0,
            "z3": 20,
            "z4": 0,
            "z5": 0,
            "load": 60,
        }
    ]


@pytest.mark.parametrize(
    ("client", "error"),
    [
        (FakeClient("not json"), "unparseable"),
        (FakeClient(json.dumps(YES) + "}"), "unparseable"),
        (FakeClient(json.dumps(YES) + "','x':''}"), "unparseable"),
        (FakeClient({**YES, "reason": "ran 45 minutes at 150 bpm"}), "ungrounded"),
        (FakeClient(YES, stop_reason="max_tokens"), "stop_reason max_tokens"),
        (FakeClient("", stop_reason="max_tokens"), "stop_reason max_tokens"),
    ],
)
def test_every_failed_call_is_stored_and_never_repeated_until_the_inputs_change(
    db: sqlite3.Connection, settings: Settings, client: object, error: str
) -> None:
    settings = replace(settings, judge_model="fake")
    seed(db)
    first = judge.judge_window(db, settings, client, set(), NOW)
    assert first.verdicts == [] and first.failed == 1 and client.messages.calls == 1
    row = judge.stored(db, DAY)
    assert row.verdict == "failed" and row.model == "fake" and row.error.startswith(error)
    assert row.reason == "" and row.confidence == 0.0
    again = judge.judge_window(db, settings, client, set(), NOW)
    assert again.skipped == 1 and again.failed == 0 and client.messages.calls == 1
    recompute(db, settings, now=NOW)
    assert not day_row(db)["quality_workout"] and not day_row(db)["judged_workout"]
    seed(db, hr_max=175)
    judge.judge_window(db, settings, client, set(), NOW)
    assert client.messages.calls == 2


def test_a_refused_cap_is_not_a_judgement_and_a_failure_never_revokes_a_yes(
    db: sqlite3.Connection, settings: Settings
) -> None:
    settings = replace(settings, judge_model="fake")
    seed(db)
    capped = replace(settings, summary=replace(settings.summary, monthly_cap_usd=0.0))
    judge.judge_window(db, capped, FakeClient(YES), set(), NOW)
    assert judge.stored(db, DAY) is None
    judge.judge_window(db, settings, FakeClient(YES), set(), NOW)
    seed(db, hr_max=175)
    judge.judge_window(db, settings, FakeClient("junk"), set(), NOW)
    kept = judge.stored(db, DAY)
    assert kept.verdict == "yes" and kept.error.startswith("unparseable")
    assert kept.inputs_hash == judge.inputs_hash(judge.day_inputs(db, DAY, settings))


def test_reader_skips_the_thinking_block_and_parses_strictly() -> None:
    good = FakeResponse(json.dumps(YES))
    assert good.content[0].type == "thinking" and good.content[1].type == "text"
    assert judge.parse_verdict(good) == (True, 0.8, YES["reason"])
    assert judge.parse_verdict(FakeResponse(json.dumps(YES) + "}")).startswith("unparseable")
    assert judge.parse_verdict(FakeResponse(json.dumps({**YES, "x": 1}))).startswith("unparse")
    assert judge.parse_verdict(FakeResponse(json.dumps({**YES, "workout": 1}))).startswith("unp")
    assert judge.parse_verdict(FakeResponse(json.dumps(YES), "max_tokens")) == (
        "stop_reason max_tokens"
    )


def test_a_stray_brace_inside_a_complete_reason_is_dropped_but_junk_after_the_json_is_not(
    db: sqlite3.Connection, settings: Settings
) -> None:
    settings = replace(settings, judge_model="fake")
    seed(db)
    tail = {**YES, "reason": "max 160 with 9000 steps.}"}
    run = judge.judge_window(db, settings, FakeClient(tail), set(), NOW)
    assert [j.reason for j in run.verdicts] == ["max 160 with 9000 steps."]
    assert judge.parse_verdict(FakeResponse(json.dumps(YES) + "}")).startswith("unparseable")
    assert judge.parse_verdict(FakeResponse(json.dumps({**YES, "reason": "a 'b'}\""}))) == (
        True,
        0.8,
        "a 'b",
    )


def test_haiku_gets_no_thinking_or_effort_fields_and_a_stray_byte_tail_is_dropped() -> None:
    request = request_body({"heart_rate_max": 160.0}, "claude-haiku-4-5")
    assert "thinking" not in request and "effort" not in request["output_config"]
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert judge.clean_reason("no sustained effort occurred.\u00c2 \u0102\u017b") == (
        "no sustained effort occurred."
    )


def test_request_turns_thinking_off_at_low_effort_with_the_schema() -> None:
    request = request_body({"heart_rate_max": 160.0, "bouts": []}, "claude-sonnet-5-5")
    assert request["thinking"] == {"type": "between_tools"}
    assert request["output_config"]["effort"] == "low"
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert request["max_tokens"] == 600


def test_the_prompt_and_the_gate_share_the_stated_constants(settings: Settings) -> None:
    inputs = {"heart_rate_max": 160.0, "steps": 9000}
    assert judge.grounded("peak 160 under the 60 bar after 30 minutes at max 189", inputs)
    assert judge.grounded("zone two is 113-131 and zone five starts at 170", inputs)
    assert not judge.grounded("about 45 minutes near zone three", inputs)
    assert PROMPT_NUMBERS == {
        "60",
        "189",
        "30",
        "10",
        "95",
        "112",
        "113",
        "131",
        "132",
        "150",
        "151",
        "169",
        "170",
    }
    assert all(n in SYSTEM_PROMPT for n in PROMPT_NUMBERS)
    assert ZONE_FLOORS == zone_floors(settings.hr_max, settings.zones_pct)


def test_minutes_resolution_needs_sixty_rows_whose_max_matches_the_day(
    db: sqlite3.Connection, settings: Settings
) -> None:
    seed(db, hr_max=157)
    seed_minutes(db, datetime(2026, 10, 6, 11, 0, tzinfo=UTC), [84.0, 154.0])
    inputs = judge.day_inputs(db, DAY, settings)
    assert inputs["hr_resolution"] == "daily" and inputs["bouts"] == []
    seed_minutes(db, datetime(2026, 10, 6, 12, 0, tzinfo=UTC), [120.0] * 57)
    inputs = judge.day_inputs(db, DAY, settings)
    assert inputs["hr_resolution"] == "daily" and inputs["bouts"] == []
    seed_minutes(db, datetime(2026, 10, 6, 14, 0, tzinfo=UTC), [120.0])
    inputs = judge.day_inputs(db, DAY, settings)
    assert inputs["hr_resolution"] == "minutes" and len(inputs["bouts"]) == 3
    seed(db, hr_max=180)
    inputs = judge.day_inputs(db, DAY, settings)
    assert inputs["hr_resolution"] == "daily" and inputs["bouts"] == []


def test_a_transient_api_error_stores_nothing_and_the_day_is_retried_next_run(
    db: sqlite3.Connection, settings: Settings
) -> None:
    """Bartek's audit, 2026-10-07: a connection error, 429 or 529 is not a verdict on the
    inputs; storing 'failed' under the hash would silence the day until its inputs changed."""
    settings = replace(settings, judge_model="fake")
    seed(db)
    down = DownClient()
    down.messages.calls = 0
    first = judge.judge_window(db, settings, down, set(), NOW)
    assert first.failed == 0 and first.judged == 0 and down.messages.calls == 1
    assert judge.stored(db, DAY) is None
    assert db.execute("SELECT COUNT(*) AS n FROM model_spend").fetchone()["n"] == 0
    judge.judge_window(db, settings, down, set(), NOW)
    assert down.messages.calls == 2
    run = judge.judge_window(db, settings, FakeClient(YES), set(), NOW)
    assert run.yes == 1 and judge.stored(db, DAY).credited


def test_grounding_never_reads_an_integer_as_a_zone_digit() -> None:
    inputs = {"heart_rate_max": 160.0, "heart_rate_avg": 88.0, "steps": 9000, "bouts": []}
    assert not judge.grounded("you did 300 minutes", inputs)
    assert not judge.grounded("load 40 above the bar", inputs)
    assert not judge.grounded("500 steps", inputs)
    assert judge.grounded("max 160 into zone 3", inputs)
    assert judge.grounded("45.0 peak", {"heart_rate_max": 45.0, "bouts": []})
    assert judge.grounded("avg 88.0 at max 160", inputs)


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ("a sustained session.}", "a sustained session."),
        ("no effort evident.}", "no effort evident."),
        ("peak 170.0.','x':''}", "peak 170.0."),
        ("fine.','x':", "fine."),
        ("max 160 with 9000 steps", "max 160 with 9000 steps"),
        ("max 160 with 9000 steps.", "max 160 with 9000 steps."),
        ("avg 170.0 and 'q'}", "avg 170.0 and 'q"),
    ],
)
def test_reason_tail_rule_cuts_at_the_last_sentence_end_when_json_follows(
    raw: str, cleaned: str
) -> None:
    assert judge.clean_reason(raw) == cleaned
    response = FakeResponse(json.dumps({**YES, "reason": raw}))
    assert judge.parse_verdict(response) == (True, 0.8, cleaned)


def test_an_over_long_reason_is_cut_at_the_last_sentence_end_not_mid_word() -> None:
    first = "Daily resolution only: peak 157.0 in zone four is not obvious enough to credit it."
    second = " Day too coarse to tell apart a sprint from a session, so no credit this time."
    assert len(first) <= REASON_MAX < len(first + second)
    assert judge.clean_reason(first + second) == first
    assert judge.clean_reason("x" * (REASON_MAX + 20)) == "x" * REASON_MAX, "no sentence: hard cut"
    assert judge.clean_reason(first) == first
    stored = (first + second)[:REASON_MAX]
    assert judge.clean_reason(stored) == first, "a row truncated before this rule is cleaned too"


def test_clean_stored_reasons_rewrites_reasons_only(db: sqlite3.Connection) -> None:
    judge.store(db, judge.Judgement(DAY, "yes", 0.8, "peak 170.0.','x':''}", "m", "h1", "t"))
    judge.store(db, judge.Judgement("2026-10-05", "no", 0.3, "clean.", "m", "h2", "t"))
    judge.store(db, judge.Judgement("2026-10-04", "denied", 0.9, "fine.}", "m", "h3", "t"))
    assert judge.clean_stored_reasons(db) == 2
    rows = {j.day_local: j for j in judge.list_judgements(db)}
    assert rows[DAY].reason == "peak 170.0." and rows[DAY].inputs_hash == "h1"
    assert rows["2026-10-05"].reason == "clean." and rows["2026-10-05"].verdict == "no"
    assert rows["2026-10-04"].reason == "fine." and rows["2026-10-04"].verdict == "denied"
    assert judge.clean_stored_reasons(db) == 0


def test_hash_covers_heart_rate_fields_only(db: sqlite3.Connection, settings: Settings) -> None:
    settings = replace(settings, judge_model="fake")
    seed(db)
    client = FakeClient(YES)
    judge.judge_window(db, settings, client, set(), NOW)
    assert client.messages.calls == 1
    db.execute("UPDATE steps_daily SET steps = 12000 WHERE day_local = ?", (DAY,))
    db.execute(
        "UPDATE wellness_daily SET value = 55 WHERE day_local = ? AND metric = ?",
        (DAY, "resting_heart_rate"),
    )
    db.commit()
    assert judge.day_inputs(db, DAY, settings)["steps"] == 12000
    assert judge.judge_window(db, settings, client, set(), NOW).skipped == 1
    assert client.messages.calls == 1
    seed(db, hr_max=175)
    judge.judge_window(db, settings, client, set(), NOW)
    assert client.messages.calls == 2


def test_judge_calls_are_priced_at_the_judge_model(
    db: sqlite3.Connection, settings: Settings
) -> None:
    settings = replace(settings, judge_model="claude-sonnet-5-5")
    seed(db)
    judge.judge_window(db, settings, FakeClient(YES), set(), NOW)
    row = db.execute("SELECT model, usd FROM model_spend").fetchone()
    assert row["model"] == "claude-sonnet-5-5"
    assert row["usd"] == pytest.approx((50 * 2.0 + 20 * 10.0) / 1_000_000)
    assert row["usd"] < (50 * 4.0 + 20 * 20.0) / 1_000_000


def test_prompt_states_the_ten_minute_floor_and_the_gate_accepts_it() -> None:
    assert "shorter than 10 minutes at or above zone two is not a workout" in SYSTEM_PROMPT
    assert "10" in PROMPT_NUMBERS
    assert judge.grounded("a 10 minute bout is under the floor", {"bouts": []})


def test_a_kept_yes_is_counted_as_a_call(db: sqlite3.Connection, settings: Settings) -> None:
    settings = replace(settings, judge_model="fake")
    seed(db)
    judge.judge_window(db, settings, FakeClient(YES), set(), NOW)
    seed(db, hr_max=175)
    later = {**YES, "workout": False, "reason": "max 175 is a spike"}
    run = judge.judge_window(db, settings, FakeClient(later), set(), NOW)
    assert run.kept == 1 and run.judged == 1 and run.verdicts == []
    assert "kept 1" in run.describe() and judge.stored(db, DAY).credited


def test_judge_day_under_haiku_writes_a_haiku_priced_ledger_row(
    db: sqlite3.Connection, settings: Settings
) -> None:
    settings = replace(settings, judge_model="claude-haiku-4-5")
    seed(db)
    client = FakeClient(YES)
    assert not isinstance(judge.judge_day(db, settings, client, DAY, NOW), str)
    rows = db.execute("SELECT model, usd FROM model_spend").fetchall()
    assert len(rows) == 1 and client.messages.last["model"] == "claude-haiku-4-5"
    assert rows[0]["model"] == "claude-haiku-4-5"
    assert rows[0]["usd"] == pytest.approx((50 * 1.0 + 20 * 5.0) / 1_000_000)


def test_prompt_says_no_at_daily_resolution_and_reason_is_for_max() -> None:
    from app.metrics.judge_prompt import DAILY_RESOLUTION_RULE, REASON_AUDIENCE_RULE

    assert DAILY_RESOLUTION_RULE in SYSTEM_PROMPT
    assert "credit waits for minute data or a recorded workout" in SYSTEM_PROMPT
    assert "credit the day only when a sustained effort is obvious" not in SYSTEM_PROMPT
    assert REASON_AUDIENCE_RULE in SYSTEM_PROMPT
    assert "read by Max on his dashboard" in SYSTEM_PROMPT
