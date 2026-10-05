"""Golden case 0 describes 2026-09-30; its line is shown and stored on SHOWN.

write_summary end to end with a fake client: request body, gate, regeneration, fallback,
idempotency, storage. No test here constructs the real client."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.config import SummaryConfig
from app.render.view_db import view_from_db
from app.summary import memory, run, spend
from app.summary.prompt import EFFORT, MAX_TOKENS, STABLE_SYSTEM_PROMPT
from app.summary.run import write_summary
from tests.summary.conftest import FakeClient, golden_cases, reply, sdk_error, seed

GOOD = "Third dot this week, load 118. A quiet week that did what it set out to do."
GOOD_WEB = GOOD + "\nThe 7.4 h of sleep is the part that makes tomorrow possible."
INVENTED = "Third dot this week, load 124. A quiet week that did what it set out to do."
NOW = datetime(2026, 10, 1, 10, 50, tzinfo=UTC)
SHOWN = "2026-10-01"


def case(index: int = 0) -> dict:
    return golden_cases()[index]


def assert_fell_back(db, result) -> None:
    """The display never blanks: a fallback is a real, stored, gate-passing line."""
    assert result.source == "fallback"
    assert result.line.strip(), "empty fallback line"
    assert stored_metrics(db, result.day_local)[run.DEVICE_KEY] == result.line
    assert view_from_db(db, settings_for(db), result.day_local).summary_line == result.line


def settings_for(db):
    from app.config import load_settings

    return load_settings()


def stored_metrics(db, day: str) -> dict:
    row = db.execute(
        "SELECT metrics_json FROM daily_metrics WHERE day_local = ?", (day,)
    ).fetchone()
    return json.loads(row["metrics_json"])


def test_model_line_that_passes_is_stored_with_every_other_metrics_key_kept(db, settings):
    seed(db, case())
    db.execute(
        "INSERT INTO daily_metrics (day_local, metrics_json) VALUES (?, ?)",
        (SHOWN, json.dumps({"books_ytd": 3, "workout_count": 0, "claude_week_used_pct": 41.2})),
    )
    before = stored_metrics(db, SHOWN)
    client = FakeClient(reply(GOOD_WEB))

    result = write_summary(db, settings, SHOWN, client=client, now=NOW)

    assert result.source == "model" and result.called
    assert result.line == GOOD
    assert result.web_line == "The 7.4 h of sleep is the part that makes tomorrow possible."
    after = stored_metrics(db, SHOWN)
    assert after[run.DEVICE_KEY] == GOOD
    assert after[run.WEB_KEY] == result.web_line
    assert after[run.SOURCE_KEY] == "model"
    assert {k: v for k, v in after.items() if not k.startswith("summary_")} == before
    assert view_from_db(db, settings, SHOWN).summary_line == GOOD
    assert memory.stored_line(db, SHOWN).source == "model"


def test_request_body_follows_the_api_contract(db, settings):
    seed(db, case())
    client = FakeClient(reply(GOOD))
    write_summary(db, settings, SHOWN, client=client, now=NOW)
    [request] = client.requests

    assert request["model"] == settings.summary.model == "claude-opus-5-5"
    assert request["max_tokens"] == MAX_TOKENS
    assert request["output_config"] == {"effort": EFFORT}
    assert "thinking" not in request and "temperature" not in request
    assert request["system"] == [
        {"type": "text", "text": STABLE_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
    ]
    assert [m["role"] for m in request["messages"]] == ["user"]
    body = json.dumps(request)
    assert "118" in body and "7.4" in body
    assert "Lens for today: " in body


def test_request_never_carries_raw_samples_or_sub_day_timestamps(db, settings, client):
    from tests.conftest import post_fixture

    post_fixture(client, "batch_part1.json")
    post_fixture(client, "metrics_v2_days.json")
    assert db.execute("SELECT COUNT(*) FROM workout_hr_samples").fetchone()[0] > 0
    seed(db, case())
    fake = FakeClient(reply(GOOD))
    write_summary(db, settings, SHOWN, client=fake, now=NOW)
    body = json.dumps(fake.requests[0])

    assert not re.search(r"T\d\d:\d\d", body), "a sub-day timestamp reached the request"
    assert not re.search(r"\d\d:\d\d:\d\d", body)
    for key in ("heartRateData", "bpm", "ts_utc", "start_utc", "end_utc", "samples", "Avg"):
        assert key not in body, key


def test_system_prompt_is_byte_identical_across_days(db, settings):
    seed(db, case(0))
    seed(db, case(1))
    first = FakeClient(reply(GOOD))
    write_summary(db, settings, SHOWN, client=first, now=NOW)
    second = FakeClient(reply("7.4 h of sleep yesterday. The day did its own quiet work."))
    write_summary(db, settings, "2026-10-02", client=second, now=NOW)
    assert first.requests[0]["system"] == second.requests[0]["system"]
    assert first.requests[0]["messages"] != second.requests[0]["messages"]


def test_gate_failure_regenerates_once_with_the_reason_then_falls_back(db, settings):
    seed(db, case())
    client = FakeClient(reply(INVENTED), reply(INVENTED))

    result = write_summary(db, settings, SHOWN, client=client, now=NOW)

    assert len(client.requests) == 2
    retry = client.requests[1]["messages"][0]["content"]
    assert "rejected: grounding: 124 not in payload" in retry
    assert_fell_back(db, result)
    assert result.line.endswith(" Yesterday made the third dot this week, load 118.")
    assert [a["result"] for a in result.attempts[:2]] == ["grounding: 124 not in payload"] * 2
    assert stored_metrics(db, SHOWN)[run.DEVICE_KEY] == result.line


def test_regeneration_that_passes_is_used(db, settings):
    seed(db, case())
    client = FakeClient(reply(INVENTED), reply(GOOD))
    result = write_summary(db, settings, SHOWN, client=client, now=NOW)
    assert result.source == "model" and result.line == GOOD
    assert len(client.requests) == 2


@pytest.mark.parametrize("kind", ["rate", "auth", "server", "connection"])
def test_sdk_errors_fall_back_and_never_blank(db, settings, kind):
    seed(db, case())
    client = FakeClient(sdk_error(kind))
    result = write_summary(db, settings, SHOWN, client=client, now=NOW)
    assert_fell_back(db, result)
    assert result.line.strip()
    assert stored_metrics(db, SHOWN)[run.DEVICE_KEY] == result.line
    assert result.attempts[0]["result"].startswith("unavailable:")
    assert db.execute("SELECT COUNT(*) FROM model_spend").fetchone()[0] == 0


@pytest.mark.parametrize("stop", ["max_tokens", "refusal", "stop_sequence"])
def test_non_end_turn_stop_reason_falls_back_but_is_still_priced(db, settings, stop):
    seed(db, case())
    client = FakeClient(reply(GOOD, stop_reason=stop))
    result = write_summary(db, settings, SHOWN, client=client, now=NOW)
    assert_fell_back(db, result)
    assert result.attempts[0]["result"] == f"unavailable: stop_reason {stop}"
    assert db.execute("SELECT stop_reason FROM model_spend").fetchone()[0] == stop


def test_no_api_key_means_fallback_without_a_call(db, settings, monkeypatch):
    seed(db, case())
    assert settings.anthropic_api_key == ""
    monkeypatch.setattr(run.anthropic, "Anthropic", lambda **kw: pytest.fail("real client built"))
    result = write_summary(db, settings, SHOWN, now=NOW)
    assert_fell_back(db, result)
    assert not result.called
    assert result.attempts[0]["result"] == "unavailable: no api key"
    assert view_from_db(db, settings, SHOWN).summary_line == result.line


def test_rerun_is_idempotent_and_force_calls_again(db, settings):
    seed(db, case())
    client = FakeClient(reply(GOOD))
    first = write_summary(db, settings, SHOWN, client=client, now=NOW)
    again = write_summary(db, settings, SHOWN, client=client, now=NOW)
    assert again.source == "stored" and again.line == first.line and not again.called
    assert len(client.requests) == 1

    forced_client = FakeClient(reply("Load 118 today, third dot. Nothing to add and nothing owed."))
    forced = write_summary(db, settings, SHOWN, client=forced_client, force=True, now=NOW)
    assert forced.called and forced.source == "model"
    assert len(forced_client.requests) == 1
    assert db.execute("SELECT COUNT(*) FROM model_spend").fetchone()[0] == 2


def test_spend_row_records_usage_request_id_and_price(db, settings):
    seed(db, case())
    client = FakeClient(
        reply(GOOD, input_tokens=200, output_tokens=50, cache_read=1000, cache_creation=0)
    )
    result = write_summary(db, settings, SHOWN, client=client, now=NOW)
    row = db.execute("SELECT * FROM model_spend").fetchone()
    expected = (200 * 4.0 + 50 * 20.0 + 1000 * 0.20) / 1_000_000
    assert row["request_id"] == "req_test_1"
    assert row["model"] == "claude-opus-5-5"
    assert (row["input_tokens"], row["output_tokens"], row["cache_read_tokens"]) == (200, 50, 1000)
    assert row["usd"] == pytest.approx(expected) == pytest.approx(0.002)
    assert result.spend_usd == pytest.approx(expected)
    assert row["month_local"] == "2026-10"


def test_cap_check_blocks_the_call_before_it_is_made(db, settings):
    seed(db, case())
    tight = replace(settings, summary=replace(settings.summary, monthly_cap_usd=0.01))
    client = FakeClient(reply(GOOD))
    result = write_summary(db, tight, SHOWN, client=client, now=NOW)
    assert client.requests == [], "the call was made despite the cap"
    assert_fell_back(db, result)
    assert result.attempts[0]["result"].startswith("cap: 2026-10: month-to-date $0.0000")


def test_cap_counts_this_month_only(db, settings):
    seed(db, case())
    spend.record(
        db,
        settings,
        "2026-09-30",
        "req_old",
        spend.Usage(1, 1),
        "end_turn",
        datetime(2026, 9, 30, 23, 0, tzinfo=UTC),
    )
    db.execute("UPDATE model_spend SET usd = 2.999")
    client = FakeClient(reply(GOOD))
    result = write_summary(db, settings, SHOWN, client=client, now=NOW)
    assert result.source == "model", result.attempts
    october = spend.month_to_date(db, "2026-10")
    assert 0 < october < 0.03


def test_cap_counts_month_to_date_including_earlier_calls_this_month(db, settings):
    seed(db, case())
    spend.record(db, settings, "2026-10-01", "req_a", spend.Usage(1, 1), "end_turn", NOW)
    db.execute("UPDATE model_spend SET usd = 2.995")
    client = FakeClient(reply(GOOD))
    result = write_summary(db, settings, SHOWN, client=client, now=NOW)
    assert client.requests == []
    assert_fell_back(db, result)


def test_worst_case_arithmetic_at_the_pinned_prices(settings):
    request = {
        "system": "s" * 3000,
        "messages": [{"role": "user", "content": "u" * 1500}],
        "max_tokens": 300,
    }
    tokens = spend.estimate_input_tokens(request)
    chars = 3000 + 2 + 1500 + len('[{"role": "user", "content": ""}]')
    assert tokens == int(chars / spend.CHARS_PER_TOKEN) + 1 == int(chars / 2.0) + 1
    usd = spend.worst_case_usd(settings, request)
    assert usd == pytest.approx((tokens * 5.0 + 300 * 20.0) / 1_000_000)
    config = SummaryConfig("m", 3.0)
    assert (config.price_input_per_mtok, config.price_output_per_mtok) == (4.0, 20.0)


def test_recent_lines_feed_the_prompt_and_the_gate(db, settings):
    for index in range(2):
        seed(db, case(index))
    memory.remember(
        db,
        "2026-09-30",
        "Second dot, load 110. A line from yesterday.",
        None,
        "habit",
        "model",
        "pass",
        [],
    )
    client = FakeClient(reply(GOOD))
    write_summary(db, settings, SHOWN, client=client, now=NOW)
    content = client.requests[0]["messages"][0]["content"]
    assert "- Second dot, load 110. A line from yesterday." in content
    assert "Named in the last 14 lines, so not offered today: none." in content


def test_morning_run_describes_yesterdays_dot_in_the_fallback(db, settings):
    seed(db, case(0))
    db.execute(
        "INSERT INTO daily_metrics (day_local, metrics_json) VALUES (?, ?)",
        (SHOWN, json.dumps({"quality_workout": False, "workout_count": 0, "books_ytd": 3})),
    )
    result = write_summary(db, settings, SHOWN, now=NOW)
    assert_fell_back(db, result)
    assert result.line.endswith(" Yesterday made the third dot this week, load 118.")
    assert not result.line.startswith("Yesterday"), "the thought leads"
