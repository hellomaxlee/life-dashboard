"""ensure_month_feature end to end with a fake streaming client. No test here constructs
the real client or dials anything."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import anthropic
import httpx
import pytest

from app.metrics.keys import DAILY_KEYS, WEEKLY_KEYS
from app.month import generate, store
from app.month.generate import FAILED, GENERATED, SKIPPED, STORED, ensure_month_feature
from app.month.prompt import EFFORT, MAX_TOKENS, SYSTEM_PROMPT
from app.summary import spend
from app.summary.run import SUMMARY_KEYS
from tests.conftest import count
from tests.month.conftest import FakeClient, feature_object, feature_text, reply

MONTH = "2026-10"
NOW = datetime(2026, 10, 4, 4, 20, tzinfo=UTC)
GOOD = feature_text(MONTH)
SHORT_A_DAY = json.dumps({**feature_object(MONTH), "days": feature_object(MONTH)["days"][:30]})


def with_note(text: str, day: int = 3) -> str:
    raw = feature_object(MONTH)
    raw["days"][day - 1]["note"] = text
    return json.dumps(raw)


def test_happy_path_stores_and_a_second_run_makes_no_call(db, keyed):
    client = FakeClient(reply(GOOD))

    first = ensure_month_feature(db, keyed, MONTH, client=client, now=NOW)

    assert (first.status, first.calls, first.reason) == (GENERATED, 1, "")
    assert first.feature.title == "Small Woods"
    row = db.execute("SELECT * FROM month_features").fetchone()
    assert row["month_local"] == MONTH and row["source"] == "model"
    assert row["model"] == keyed.summary.model
    assert row["raw_reply"] == GOOD
    assert json.loads(row["feature_json"]) == feature_object(MONTH)
    assert row["created_at_utc"] == "2026-10-04T04:20:00Z"
    assert store.load_feature(db, MONTH) == first.feature
    assert count(db, "month_feature_attempts") == 0

    again = ensure_month_feature(db, keyed, MONTH, client=client, now=NOW + timedelta(days=1))

    assert (again.status, again.calls, again.spend_usd) == (STORED, 0, 0.0)
    assert again.feature == first.feature
    assert len(client.requests) == 1
    assert count(db, "model_spend") == 1


def test_request_follows_the_api_contract_and_streams(db, keyed):
    client = FakeClient(reply(GOOD))
    ensure_month_feature(db, keyed, MONTH, client=client, now=NOW)
    [request] = client.requests

    assert request["model"] == keyed.summary.model
    assert request["max_tokens"] == MAX_TOKENS == 32000
    assert request["output_config"] == {"effort": EFFORT}
    assert "thinking" not in request and "temperature" not in request
    assert request["system"] == [
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
    ]
    assert [m["role"] for m in request["messages"]] == ["user"]
    assert "October 2026" in request["messages"][0]["content"]
    assert "exactly 31 entries" in request["messages"][0]["content"]


@pytest.mark.parametrize(
    "wrapped",
    [
        "```json\n" + GOOD + "\n```",
        "Here is October's feature, a walk in a {small} wood:\n\n" + GOOD + "\n\nEnjoy it.",
        "```\n" + GOOD + "\n```\nI kept every caption short.",
    ],
)
def test_a_fenced_or_prosey_reply_parses(db, keyed, wrapped):
    client = FakeClient(reply(wrapped))
    result = ensure_month_feature(db, keyed, MONTH, client=client, now=NOW)
    assert result.status == GENERATED
    assert json.loads(db.execute("SELECT feature_json FROM month_features").fetchone()[0]) == (
        feature_object(MONTH)
    )
    assert db.execute("SELECT raw_reply FROM month_features").fetchone()[0] == wrapped


def test_invalid_then_valid_is_two_calls_and_the_retry_names_the_reason(db, keyed):
    client = FakeClient(reply(SHORT_A_DAY), reply(GOOD, request_id="req_month_2"))

    result = ensure_month_feature(db, keyed, MONTH, client=client, now=NOW)

    assert (result.status, result.calls) == (GENERATED, 2)
    assert result.rejections == ["days: wants 31 entries"]
    first, second = (r["messages"][0]["content"] for r in client.requests)
    assert "rejected" not in first
    assert "Your last reply was rejected: days: wants 31 entries." in second
    assert client.requests[0]["system"] == client.requests[1]["system"]
    assert store.load_feature(db, MONTH) is not None
    assert count(db, "month_feature_attempts") == 0
    assert count(db, "model_spend") == 2


def test_invalid_twice_stores_nothing_and_the_same_day_makes_no_more_calls(db, keyed):
    client = FakeClient(reply(SHORT_A_DAY), reply("I would rather not."), reply(GOOD))

    result = ensure_month_feature(db, keyed, MONTH, client=client, now=NOW)

    assert (result.status, result.calls, result.feature) == (FAILED, 2, None)
    assert result.reason == "rejected: the reply holds no JSON object"
    assert count(db, "month_features") == 0
    [attempt] = store.attempts(db, MONTH)
    assert (attempt["day_local"], attempt["calls"]) == ("2026-10-04", 2)
    assert count(db, "model_spend") == 2

    later = ensure_month_feature(db, keyed, MONTH, client=client, now=NOW + timedelta(hours=3))

    assert (later.status, later.calls) == (SKIPPED, 0)
    assert "already tried on 2026-10-04" in later.reason
    assert len(client.requests) == 2
    assert count(db, "month_feature_attempts") == 1


def test_the_guard_allows_one_run_a_day_and_three_a_month_and_force_skips_it(db, keyed):
    client = FakeClient(*[reply("no", output_tokens=40) for _ in range(8)], reply(GOOD))
    for day in range(3):
        moment = NOW + timedelta(days=day)
        assert ensure_month_feature(db, keyed, MONTH, client=client, now=moment).status == FAILED
        assert ensure_month_feature(db, keyed, MONTH, client=client, now=moment).status == SKIPPED
    assert len(client.requests) == 6

    fourth = ensure_month_feature(db, keyed, MONTH, client=client, now=NOW + timedelta(days=3))
    assert fourth.status == SKIPPED and "3 failed runs for 2026-10" in fourth.reason
    assert len(client.requests) == 6

    forced = ensure_month_feature(
        db, keyed, MONTH, client=client, now=NOW + timedelta(days=3), force=True
    )
    assert (forced.status, forced.calls) == (FAILED, 2)
    forced = ensure_month_feature(
        db, keyed, MONTH, client=client, now=NOW + timedelta(days=3), force=True
    )
    assert (forced.status, forced.calls) == (GENERATED, 1)

    other_month = FakeClient(reply(feature_text("2026-11")))
    november = datetime(2026, 11, 1, 5, 20, tzinfo=UTC)
    assert ensure_month_feature(db, keyed, "2026-11", client=other_month, now=november).ok


def test_over_the_cap_makes_no_call_even_when_forced(db, keyed):
    capped = replace(keyed, summary=replace(keyed.summary, monthly_cap_usd=0.5))
    client = FakeClient(reply(GOOD))
    for force in (False, True):
        result = ensure_month_feature(db, capped, MONTH, client=client, now=NOW, force=force)
        assert (result.status, result.calls) == (FAILED, 0)
        assert result.reason.startswith("cap: 2026-10: month-to-date $0.0000 + worst case $0.6")
    assert client.requests == []
    assert count(db, "model_spend") == count(db, "month_feature_attempts") == 0


def test_the_cap_counts_what_the_month_already_spent(db, keyed):
    spend.record(db, keyed, "2026-10-02", "req_old", spend.Usage(output_tokens=120_000), "x", NOW)
    assert spend.month_to_date(db, MONTH) == pytest.approx(2.4)
    client = FakeClient(reply(GOOD))
    result = ensure_month_feature(db, keyed, MONTH, client=client, now=NOW)
    assert result.status == FAILED and "cap reached, no call" in result.reason
    assert client.requests == []


def test_the_cap_stops_the_retry_too(db, keyed):
    tight = replace(keyed, summary=replace(keyed.summary, monthly_cap_usd=0.9))
    client = FakeClient(reply(SHORT_A_DAY), reply(GOOD))
    result = ensure_month_feature(db, tight, MONTH, client=client, now=NOW)
    assert (result.status, result.calls) == (FAILED, 1)
    assert result.reason.startswith("cap: ")
    assert len(client.requests) == 1
    assert [row["calls"] for row in store.attempts(db, MONTH)] == [1]


def test_no_key_makes_no_call_and_leaves_no_attempt(db, settings, monkeypatch):
    assert settings.anthropic_api_key == ""
    monkeypatch.setattr(
        anthropic, "Anthropic", lambda **kw: pytest.fail("a client was built without a key")
    )
    result = ensure_month_feature(db, settings, MONTH, now=NOW)
    assert (result.status, result.calls, result.reason) == (FAILED, 0, "no api key")
    assert count(db, "month_features") == count(db, "month_feature_attempts") == 0
    assert count(db, "model_spend") == 0


def api_error(kind: str) -> BaseException:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    if kind == "connection":
        return anthropic.APIConnectionError(request=request)
    if kind == "timeout":
        return anthropic.APITimeoutError(request=request)
    status, cls = {
        "rate": (429, anthropic.RateLimitError),
        "auth": (401, anthropic.AuthenticationError),
        "server": (529, anthropic.APIStatusError),
    }[kind]
    return cls(kind, response=httpx.Response(status, request=request), body=None)


def raised_on_open(exc: BaseException) -> BaseException:
    exc.on_open = True
    return exc


@pytest.mark.parametrize(
    "error",
    [
        api_error("connection"),
        api_error("timeout"),
        api_error("rate"),
        api_error("auth"),
        api_error("server"),
        httpx.ReadTimeout("the stream went quiet"),
        ValueError("not a message"),
        raised_on_open(api_error("server")),
        raised_on_open(httpx.ConnectTimeout("no route")),
    ],
    ids=lambda e: type(e).__name__ + ("-open" if getattr(e, "on_open", False) else ""),
)
def test_a_raising_client_raises_nothing_and_stores_nothing(db, keyed, error):
    client = FakeClient(error, reply(GOOD))

    result = ensure_month_feature(db, keyed, MONTH, client=client, now=NOW)

    assert (result.status, result.calls, result.feature) == (FAILED, 0, None)
    assert result.reason.startswith("model unavailable: ")
    assert len(client.requests) == 1
    assert count(db, "month_features") == count(db, "model_spend") == 0
    assert [row["calls"] for row in store.attempts(db, MONTH)] == [1]


def test_a_failure_inside_the_run_is_returned_not_raised(db, keyed, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "save_feature", broken)
    result = ensure_month_feature(db, keyed, MONTH, client=FakeClient(reply(GOOD)), now=NOW)
    assert (result.status, result.reason) == (FAILED, "unexpected error: RuntimeError")
    assert ensure_month_feature(db, keyed, "October", now=NOW).reason.endswith("is not YYYY-MM")


def test_a_cut_off_reply_is_billed_recorded_and_not_retried(db, keyed):
    client = FakeClient(reply(GOOD[:400], stop_reason="max_tokens", output_tokens=32000))
    result = ensure_month_feature(db, keyed, MONTH, client=client, now=NOW)
    assert (result.status, result.calls) == (FAILED, 1)
    assert result.reason == "model unavailable: stop_reason max_tokens"
    assert result.spend_usd == pytest.approx((2400 * 4.0 + 32000 * 20.0) / 1e6)
    assert len(client.requests) == 1 and count(db, "month_features") == 0


def test_spend_is_recorded_for_every_billed_call_rejected_ones_included(db, keyed):
    client = FakeClient(
        reply(SHORT_A_DAY, output_tokens=14000, request_id="req_a"),
        reply(with_note("It should have rained."), output_tokens=15000, request_id="req_b"),
    )

    result = ensure_month_feature(db, keyed, MONTH, client=client, now=NOW)

    assert result.status == FAILED and result.calls == 2
    rows = db.execute("SELECT * FROM model_spend ORDER BY id").fetchall()
    assert [r["request_id"] for r in rows] == ["req_a", "req_b"]
    assert [r["output_tokens"] for r in rows] == [14000, 15000]
    assert {r["day_local"] for r in rows} == {"2026-10-04"}
    assert {r["month_local"] for r in rows} == {MONTH}
    expected = (2 * 2400 * 4.0 + 29000 * 20.0) / 1e6
    assert result.spend_usd == pytest.approx(expected)
    assert spend.month_to_date(db, MONTH) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("note", "names"),
    [
        ("Do not break the chain of oaks.", "day 3 note: ban: 'don't break the chain'"),
        ("It should have rained by now.", "day 3 note: ban: 'should have'"),
        ("You need to water this one.", "day 3 note: ban: 'you need to'"),
        ("A fine day for a fern!", "day 3 note: ban: exclamation mark"),
        ("As Seneca said, the wood waits.", "day 3 note: words attributed to a person"),
        ('"Wait," Seneca told the fern.', "day 3 note: words attributed to a person"),
    ],
)
def test_ban_list_wording_in_a_note_is_rejected(db, keyed, note, names):
    client = FakeClient(reply(with_note(note)), reply(GOOD))
    result = ensure_month_feature(db, keyed, MONTH, client=client, now=NOW)
    assert result.status == GENERATED and result.calls == 2
    assert result.rejections[0].startswith(names)
    assert f"rejected: {names}" in client.requests[1]["messages"][0]["content"]


def test_a_tradition_named_in_paraphrase_passes_and_a_digit_in_the_theme_does_not(db, keyed):
    stoic = with_note("The Stoics kept a door open for weather.")
    assert not isinstance(generate.judge(stoic, MONTH), str)
    dated = feature_text(MONTH, theme="A wood of 31 plants.")
    assert generate.judge(dated, MONTH) == "theme: digits are not allowed"
    caption = feature_object(MONTH)
    caption["days"][8]["caption"] = "Daily grind"
    assert generate.judge(json.dumps(caption), MONTH) == "day 9 caption: ban: 'grind'"


def seed_health(client) -> None:
    from tests.conftest import post_fixture

    post_fixture(client, "batch_part1.json")
    post_fixture(client, "metrics_v2_days.json")


def test_the_request_carries_no_health_data_and_no_stray_digits(db, keyed, client):
    from app.metrics.engine import recompute

    seed_health(client)
    recompute(db, keyed)
    db.execute(
        "INSERT INTO books (id, title, author, read_at, date_added) VALUES "
        "('b1', 'The Overstory', 'Richard Powers', '2026-09-20', '2026-09-01')"
    )
    assert count(db, "workout_hr_samples") > 0 and count(db, "daily_metrics") > 0
    fake = FakeClient(reply(GOOD))
    ensure_month_feature(db, keyed, MONTH, client=fake, now=NOW)
    [request] = fake.requests
    body = json.dumps(request)

    assert request["system"][0]["text"] == SYSTEM_PROMPT
    for key in sorted(DAILY_KEYS | WEEKLY_KEYS | SUMMARY_KEYS):
        assert key not in body, key
    for word in ("heartRate", "bpm", "sleep", "workout", "steps", "hrv", "Overstory", "Powers"):
        assert word.lower() not in body.lower(), word
    assert keyed.home_tz not in body and "New_York" not in body
    assert not re.search(r"\d\d:\d\d", body), "a time of day reached the request"
    message = request["messages"][0]["content"]
    assert set(re.findall(r"\d+", message)) == {"2026", "10", "31"}
    assert set(request) == {"model", "max_tokens", "system", "messages", "output_config"}


def test_the_system_prompt_is_static_text(db, keyed):
    first, second = FakeClient(reply(GOOD)), FakeClient(reply(feature_text("2027-02")))
    ensure_month_feature(db, keyed, MONTH, client=first, now=NOW)
    ensure_month_feature(db, keyed, "2027-02", client=second, now=NOW + timedelta(days=130))
    assert first.requests[0]["system"] == second.requests[0]["system"]
    assert "exactly 28 entries" in second.requests[0]["messages"][0]["content"]
    assert keyed.summary.model not in SYSTEM_PROMPT
    for month_name in ("October", "February", "2026", "2027"):
        assert month_name not in SYSTEM_PROMPT


def test_previous_themes_are_in_the_request_oldest_first(db, keyed):
    for month, title in (("2026-08", "Slow Tides"), ("2026-09", "Small Woods")):
        store.save_feature(db, month, {**feature_object(month), "title": title}, "model", "m", "")
    store.save_feature(db, "2026-11", feature_object("2026-11"), "model", "m", "")
    assert [(m, t) for m, t, _ in store.previous_themes(db, MONTH)] == [
        ("2026-08", "Slow Tides"),
        ("2026-09", "Small Woods"),
    ]
    assert [m for m, _, _ in store.previous_themes(db, MONTH, limit=1)] == ["2026-09"]

    client = FakeClient(reply(GOOD))
    ensure_month_feature(db, keyed, MONTH, client=client, now=NOW)
    message = client.requests[0]["messages"][0]["content"]

    theme = feature_object(MONTH)["theme"]
    assert f"- 2026-08: Slow Tides: {theme}\n- 2026-09: Small Woods: {theme}" in message
    assert "2026-11" not in message and "none yet" not in message

    empty = FakeClient(reply(feature_text("2026-07")))
    ensure_month_feature(db, keyed, "2026-07", client=empty, now=NOW)
    assert "- (none yet; this is the first)" in empty.requests[0]["messages"][0]["content"]


def test_force_replaces_a_stored_feature_and_a_failed_force_keeps_it(db, keyed):
    ensure_month_feature(db, keyed, MONTH, client=FakeClient(reply(GOOD)), now=NOW)
    failing = FakeClient(reply("no"), reply("no"))
    failed = ensure_month_feature(db, keyed, MONTH, client=failing, now=NOW, force=True)
    assert failed.status == FAILED
    assert store.load_feature(db, MONTH).title == "Small Woods"

    renamed = FakeClient(reply(feature_text(MONTH, title="Slow Tides")))
    forced = ensure_month_feature(db, keyed, MONTH, client=renamed, now=NOW, force=True)
    assert forced.status == GENERATED
    assert store.load_feature(db, MONTH).title == "Slow Tides"
    assert count(db, "month_features") == 1


def test_a_stored_row_that_no_longer_parses_loads_as_none_and_logs_once(db, keyed, caplog):
    ensure_month_feature(db, keyed, MONTH, client=FakeClient(reply(GOOD)), now=NOW)
    db.execute("UPDATE month_features SET feature_json = ?", (SHORT_A_DAY,))

    assert store.load_feature(db, MONTH) is None
    assert store.load_feature(db, MONTH) is None
    assert caplog.text.count("the stored row no longer parses: days: wants 31 entries") == 1
    assert store.previous_themes(db, "2026-11") == []

    db.execute("UPDATE month_features SET feature_json = '{not json'")
    assert store.load_feature(db, MONTH) is None
    with pytest.raises(generate.SpecError):
        store.save_feature(db, MONTH, json.loads(SHORT_A_DAY), "model", "m", "")


def test_the_real_client_is_pinned_to_the_api_host_and_built_only_with_a_key(keyed, settings):
    assert generate.make_client(settings) is None
    client = generate.make_client(keyed)
    assert str(client.base_url).rstrip("/") == "https://api.anthropic.com"
    assert client.max_retries == 0
    assert client.timeout == generate.STREAM_TIMEOUT_S
    assert client._client.trust_env is False
    assert hasattr(client.messages, "stream")


def test_the_brief_asks_for_months_that_differ_and_days_that_inform():
    from app.month import prompt

    for phrase in ("differ in concept", "sharp and informative", "do not invent", "a riddle never"):
        assert phrase in prompt.SYSTEM_PROMPT, phrase
