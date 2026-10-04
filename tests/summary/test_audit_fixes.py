"""The 2026-10-04 audit of the daily summary, one test group per finding. Every line below
was verified by execution to behave the wrong way before its fix."""

from __future__ import annotations

import httpx
import pytest

from app.summary import fallback as fb
from app.summary import gate, run
from app.summary.gate import Recent, check_ban, check_device_line, check_grounding, check_names
from app.summary.payload import LENSES, Payload
from app.summary.run import split_reply, write_summary
from tests.summary.conftest import FakeClient, golden_cases, payload_for, reply, seed
from tests.summary.test_run import GOOD, NOW, SHOWN, assert_fell_back

TH = 0.5
UNDER_BAR = {
    "quality_workout": False,
    "workout_count": 1,
    "workout_load": 62.4,
    "sleep_hours": 7.4,
    "steps": 8412,
    "books_ytd": 3,
}


def with_lens(payload: Payload, lens: str) -> Payload:
    return Payload(payload.day_local, lens, payload.cell, dict(payload.data, lens=lens))


def case_zero(db, settings, **weekly) -> Payload:
    case = dict(golden_cases()[0])
    case["weekly_metrics"] = {**case["weekly_metrics"], **weekly}
    return payload_for(db, settings, case)


# 1. Any failure of the model call ends in stored rule-based copy.


def validation_error() -> Exception:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return run.anthropic.APIResponseValidationError(
        response=httpx.Response(200, request=request), body=None
    )


def base_api_error() -> Exception:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return run.anthropic.APIError("boom", request, body=None)


@pytest.mark.parametrize(
    "error",
    [
        validation_error(),
        base_api_error(),
        httpx.ReadTimeout("slow"),
        TypeError("unexpected keyword"),
        ValueError("bad body"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_any_exception_from_the_call_falls_back_and_never_blanks(db, settings, error):
    seed(db, golden_cases()[0])
    result = write_summary(db, settings, SHOWN, client=FakeClient(error), now=NOW)
    assert_fell_back(db, result)
    assert result.attempts[0]["result"].startswith("unavailable:")
    assert type(error).__name__ in result.attempts[0]["result"]
    assert db.execute("SELECT COUNT(*) FROM model_spend").fetchone()[0] == 0


def test_a_response_that_cannot_be_read_falls_back(db, settings):
    seed(db, golden_cases()[0])
    broken = reply(GOOD)
    broken.content = [type("Block", (), {"type": "text"})()]
    result = write_summary(db, settings, SHOWN, client=FakeClient(broken), now=NOW)
    assert_fell_back(db, result)
    assert result.attempts[0]["result"] == "unavailable: unexpected error: AttributeError"


# 3. A workout under the bar is a workout, plainly, with no dot.


def test_workout_under_the_bar_is_never_called_no_workout(db, settings):
    case = dict(golden_cases()[0], daily_metrics=UNDER_BAR)
    payload = payload_for(db, settings, case)
    assert payload.cell.completeness == "all-sources"
    fact = fb.fact_clause(payload)
    assert fact == "One workout yesterday, load 62, under the bar, no dot."
    assert check_grounding(fact, payload) == []
    for lens in LENSES:
        lensed = with_lens(payload, lens)
        lines = fb.candidates(lensed)
        assert lines, lens
        for line in lines:
            assert "no workout" not in line.lower(), line
            thought = line.removeprefix(fact).strip()
            assert thought in fb.THOUGHTS[lens]["nodot"], thought
            assert thought not in fb.RECORD_ONLY_THOUGHTS, thought
            assert check_device_line(line, lensed, Recent(), TH).ok, line
        assert not fb.fallback_line(lensed, Recent(), TH).last_resort


def test_under_the_bar_fact_cites_only_payload_numbers_and_fits_when_travelling(db, settings):
    metrics = dict(UNDER_BAR, workout_count=2, workout_load=1180.0, day_flags=["travel"])
    payload = payload_for(db, settings, dict(golden_cases()[0], daily_metrics=metrics))
    fact = fb.fact_clause(payload)
    assert fact == "Two workouts yesterday, load 1180, under the bar, no dot."
    assert "Away from home, two workouts yesterday, load 1180, no dot." in fb.fact_options(payload)
    assert len(fact) <= fb.FACT_MAX
    assert check_grounding(fact, payload) == []


def test_a_rest_day_still_says_no_workout(db, settings):
    payload = payload_for(db, settings, golden_cases()[1])
    assert fb.fact_clause(payload).startswith("No workout yesterday")


# 4. Grounding: number words, glued digits, bare decimals, odd numerals.

UNGROUNDED = [
    "Five this week, load 118.",
    "Thirty workouts on",
    "A hundred dots later",
    "zero dots left",
    "forty minutes of it hard",
    "eighty percent of the week done",
    "Twice this week",
    "x2 last week",
    ".5 h more sleep",
    "7½ h of sleep",
    "Load 118²",
    "1st of many",
    "Two of three dots",
    "Twenty-one days on, load 118.",
    "Half the week done, load 118.",
    "A dozen dots, load 118.",
    "Version 1.2.3 of the week, load 118.",
]


@pytest.mark.parametrize("line", UNGROUNDED)
def test_ungrounded_number_forms_fail_grounding(db, settings, line):
    payload = payload_for(db, settings, golden_cases()[0])
    verdict = check_device_line(line, payload, Recent(), TH)
    assert verdict.reason.startswith("grounding:"), (line, verdict.reason)


def test_grounded_lines_the_old_gate_rejected_now_pass(db, settings):
    payload = case_zero(db, settings, load_bar=100.0)
    assert payload.data["targets"]["load_bar"] == 100
    for line in (
        "Third dot, load 118, week hit.",
        "Load 118 against a bar of 100",
        "Three of three dots, load 118.",
        "Four weeks running, 7.4 h of sleep.",
        "One step at a time, load 118.",
        "At first it was slow; load 118 by the end.",
    ):
        assert check_device_line(line, payload, Recent(), TH).ok, line


def test_load_bar_is_grounded_only_when_the_week_row_carries_it(db, settings):
    payload = payload_for(db, settings, golden_cases()[0])
    assert payload.data["targets"]["load_bar"] is None
    verdict = check_device_line("Load 118 against a bar of 100", payload, Recent(), TH)
    assert verdict.reason == "grounding: 100 not in payload"


def test_number_word_compounds_and_names_with_digits():
    assert gate.counted_numbers("two hundred and five steps") == [("two hundred five", 205.0, None)]
    assert gate.counted_numbers("twenty-first dot") == [("twenty first", 21.0, "dots")]
    assert gate.counted_numbers("one two three") == [("two", 2.0, None), ("three", 3.0, None)]
    assert gate.counted_numbers("a thousand miles") == [("thousand", 1000.0, None)]
    assert gate.number_tokens("VO2 and Z2 are names; x2 and No.5 are not") == ["2", ".5"]


# 5. Voice: general families, folded before matching.

ANXIOUS = [
    "Don't break your streak.",
    "Keep your streak alive.",
    "Protect the streak.",
    "Protecting this streak matters.",
    "The streak is on the line.",
    "Your streak's at stake.",
    "You missed a workout.",
    "Only five more.",
    "Only one workout left.",
    "Just one more to go.",
    "No more excuses.",
    "You could have done more.",
    "You gotta move.",
    "Try harder next time.",
    "Don’t break the chain.",
    "Ｄｏｎ't ｂｒｅａｋ the chain.",
    "Cru​shed it.",
    "Well done！",
]


@pytest.mark.parametrize("line", ANXIOUS)
def test_streak_anxiety_and_nagging_families_are_banned(line):
    assert check_ban(line), line


ATTRIBUTED = [
    "as marcus aurelius said, the day was yours.",
    "AS MARCUS AURELIUS SAID, the day was yours.",
    "As Marcus Aurelius noted, the day was yours.",
    "In the words of Seneca, the day was yours.",
    "To quote Aristotle, we are what we repeat.",
    "Seneca says the day was yours.",
    "Steve Jobs would call that focus.",
    "The Dalai Lama says begin again.",
    "Jane Doe would call that focus.",
    "Jane Doe once wrote about days like this.",
]


@pytest.mark.parametrize("line", ATTRIBUTED)
def test_attribution_to_a_named_person_is_rejected(line):
    assert check_names(line, ()), line


def test_an_ancient_source_paraphrased_once_and_plain_capitals_still_pass():
    for line in (
        "Epictetus would file the sleep under what was yours.",
        "Marcus Aurelius would file the sleep under what was yours.",
        "Marcus Aurelius had a note for mornings like this.",
        "Your HRV says the week landed.",
        "The Week said little, and that is fine.",
    ):
        assert check_names(line, ()) == [], line


# 6. A preamble is not the summary.


def test_split_reply_skips_a_label_line_and_filler():
    assert split_reply(f"Here is the line:\n{GOOD}") == (GOOD, None)
    assert split_reply(f"Sure.\nHere is the line:\n\n{GOOD}\nWeb.") == (GOOD, "Web.")
    assert split_reply(f'Here is the line: "{GOOD}"') == (GOOD, None)
    assert split_reply("Here is the line:") == ("", None)
    assert split_reply(GOOD) == (GOOD, None)
    assert split_reply("Here is a quiet week, load 118.") == (
        "Here is a quiet week, load 118.",
        None,
    )


def test_a_reply_with_a_preamble_stores_the_line_not_the_preamble(db, settings):
    seed(db, golden_cases()[0])
    client = FakeClient(reply(f"Here is the line:\n{GOOD}"))
    result = write_summary(db, settings, SHOWN, client=client, now=NOW)
    assert result.source == "model" and result.line == GOOD


def test_a_reply_that_is_only_a_preamble_is_rejected_and_retried(db, settings):
    seed(db, golden_cases()[0])
    client = FakeClient(reply("Here is the line:"), reply("Here is the line:"))
    result = write_summary(db, settings, SHOWN, client=client, now=NOW)
    assert_fell_back(db, result)
    assert [a["result"] for a in result.attempts[:2]] == ["length: empty line"] * 2


# 7. The client talks to api.anthropic.com whatever the environment says.


def test_client_base_url_is_pinned_against_the_environment(settings, monkeypatch):
    from dataclasses import replace

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://collector.example.net")
    client = run.make_client(replace(settings, anthropic_api_key="test-key-never-sent"))
    assert client.base_url.host == "api.anthropic.com"
    assert client.base_url.scheme == "https"
    client.close()
