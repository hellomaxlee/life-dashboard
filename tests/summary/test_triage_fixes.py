"""Ingrid's triage of the 2026-10-07 voice audit: the rewrite-once rule for a line authored
before the morning push, quote canonicalisation, the payload's key set, and an Anthropic
client that ignores proxy variables."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import anthropic

from app.summary import memory, run
from app.summary.payload import build_payload
from app.summary.run import write_summary
from tests.summary.conftest import FakeClient, golden_cases, reply, seed, shown_day

NOW = datetime(2026, 10, 8, 10, 50, tzinfo=UTC)
DELAYED = 5
EARLY = "Nothing on record yet. The day is still its own; let it arrive."
LATER = "A plain day, 7.6 h of sleep. Enough to stand on, nothing to prove."
AGAIN = "A quiet day with 7.6 h of sleep. The ground holds; no need to prove it."
BY = " - Marcus Aurelius"
QUOTE = '"The impediment to action advances action; what stands in the way becomes the way"' + BY
BANK = '"The impediment to action advances action. What stands in the way becomes the way."' + BY
ARRIVED = {"books_ytd": 3, "sleep_hours": 7.6, "steps": 8100, "workout_count": 0}
EXPECTED_KEYS = {
    "books.target",
    "books.ytd",
    "cell.completeness",
    "cell.day_type",
    "cell.inferred",
    "cell.season",
    "cell.streak_state",
    "day.book_finished",
    "day.book_title",
    "day.judged_workout",
    "day.manual_workout",
    "day.quality_workout",
    "day.sleep_hours",
    "day.sleep_win",
    "day.steps",
    "day.wins",
    "day.workout_count",
    "day.workout_load",
    "day.workout_minutes",
    "describes",
    "describes_relation",
    "lens",
    "load.acute",
    "load.balance",
    "load.chronic",
    "load.trimp",
    "shown_on",
    "targets.load_bar",
    "targets.sleep_hours",
    "week.dots_word",
    "week.previous_week_hit",
    "week.previous_week_quality_workouts",
    "week.quality_workouts",
    "week.relation",
    "week.target",
    "week.week_hit",
    "week.weeks_hit_streak",
    "weekday",
}


def arrive(db, described: str) -> None:
    db.execute(
        "UPDATE daily_metrics SET metrics_json = ? WHERE day_local = ?",
        (json.dumps(ARRIVED), described),
    )


def test_a_line_authored_before_the_push_is_rewritten_once_when_the_data_arrives(db, settings):
    case = golden_cases()[DELAYED]
    seed(db, case)
    shown = shown_day(case)
    assert build_payload(db, settings, shown).cell.completeness == "health-delayed"
    early = write_summary(db, settings, shown, client=FakeClient(reply(EARLY)), now=NOW)
    assert early.source == "model" and early.line == EARLY
    assert run.authored_completeness(memory.stored_line(db, shown).attempts) == "health-delayed"

    untouched = write_summary(db, settings, shown, client=FakeClient(reply(LATER)), now=NOW)
    assert untouched.source == "stored" and not untouched.called

    arrive(db, case["day"])
    assert build_payload(db, settings, shown).cell.completeness == "all-sources"
    client = FakeClient(reply(LATER), reply(AGAIN))
    rewritten = write_summary(db, settings, shown, client=client, now=NOW)
    assert rewritten.source == "model" and rewritten.called and rewritten.line == LATER
    assert len(client.requests) == 1
    stored = memory.stored_line(db, shown)
    assert stored.line == LATER
    rewrite = [a for a in stored.attempts if a["source"] == run.REWRITE_SOURCE]
    assert rewrite == [
        {"source": "rewrite", "superseded": EARLY, "result": "authored under health-delayed"}
    ]
    assert run.authored_completeness(stored.attempts) == "all-sources"
    assert EARLY in client.requests[0]["messages"][0]["content"]

    third = write_summary(db, settings, shown, client=client, now=NOW)
    assert third.source == "stored" and not third.called and third.line == LATER
    assert len(client.requests) == 1
    assert db.execute("SELECT COUNT(*) FROM model_spend").fetchone()[0] == 2


def test_a_failed_rewrite_keeps_the_old_line_and_is_not_retried(db, settings):
    case = golden_cases()[DELAYED]
    seed(db, case)
    shown = shown_day(case)
    write_summary(db, settings, shown, client=FakeClient(reply(EARLY)), now=NOW)
    arrive(db, case["day"])
    invented = "A plain day, 9.9 h of sleep. Enough to stand on, nothing to prove."
    client = FakeClient(reply(invented), reply(LATER), reply(LATER))
    kept = write_summary(db, settings, shown, client=client, now=NOW)
    assert kept.source == "stored" and kept.line == EARLY and kept.called
    assert len(client.requests) == 1
    again = write_summary(db, settings, shown, client=client, now=NOW)
    assert again.source == "stored" and not again.called and len(client.requests) == 1


def test_a_bank_quote_with_drifted_punctuation_is_stored_in_the_banks_words(db, settings):
    case = golden_cases()[0]
    seed(db, case)
    shown = shown_day(case)
    result = write_summary(db, settings, shown, client=FakeClient(reply(QUOTE)), now=NOW)
    assert result.source == "model"
    assert result.line == BANK
    assert memory.stored_line(db, shown).line == BANK
    row = db.execute(
        "SELECT metrics_json FROM daily_metrics WHERE day_local = ?", (shown,)
    ).fetchone()
    assert json.loads(row["metrics_json"])[run.DEVICE_KEY] == BANK


def key_paths(value, prefix: str = "") -> set[str]:
    if isinstance(value, dict):
        return {p for k, v in value.items() for p in key_paths(v, f"{prefix}{k}.")}
    return {prefix.rstrip(".")}


def test_payload_sends_exactly_the_expected_keys(db, settings):
    """Every key the model is sent, nested paths included, so a new one is a deliberate act."""
    for case in golden_cases():
        seed(db, case)
        payload = build_payload(db, settings, shown_day(case))
        assert key_paths(payload.data) == EXPECTED_KEYS, payload.cell.name


def test_the_anthropic_client_does_not_trust_proxy_variables(settings, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    client = run.make_client(replace(settings, anthropic_api_key="sk-test"))
    assert isinstance(client, anthropic.Anthropic)
    assert client._client.trust_env is False
    assert client.timeout == 30
