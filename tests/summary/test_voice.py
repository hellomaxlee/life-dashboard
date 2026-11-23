"""The 2026-10-04 voice (Max): the thought leads, a number is optional, the line may run to
220 characters, and a direct quotation is welcome when it is a quote-bank entry under its
own author's name. Nothing else goes under a person's name."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from app.render.screens import SUMMARY_MAX_CHARS
from app.summary import fallback as fb
from app.summary import gate, memory, prompt, quotes
from app.summary.gate import DEVICE_MAX, Recent, check_device_line, check_names
from app.summary.payload import LENSES, Payload, lens_for
from app.summary.run import split_reply, write_summary
from tests.summary.conftest import FakeClient, golden_cases, payload_for, reply, seed
from tests.summary.test_run import NOW, SHOWN, assert_fell_back

TH = 0.5
EMPTY = Recent()
SENECA = '"We suffer more often in imagination than in reality." - Seneca'
EPICTETUS = '"Of things some are in our power, and others are not." - Epictetus'
EPICTETUS_2 = '"It is difficulties that show what men are." - Epictetus'
JOURNEY = '"A journey of a thousand miles begins with a single step." - Lao Tzu'
DECEASED = {
    "Marcus Aurelius": 180,
    "Epictetus": 135,
    "Seneca": 65,
    "Aristotle": -322,
    "The Dhammapada": -200,
    "Lao Tzu": -500,
    "Zhuangzi": -286,
    "Heraclitus": -475,
    "Confucius": -479,
    "Montaigne": 1592,
    "Thoreau": 1862,
    "William James": 1910,
    "Emerson": 1882,
}


def with_day(payload: Payload, day: date) -> Payload:
    lens = lens_for(day)
    return Payload(day.isoformat(), lens, payload.cell, dict(payload.data, lens=lens))


# 1. The bank.


def test_bank_is_large_unique_ascii_sourced_and_by_deceased_authors_only():
    assert len(quotes.QUOTES) >= 60
    assert len(gate.BANK) == len(quotes.QUOTES), "two entries normalise to the same words"
    assert set(quotes.AUTHOR_ALIASES) == set(DECEASED)
    for q in quotes.QUOTES:
        shown = quotes.display(q)
        assert q.author in DECEASED, q.author
        assert q.work.strip() and q.lenses and set(q.lenses) <= set(LENSES), q
        assert shown.isascii(), shown
        assert len(shown) <= DEVICE_MAX, (len(shown), shown)
        assert '"' not in q.text and q.text == q.text.strip()
    for lens in LENSES:
        assert len({q.author for q in quotes.for_lens(lens)}) >= 6, lens


@pytest.mark.parametrize("quote", quotes.QUOTES, ids=[q.work for q in quotes.QUOTES])
def test_every_bank_entry_passes_the_gate_as_displayed(db, settings, quote):
    shown = quotes.display(quote)
    for case in golden_cases():
        payload = payload_for(db, settings, case)
        verdict = check_device_line(shown, payload, EMPTY, TH)
        assert verdict.ok, (verdict.reason, shown, case["cell"])


def test_known_misattributions_are_not_in_the_bank_and_fail_the_gate():
    for text, author in (
        (
            "We are what we repeatedly do. Excellence, then, is not an act, but a habit.",
            "Aristotle",
        ),
        ("Luck is what happens when preparation meets opportunity.", "Seneca"),
        (
            "You have power over your mind - not outside events. Realize this, and you will "
            "find strength.",
            "Marcus Aurelius",
        ),
        ("It's not what happens to you, but how you react to it that matters.", "Epictetus"),
        ("Be kind, for everyone you meet is fighting a hard battle.", "Plato"),
        ("Peace comes from within. Do not seek it without.", "Buddha"),
        ("It does not matter how slowly you go as long as you do not stop.", "Confucius"),
        ("Life is a journey, not a destination.", "Emerson"),
    ):
        assert gate.bank_entry(text) is None, text
        for line in (f'"{text}" - {author}', f"{text} - {author}", f"As {author} said, {text}"):
            assert check_names(line, ()), line


# 2. A quotation passes only as a bank entry under its own author.


def test_a_bank_quote_with_its_author_passes_in_the_shapes_the_prompt_allows(db, settings):
    payload = payload_for(db, settings, golden_cases()[1])
    for line in (
        SENECA,
        SENECA + ". Yesterday asked nothing of you.",
        'Seneca: "We suffer more often in imagination than in reality."',
        "Yesterday's 7.4 h of sleep was the work. " + SENECA,
        "“We suffer more often in imagination than in reality.” — Seneca",
        '"we suffer  more often in imagination than in reality" - SENECA',
        '"We suffer more often in imagination, than in reality!" -- Seneca'.replace("!", ""),
        '"Lay hold of to-day’s task, and you will not need to depend so much upon '
        'to-morrow’s. While we are postponing, life speeds by." – Seneca',
        '"All that we are is the result of what we have thought: it is founded on our '
        'thoughts, it is made up of our thoughts." - The Dhammapada',
        '"It rests by changing." - Heraclitus',
    ):
        verdict = check_device_line(line, payload, EMPTY, TH)
        assert verdict.ok, (verdict.reason, line)


def test_a_bank_quote_under_the_wrong_author_is_rejected(db, settings):
    payload = payload_for(db, settings, golden_cases()[1])
    words = '"We suffer more often in imagination than in reality."'
    for author in ("Epictetus", "Marcus Aurelius", "Jane Doe", "Steve Jobs"):
        verdict = check_device_line(f"{words} - {author}", payload, EMPTY, TH)
        assert verdict.reason.startswith("names: that quotation is Seneca's in the bank"), author
    both = check_device_line(f"{words} - Seneca and Epictetus", payload, EMPTY, TH)
    assert both.reason == "names: 'epictetus' named beside a quotation by Seneca"
    bare = check_device_line(words, payload, EMPTY, TH)
    assert bare.reason == "names: that quotation is Seneca's in the bank; name its author"


@pytest.mark.parametrize(
    "line",
    [
        '"The day was yours, and so is the rest." - Seneca',
        '"We suffer more in imagination than in reality." - Seneca',
        '"We suffer more often in imagination than in reality, friend." - Seneca',
        '"We suffer more often in imagination" - Seneca',
        'As Marcus Aurelius said, "the day is what you make of it."',
        'as marcus aurelius said, "the day is what you make of it."',
        "Seneca: the day was yours.",
        "'We suffer more often in imagination than in reality.' - Seneca",
        SENECA + ', who also said "rest is training."',
        SENECA + ", who also said that rest is training.",
        SENECA + ' "It rests by changing." - Heraclitus',
        '"We suffer more often in imagination than in reality. - Seneca',
        'The "dot" is only a dot.',
    ],
)
def test_a_bank_author_with_words_not_in_the_bank_is_rejected(db, settings, line):
    payload = payload_for(db, settings, golden_cases()[1])
    verdict = check_device_line(line, payload, EMPTY, TH)
    assert verdict.reason.startswith("names: "), (line, verdict.reason)


@pytest.mark.parametrize(
    "line",
    [
        "Steve Jobs would call that focus.",
        "The Dalai Lama says begin again.",
        '"Stay hungry. Stay foolish." - Steve Jobs',
        '"Begin again." - The Dalai Lama',
        "Begin again, without drama. - Pema Chodron",
        "Begin again, without drama. - Jane Doe",
        "Begin again. Thich Nhat Hanh knew.",
        "Socrates had a word for this.",
        "The Buddha said begin again.",
        "Jane Doe would call that focus.",
    ],
)
def test_a_living_or_non_bank_person_is_rejected(db, settings, line):
    payload = payload_for(db, settings, golden_cases()[1])
    verdict = check_device_line(line, payload, EMPTY, TH)
    assert verdict.reason.startswith("names: "), (line, verdict.reason)


@pytest.mark.parametrize(
    "line",
    [
        "The Stoics kept a short list of what is yours; the effort was on it.",
        "An old Buddhist idea: the effort is yours, the result is not.",
        "Taoists speak of not forcing; yesterday you did not force.",
        "Stoic practice is mostly subtraction.",
        "The Aristotelian mean is a moving target, and you moved with it.",
    ],
)
def test_a_tradition_named_in_paraphrase_passes(db, settings, line):
    payload = payload_for(db, settings, golden_cases()[1])
    verdict = check_device_line(line, payload, EMPTY, TH)
    assert verdict.ok, (line, verdict.reason)


def test_a_bank_quote_without_its_marks_and_author_is_not_an_original_line(db, settings):
    payload = payload_for(db, settings, golden_cases()[1])
    verdict = check_device_line("It rests by changing. So did you.", payload, EMPTY, TH)
    assert verdict.reason == (
        "names: a bank quotation by Heraclitus without its quotation marks and author"
    )


# 3. Grounding: the thought needs no number, a quote's numbers are its own.


def test_a_number_free_reflection_passes_in_every_cell(db, settings):
    line = "Nothing was owed to yesterday, and nothing is owed for it."
    assert gate.number_tokens(line) == [] and gate.counted_numbers(line) == []
    for case in golden_cases():
        payload = payload_for(db, settings, case)
        assert gate.check_grounding(line, payload) == []
        assert check_device_line(line, payload, EMPTY, TH).ok, case["cell"]


def test_numbers_inside_a_bank_quote_are_not_grounded_and_numbers_outside_are(db, settings):
    payload = payload_for(db, settings, golden_cases()[0])
    assert 1000.0 not in payload.numbers()
    assert gate.counted_numbers(JOURNEY) == [("thousand", 1000.0, None)]
    assert check_device_line(JOURNEY, payload, EMPTY, TH).ok
    assert check_device_line(JOURNEY + ". Load 118.", payload, EMPTY, TH).ok
    outside = check_device_line(JOURNEY + ". Load 124.", payload, EMPTY, TH)
    assert outside.reason == "grounding: 124 not in payload"
    words = check_device_line(JOURNEY + ". Four dots in.", payload, EMPTY, TH)
    assert words.reason == "grounding: four not in payload"
    unquoted = "A journey of a thousand miles, and yesterday was one of them."
    assert check_device_line(unquoted, payload, EMPTY, TH).reason == (
        "grounding: thousand not in payload"
    )
    invented = '"A journey of a thousand miles begins with 124 steps." - Lao Tzu'
    assert check_device_line(invented, payload, EMPTY, TH).reason.startswith("grounding: 124")


# 4. Length.


def test_220_characters_pass_and_221_fail(db, settings):
    payload = payload_for(db, settings, golden_cases()[1])
    base = "Nothing was owed to yesterday, and nothing is owed for it; "
    line = (base + "the quiet day carries the loud one, " * 5)[:219].rstrip(", ") + "."
    line = line.ljust(220, "a") if len(line) < 220 else line
    assert len(line) == 220
    assert check_device_line(line, payload, EMPTY, TH).ok
    longer = line + "a"
    assert check_device_line(longer, payload, EMPTY, TH).reason == "length: 221 chars, limit 220"
    assert DEVICE_MAX == prompt.DEVICE_LINE_MAX_CHARS == SUMMARY_MAX_CHARS == 220
    assert fb.THOUGHT_MAX == 220 - fb.FACT_MAX - 1


def test_prompt_states_the_cap_and_that_shorter_is_welcome():
    text = prompt.STABLE_SYSTEM_PROMPT
    assert "at most 220 characters" in text and "110" not in text
    assert "Shorter is welcome" in text and "half a minute" in text
    assert "The thought leads" in text and "never a recap of the stats" in text
    assert "Quote only from the quote bank" in text and "Never quote from memory" in text
    assert "living or dead" in text
    for _, line in prompt.CONTRASTIVE_LINES:
        assert len(line) <= DEVICE_MAX


# 5. Repetition: the same quote or the same author inside the last 14 lines.


def test_the_same_quote_or_author_within_the_memory_window_is_rejected(db, settings):
    payload = payload_for(db, settings, golden_cases()[1])
    again = check_device_line(EPICTETUS, payload, Recent(opening_lines=(EPICTETUS,)), TH)
    assert again.reason == "names: this quotation appeared in the last 14 lines"
    author = check_device_line(EPICTETUS_2, payload, Recent(sources_named=("epictetus",)), TH)
    assert author.reason == "names: Epictetus was already quoted in the last 14 lines"
    assert check_device_line(EPICTETUS_2, payload, Recent(sources_named=("seneca",)), TH).ok


def test_memory_remembers_authors_for_fourteen_lines_and_no_longer(db, settings):
    start = date(2026, 9, 1)
    memory.remember(db, start.isoformat(), EPICTETUS, None, "control", "model", "pass", [])
    for i in range(1, 14):
        day = (start + timedelta(days=i)).isoformat()
        memory.remember(db, day, f"Plain line {i}, nothing named.", None, "habit", "m", "pass", [])
    payload = payload_for(db, settings, golden_cases()[1])
    inside = memory.recent_before(db, (start + timedelta(days=14)).isoformat())
    assert inside.sources_named == ("epictetus",)
    assert not check_device_line(EPICTETUS_2, payload, inside, TH).ok
    assert not check_device_line(EPICTETUS, payload, inside, TH).ok
    memory.remember(
        db, (start + timedelta(days=14)).isoformat(), "One more plain line.", None, "habit", "m",
        "pass", [],
    )  # fmt: skip
    outside = memory.recent_before(db, (start + timedelta(days=15)).isoformat())
    assert outside.sources_named == ()
    assert check_device_line(EPICTETUS_2, payload, outside, TH).ok


def test_memory_counts_the_author_named_not_a_name_inside_someone_elses_quote():
    assert gate.sources_in(SENECA) == ["seneca"]
    assert gate.sources_in("“It rests by changing.” — Heraclitus") == ["heraclitus"]
    assert gate.sources_in("The Stoics kept two lists.") == []
    assert gate.sources_in('"..." - The Dhammapada') == ["the dhammapada"]


# 6. The prompt: stable system text, a user message of aggregates and public quotes only.


def test_user_message_is_fixed_headers_the_payload_bank_entries_and_recent_lines(db, settings):
    payload = payload_for(db, settings, golden_cases()[0])
    recent_lines = ["A recent line.", SENECA]
    message = prompt.user_message(
        payload, recent_lines, ["seneca"], "grounding: 124 not in payload"
    )
    offered = {f"- {quotes.display(q)}" for q in quotes.for_lens(payload.lens)}
    allowed = (
        set(payload.text().splitlines())
        | offered
        | {f"- {line}" for line in recent_lines}
        | {
            f"Lens for today: {payload.lens}.",
            f"Cell: {payload.cell.name}.",
            "Payload (context, and the only numbers you may use):",
            "Quote bank for today (quote at most one, whole and unchanged, or none):",
            "Named in the last 14 lines, so not offered today: seneca.",
            "Recent device lines (do not reuse their opening three words, central image or quote):",
            "Your previous line was rejected: grounding: 124 not in payload. Write a different "
            "one.",
            "Write the line now.",
        }
    )
    lines = message.splitlines()
    assert [line for line in lines if line not in allowed] == []
    shown = [line for line in lines if line in offered]
    assert shown and not [line for line in shown if line.endswith("- Seneca")]
    assert len(shown) == len([q for q in quotes.for_lens(payload.lens) if q.author != "Seneca"])
    for key, value in json.loads(payload.text()).items():
        assert not isinstance(value, list), key
    assert "samples" not in message and "start_utc" not in message


def test_system_prompt_carries_no_payload_and_only_bank_quotations():
    text = prompt.STABLE_SYSTEM_PROMPT
    for _, line in prompt.CONTRASTIVE_LINES:
        for span in gate.quotations_in(line):
            assert gate.bank_entry(span) is not None, span
    assert text.count("Lens for today") == 0


# 7. End to end: a quote line from the model is stored as written; an invented one is not.


def test_split_reply_keeps_a_quotation_and_unwraps_a_wrapped_line():
    assert split_reply(SENECA) == (SENECA, None)
    assert split_reply(f"Here is the line:\n{SENECA}\nWeb.") == (SENECA, "Web.")
    assert split_reply('Seneca: "We suffer."') == ('Seneca: "We suffer."', None)
    assert split_reply('"A plain line."') == ("A plain line.", None)
    assert split_reply("“A plain line.”") == ("A plain line.", None)


def test_a_bank_quote_from_the_model_is_stored_and_remembered(db, settings):
    seed(db, golden_cases()[0])
    line = '"It is difficulties that show what men are." - Epictetus'
    result = write_summary(db, settings, SHOWN, client=FakeClient(reply(line)), now=NOW)
    assert result.source == "model" and result.line == line
    assert memory.recent_before(db, "2026-10-02").sources_named == ("epictetus",)


def test_an_invented_quote_is_rejected_retried_with_the_reason_then_falls_back(db, settings):
    seed(db, golden_cases()[0])
    invented = '"The day belongs to whoever shows up for it." - Marcus Aurelius'
    client = FakeClient(reply(invented), reply(invented))
    result = write_summary(db, settings, SHOWN, client=client, now=NOW)
    retry = client.requests[1]["messages"][0]["content"]
    assert "rejected: names: the words in quotation marks are not a quote-bank entry" in retry
    assert_fell_back(db, result)
    assert "shows up for it" not in result.line


# 8. The fallback speaks in the same voice.


def kind_of(line: str, payload: Payload) -> str:
    if gate.quotations_in(line):
        return "quote"
    return "fact" if fb.fact_clause(payload) in line else "thought"


@pytest.mark.parametrize("index", [0, 1, 3, 7], ids=["train", "rest", "broken", "travel"])
def test_ordinary_days_are_thought_led_and_the_three_kinds_take_turns(db, settings, index):
    base = payload_for(db, settings, golden_cases()[index])
    first = date(2026, 10, 5)
    kinds = []
    for offset in range(21):
        payload = with_day(base, first + timedelta(days=offset))
        line = fb.fallback_line(payload, EMPTY, TH).line
        fact = fb.fact_clause(payload)
        assert not line.startswith(fact), line
        kinds.append(kind_of(line, payload))
    assert {kinds.count(k) for k in ("quote", "thought", "fact")} == {7}
    assert "".join(k[0] for k in kinds[:6]) in ("fqtfqt", "qtfqtf", "tfqtfq")


@pytest.mark.parametrize("index", [2, 5], ids=["no-hr", "health-delayed"])
def test_hard_cases_keep_the_plain_fact_in_front(db, settings, index):
    base = payload_for(db, settings, golden_cases()[index])
    for offset in range(7):
        payload = with_day(base, date(2026, 10, 5) + timedelta(days=offset))
        line = fb.fallback_line(payload, EMPTY, TH).line
        assert line.startswith(fb.fact_clause(payload)), line


def test_a_finished_book_is_always_named_after_the_thought(db, settings):
    base = payload_for(db, settings, golden_cases()[8])
    for offset in range(7):
        payload = with_day(base, date(2026, 10, 5) + timedelta(days=offset))
        line = fb.fallback_line(payload, EMPTY, TH).line
        assert "*Little Fires Everywhere*" in line and not line.startswith("Finished"), line


@pytest.mark.parametrize("index", [0, 1, 7], ids=["train", "rest", "travel-broken"])
def test_sixty_days_of_fallback_never_repeat_a_line_a_quote_or_an_author_in_the_window(
    db, settings, index
):
    base = payload_for(db, settings, golden_cases()[index])
    first = date(2026, 10, 5)
    lines: list[str] = []
    for offset in range(60):
        payload = with_day(base, first + timedelta(days=offset))
        newest_first = tuple(reversed(lines))
        sources: list[str] = []
        for previous in newest_first[: gate.SOURCE_WINDOW_LINES]:
            sources.extend(gate.sources_in(previous))
        recent = Recent(newest_first[:14], newest_first[:30], tuple(dict.fromkeys(sources)))
        result = fb.fallback_line(payload, recent, TH)
        assert not result.last_resort, (offset, result.gate.reason)
        assert result.line.strip() and len(result.line) <= DEVICE_MAX
        assert check_device_line(result.line, payload, recent, TH).ok
        assert result.line not in newest_first[:30], (offset, result.line)
        for person in gate.sources_in(result.line):
            assert person not in sources, (offset, result.line)
        lines.append(result.line)
    quoted = [line for line in lines if gate.quotations_in(line)]
    assert len(quoted) >= 12, len(quoted)
    assert len({gate.sources_in(line)[0] for line in quoted}) >= 8


# 9. No workout on record is not rest (Max, 2026-10-04), weekends included.

REST_CLAIMS = [
    "A rest day well spent.",
    "Rest day Saturday; the week is where the habit lives.",
    "Recovery day: nothing owed.",
    "A day off is part of the plan.",
    "A day of rest, and a good one.",
    "You took the day off, and that was yours to take.",
    "The weekend off did its work.",
    "You rested yesterday, and that counts.",
    "You have rested; now begin again.",
    "You took it easy, rightly.",
    "You chose rest, which is also a choice.",
    "Rested on Sunday, as weekends go.",
    "Yesterday was rest; nothing more to say.",
    "Saturday's recovery did its work.",
    "Rest yesterday, effort tomorrow.",
    "Your rest counts.",
    "No workout yesterday, and nothing owed for it.",
    "No training on Saturday; the weekend is for that.",
    "You did not train yesterday, and the week is fine.",
    "A day without a workout is still a day.",
    "An easy day is part of the pattern.",
]
REST_IDEAS = [
    "Rest is where the training lands.",
    "Rest is not the gap between efforts; it is where they land.",
    "Recovery is work too, and it counts.",
    "Rest does its work unobserved.",
    "The rest of the week is not yours to hold yet.",
    "No workout on record yesterday; the thought stands without one.",
    "No workout recorded, which says something about the record and nothing about you.",
    "Whatever yesterday was, nothing is owed for it.",
    "Sleep is where rest does its work, and yesterday held 7.4 h of it.",
    "The Stoics would not grade a Saturday; neither does the week.",
]


@pytest.mark.parametrize("line", REST_CLAIMS)
def test_a_claim_that_he_rested_is_rejected_on_any_day(db, settings, line):
    for index in (0, 1, 7):
        payload = payload_for(db, settings, golden_cases()[index])
        assert not gate.has_rest_signal(payload)
        verdict = check_device_line(line, payload, EMPTY, TH)
        assert verdict.reason.startswith("rest: "), (line, verdict.reason)
        assert gate.check_web_line(line, payload, EMPTY).reason.startswith("rest: ")


@pytest.mark.parametrize("line", REST_IDEAS)
def test_rest_as_an_idea_and_no_workout_on_record_pass(db, settings, line):
    payload = payload_for(db, settings, golden_cases()[1])
    verdict = check_device_line(line, payload, EMPTY, TH)
    assert verdict.ok, (line, verdict.reason)


def test_no_fallback_fact_or_thought_claims_rest_whatever_the_weekday(db, settings):
    bank = [t for lens in fb.THOUGHTS.values() for ts in lens.values() for t in ts]
    base = payload_for(db, settings, golden_cases()[1])
    for thought in bank:
        assert gate.check_rest_claim(thought, base) == [], thought
    for index in (1, 3, 7):
        case = payload_for(db, settings, golden_cases()[index])
        for offset in range(14):
            payload = with_day(case, date(2026, 10, 3) + timedelta(days=offset))
            for text in fb.candidates(payload) + fb.fact_options(payload):
                assert gate.check_rest_claim(text, payload) == [], text
                assert "workout" not in text.lower() or "on record" in text.lower(), text


def test_the_model_is_told_what_is_known_not_that_the_day_was_rest(db, settings):
    payload = payload_for(db, settings, golden_cases()[1])
    assert payload.cell.day_type == "rest", "the fixture matrix keeps its name"
    message = prompt.user_message(payload, [], [])
    assert "Cell: no-workout-on-record / all-sources / alive / base." in message
    assert '"day_type": "no-workout-on-record"' in message
    assert '"day_type": "rest"' not in message and "Cell: rest" not in message
    text = prompt.STABLE_SYSTEM_PROMPT
    assert "Never say or imply that he rested or did not train" in text
    assert "weekends included" in text and "no workout on record" in text
    for cell, line in prompt.CONTRASTIVE_LINES:
        assert not cell.startswith("rest"), cell
        assert gate.check_rest_claim(line, payload) == [], line
