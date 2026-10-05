"""The gate against the golden cases: the ten hand-written lines pass, the hated lines fail
with the named reason, and each rule has its own red case. The quotation rules have their
own file, test_voice.py."""

from __future__ import annotations

import pytest

from app.summary import gate
from app.summary.gate import Recent, check_device_line, check_web_line, similarity
from tests.summary.conftest import golden_cases, hated_cases, payload_for

THRESHOLD = 0.5
EMPTY = Recent()


@pytest.mark.parametrize("case", golden_cases(), ids=[c["cell"] for c in golden_cases()])
def test_hand_written_lines_pass_against_their_payload(db, settings, case):
    payload = payload_for(db, settings, case)
    expected = case["cell"].split(" (")[0]
    assert payload.cell.name == expected, payload.cell
    verdict = check_device_line(case["line"], payload, EMPTY, THRESHOLD)
    assert verdict.ok, verdict.reasons


@pytest.mark.parametrize("case", hated_cases(), ids=[c["line"][:40] for c in hated_cases()])
def test_hated_lines_fail_with_the_named_reason(db, settings, case):
    golden = golden_cases()[case.get("case", 0)]
    payload = payload_for(db, settings, golden)
    verdict = check_device_line(case["line"], payload, EMPTY, THRESHOLD)
    assert not verdict.ok, case["line"]
    assert verdict.reason.startswith(case["reason"]), verdict.reason


def test_invented_number_fails_grounding_and_the_same_line_with_the_payload_number_passes(
    db, settings
):
    payload = payload_for(db, settings, golden_cases()[0])
    invented = check_device_line("Third dot this week, load 119.", payload, EMPTY, THRESHOLD)
    real = check_device_line("Third dot this week, load 118.", payload, EMPTY, THRESHOLD)
    assert invented.reason == "grounding: 119 not in payload"
    assert real.ok


def test_grounding_formatting_rules(db, settings):
    payload = payload_for(db, settings, golden_cases()[0])
    numbers = payload.numbers()
    assert {118.0, 7.4, 3.0, 4.0, 12.0, 7.0, 8412.0} <= numbers
    assert gate.ungrounded_numbers("load 118.0 and 118", numbers) == []
    assert gate.ungrounded_numbers("7 h target, 7.4 h slept", numbers) == []
    assert gate.ungrounded_numbers("8,412 steps", numbers) == []
    assert gate.ungrounded_numbers("7.40 h", numbers) == []
    assert gate.ungrounded_numbers("7.5 h", numbers) == ["7.5"]
    assert gate.ungrounded_numbers("118.5", numbers) == ["118.5"]
    assert gate.number_tokens("VO2 and Z2 are not numbers; 1.3 is") == ["1.3"]


def test_a_person_is_named_only_as_the_author_of_a_bank_quote(db, settings):
    """Was: an ancient source may be paraphrased by name once in seven days. The 2026-10-04
    voice lets a name appear only beside that person's own words from the bank."""
    payload = payload_for(db, settings, golden_cases()[1])
    for line in (
        "A night of 7.4 h of sleep. Epictetus would file the sleep under what was yours.",
        "A night of 7.4 h of sleep. Marcus Aurelius had a note for mornings like this.",
    ):
        verdict = check_device_line(line, payload, EMPTY, THRESHOLD)
        assert verdict.reason.startswith("names: "), line
        assert "named without a verbatim quote-bank entry" in verdict.reason
    quoted = '"Of things some are in our power, and others are not." - Epictetus'
    assert check_device_line(quoted, payload, EMPTY, THRESHOLD).ok


def test_words_put_in_a_named_mouth_are_rejected_even_once(db, settings):
    """Was: every attributed quote is rejected. Now: every attribution that is not a bank
    entry under its own author is."""
    payload = payload_for(db, settings, golden_cases()[1])
    for line, reason in (
        ("As Marcus Aurelius said, 7.4 h is enough.", "names: 'marcus aurelius' named without"),
        (
            "A night of 7.4 h of sleep. As Seneca put it, the day was yours.",
            "names: 'seneca' named without",
        ),
        ("As David Goggins says, 7.4 h is soft.", "names: attributed quote"),
        ("As my old coach said, 7.4 h is enough.", "names: attributed quote"),
    ):
        verdict = check_device_line(line, payload, EMPTY, THRESHOLD)
        assert verdict.reason.startswith(reason), (line, verdict.reason)


def test_opening_three_words_may_not_repeat_a_recent_line(db, settings):
    payload = payload_for(db, settings, golden_cases()[0])
    recent = Recent(opening_lines=("Third dot this week, load 118. Something else entirely.",))
    verdict = check_device_line(
        "Third dot this week, 7.4 h of sleep and nothing to add.", payload, recent, THRESHOLD
    )
    assert verdict.reason == "opening: 'third dot this' opened a recent line"
    assert check_device_line(
        "Load 118, the third dot. Nothing to add.", payload, recent, THRESHOLD
    ).ok


def test_similarity_gate_rejects_a_near_copy_and_passes_a_fresh_line(db, settings):
    payload = payload_for(db, settings, golden_cases()[0])
    previous = "Third dot this week, load 118. What you repeat is what you become."
    near = "The third dot this week, load 118. What you repeat is what you become, again."
    fresh = "Load 118 today. A quiet week that did what it set out to do."
    recent = Recent(similarity_lines=(previous,))
    assert similarity(near, previous) > THRESHOLD
    assert similarity(fresh, previous) < THRESHOLD
    assert check_device_line(near, payload, recent, THRESHOLD).reason.startswith("similarity:")
    assert check_device_line(fresh, payload, recent, THRESHOLD).ok


def test_similarity_threshold_comes_from_the_caller(db, settings):
    payload = payload_for(db, settings, golden_cases()[0])
    previous = "Third dot this week, load 118. What you repeat is what you become."
    near = "The third dot this week, load 118. What you repeat is what you become, again."
    recent = Recent(similarity_lines=(previous,))
    assert not check_device_line(near, payload, recent, 0.5).ok
    assert check_device_line(near, payload, recent, 0.99).ok


def test_similarity_only_looks_at_the_last_thirty_lines(db, settings):
    payload = payload_for(db, settings, golden_cases()[0])
    old = "Third dot this week, load 118. What you repeat is what you become."
    lines = tuple(f"Line number {i} says little, load 118." for i in range(30)) + (old,)
    recent = Recent(similarity_lines=lines)
    assert check_device_line(old, payload, recent, THRESHOLD).ok


def test_wellness_fact_present_allows_one_wellness_mention(db, settings):
    case = dict(golden_cases()[0])
    case["daily_metrics"] = {
        **case["daily_metrics"],
        "wellness_fact": {
            "metric": "hrv_ms",
            "value": 38.2,
            "baseline": 52.0,
            "direction": "below",
        },
    }
    payload = payload_for(db, settings, case)
    assert {38.0, 52.0} <= payload.numbers()
    verdict = check_device_line(
        "Third dot, load 118. HRV 38 against a 52 baseline; a fact, not a verdict.",
        payload,
        EMPTY,
        THRESHOLD,
    )
    assert verdict.ok, verdict.reasons


def test_web_line_is_gated_for_numbers_and_bans_but_not_length(db, settings):
    payload = payload_for(db, settings, golden_cases()[0])
    long_ok = "This second sentence is for the web page only, " * 4 + "and the load was 118."
    assert check_web_line(long_ok, payload, EMPTY).ok
    assert check_web_line(
        "Load 118, and 200 steps more than you think.", payload, EMPTY
    ).reason == ("grounding: 200 not in payload")
    assert check_web_line("Load 118. Crush tomorrow.", payload, EMPTY).reason == "ban: 'crush'"


def test_empty_line_fails_length(db, settings):
    payload = payload_for(db, settings, golden_cases()[0])
    assert check_device_line("", payload, EMPTY, THRESHOLD).reason == "length: empty line"
    assert check_device_line("   ", payload, EMPTY, THRESHOLD).reason == "length: empty line"
