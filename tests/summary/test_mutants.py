"""A gate is not a gate until a mutation makes it red.

Each mutant is one source substitution applied to a COPY of the tree; the summary tests
run against that copy in a subprocess and must fail, naming at least the tests listed.
The working tree is never modified.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COPIED = ("app", "tests", "fixtures", "tools", "config.toml", "pyproject.toml")
IGNORED = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc")


@dataclass(frozen=True)
class Mutant:
    name: str
    file: str
    old: str
    new: str
    must_fail: tuple[str, ...]


MUTANTS = (
    Mutant(
        "gate accepts a number not in the payload",
        "app/summary/gate.py",
        "return [tok for tok in number_tokens(text) if float(tok) not in allowed]",
        "return []",
        (
            "test_invented_number_fails_grounding",
            "test_hated_lines_fail_with_the_named_reason[Third dot this week, load 124.",
            "test_gate_failure_regenerates_once_with_the_reason_then_falls_back",
        ),
    ),
    Mutant(
        "ban list ignores exclamation marks",
        "app/summary/gate.py",
        'if "!" in fold(line):',
        'if "!" in fold(line) and False:',
        ("test_hated_lines_fail_with_the_named_reason[Third dot this week, load 118. Well don",),
    ),
    Mutant(
        "cap check uses last month",
        "app/summary/spend.py",
        "month = month_of(moment, settings.home_tz)\n    return CapCheck(",
        "local = moment.astimezone(ZoneInfo(settings.home_tz))\n"
        "    last = local.replace(day=1) - __import__('datetime').timedelta(days=1)\n"
        '    month = last.strftime("%Y-%m")\n'
        "    return CapCheck(",
        (
            "test_cap_counts_month_to_date_including_earlier_calls_this_month",
            "test_cap_counts_this_month_only",
        ),
    ),
    Mutant(
        "fallback on model error replaced by an empty line",
        "app/summary/run.py",
        "fallback = fallback_line(payload, recent, threshold)",
        "from app.summary.fallback import FallbackResult\n"
        "    from app.summary.gate import GateResult\n"
        '    fallback = FallbackResult("", None, GateResult(True, ()))',
        (
            "test_sdk_errors_fall_back_and_never_blank[rate]",
            "test_sdk_errors_fall_back_and_never_blank[connection]",
            "test_non_end_turn_stop_reason_falls_back_but_is_still_priced[refusal]",
            "test_job_writes_todays_line_and_a_second_run_does_not_call",
        ),
    ),
    Mutant(
        "gate ignores number words",
        "app/summary/gate.py",
        "for token, value, kind in counted_numbers(text):",
        "for token, value, kind in []:",
        (
            "test_invented_number_words_fail_grounding[Four dots",
            "test_hated_lines_fail_with_the_named_reason[Four dots",
        ),
    ),
    Mutant(
        "ban list matches only the bare word, not its inflections",
        "app/summary/gate.py",
        '("crush", r"\\bcrush\\w*"),',
        '("crush", r"\\bcrush\\b"),',
        ("test_ban_list_catches_inflections[You crushed it.]",),
    ),
    Mutant(
        "gate ignores a number word that stands away from a counted noun",
        "app/summary/gate.py",
        "if item.soft or (item.digits and not item.ordinal_digit):",
        "if True:",
        (
            "test_ungrounded_number_forms_fail_grounding[Five this week",
            "test_ungrounded_number_forms_fail_grounding[Twice this week]",
        ),
    ),
    Mutant(
        "gate reads digits only when nothing is glued in front",
        "app/summary/gate.py",
        "            found.extend(_glued_digits(token))",
        "            pass",
        ("test_ungrounded_number_forms_fail_grounding[x2 last week]",),
    ),
    Mutant(
        "streak-anxiety bans match only 'the'",
        "app/summary/gate.py",
        'DETERMINER = r"(?:the|your|this|that|a|my|our)"',
        'DETERMINER = r"(?:the)"',
        (
            "test_streak_anxiety_and_nagging_families_are_banned[Don't break your streak.]",
            "test_streak_anxiety_and_nagging_families_are_banned[Keep your streak alive.]",
        ),
    ),
    Mutant(
        "ban list reads the line unfolded",
        "app/summary/gate.py",
        'return " ".join(WORD.findall(fold(text).lower()))',
        'return " ".join(WORD.findall(text.lower()))',
        ("test_streak_anxiety_and_nagging_families_are_banned[",),
    ),
    Mutant(
        "attribution is case-sensitive again",
        "app/summary/gate.py",
        "    re.IGNORECASE,\n)\nCLOSING_ATTRIBUTION",
        "    0,\n)\nCLOSING_ATTRIBUTION",
        (
            "test_attribution_to_a_named_person_is_rejected[In the words of an old coach",
            "test_attribution_to_a_named_person_is_rejected[AS MY OLD COACH SAID",
        ),
    ),
    Mutant(
        "a bank quote passes under any author's name",
        "app/summary/gate.py",
        "        if author not in named:",
        "        if False:",
        (
            "test_a_bank_quote_under_the_wrong_author_is_rejected",
            'test_hated_lines_fail_with_the_named_reason["We suffer more often in imagination',
        ),
    ),
    Mutant(
        "a quotation matches the bank on its first three words",
        "app/summary/gate.py",
        "    return normalize(fold_quotes(text))\n\n\nBANK",
        '    return " ".join(normalize(fold_quotes(text)).split()[:3])\n\n\nBANK',
        (
            "test_a_bank_author_with_words_not_in_the_bank_is_rejected"
            '["We suffer more in imagination than in reality." - Seneca]',
            "test_bank_is_large_unique_ascii_sourced_and_by_deceased_authors_only",
        ),
    ),
    Mutant(
        "words in quotation marks need not be a bank entry",
        "app/summary/gate.py",
        "    if spans and entry is None:",
        "    if False:",
        (
            'test_a_bank_author_with_words_not_in_the_bank_is_rejected[The "dot" is only a dot.]',
            "test_an_invented_quote_is_rejected_retried_with_the_reason_then_falls_back",
        ),
    ),
    Mutant(
        "a person named without a quotation passes",
        "app/summary/gate.py",
        "    elif named:\n        return [",
        "    elif False:\n        return [",
        (
            "test_attribution_to_a_named_person_is_rejected[Seneca says the day was yours.]",
            "test_a_living_or_non_bank_person_is_rejected[Socrates had a word for this.]",
            "test_a_person_is_named_only_as_the_author_of_a_bank_quote",
        ),
    ),
    Mutant(
        "the gate believes every day was a rest day",
        "app/summary/gate.py",
        'and the day-type label is derived from that same absence."""\n    return False',
        'and the day-type label is derived from that same absence."""\n    return True',
        (
            "test_a_claim_that_he_rested_is_rejected_on_any_day[A rest day well spent.]",
            "test_hated_lines_fail_with_the_named_reason[Rest day Saturday",
            "test_hated_lines_fail_with_the_named_reason[You rested yesterday",
        ),
    ),
    Mutant(
        "device cap back to 110",
        "app/summary/gate.py",
        "DEVICE_MAX = 220",
        "DEVICE_MAX = 110",
        (
            "test_220_characters_pass_and_221_fail",
            "test_every_bank_entry_passes_the_gate_as_displayed[Meditations 12.4]",
        ),
    ),
    Mutant(
        "device line of any length passes",
        "app/summary/gate.py",
        "    if len(line) > DEVICE_MAX:",
        "    if False:",
        (
            "test_220_characters_pass_and_221_fail",
            "test_hated_lines_fail_with_the_named_reason[Steady is the whole point",
        ),
    ),
    Mutant(
        "grounding reads the numbers inside a bank quote",
        "app/summary/gate.py",
        "    text = without_bank_quotes(without_title(line, payload))",
        "    text = without_title(line, payload)",
        (
            "test_numbers_inside_a_bank_quote_are_not_grounded_and_numbers_outside_are",
            "test_every_bank_entry_passes_the_gate_as_displayed[Tao Te Ching 64]",
        ),
    ),
    Mutant(
        "an author may be quoted again inside the window",
        "app/summary/gate.py",
        "        if entry.author.lower() in recent_sources:",
        "        if False:",
        (
            "test_the_same_quote_or_author_within_the_memory_window_is_rejected",
            "test_memory_remembers_authors_for_fourteen_lines_and_no_longer",
            "test_sixty_days_of_fallback_never_repeat_a_line_a_quote_or_an_author_in_the_window",
        ),
    ),
    Mutant(
        "the same quotation may return inside the window",
        "app/summary/gate.py",
        "            if any(quote_key(span) == key for span in quotations_in(previous)):",
        "            if False:",
        ("test_the_same_quote_or_author_within_the_memory_window_is_rejected",),
    ),
    Mutant(
        "the model's quotation marks are stripped from the line",
        "app/summary/run.py",
        "    if wrapped and not any(mark in inner for mark in WRAP_OPEN + WRAP_CLOSE):",
        "    if True:",
        ("test_split_reply_keeps_a_quotation_and_unwraps_a_wrapped_line",),
    ),
    Mutant(
        "fallback leads with the fact on ordinary days",
        "app/summary/fallback.py",
        '    with_fact = [f"{thought} {fact}" for thought in thoughts]',
        '    with_fact = [f"{fact} {thought}" for thought in thoughts]',
        (
            "test_ordinary_days_are_thought_led_and_the_three_kinds_take_turns",
            "test_morning_run_describes_yesterdays_dot_in_the_fallback",
        ),
    ),
    Mutant(
        "an unexpected exception from the model call escapes",
        "app/summary/run.py",
        "    except Exception as exc:\n        log.exception(",
        "    except ZeroDivisionError as exc:\n        log.exception(",
        ("test_any_exception_from_the_call_falls_back_and_never_blanks[TypeError]",),
    ),
    Mutant(
        "replay --verify compares the summary's keys",
        "tools/replay.py",
        "strip |= AUTHORED_KEYS.get(table, frozenset())",
        "strip |= frozenset()",
        ("test_verify_stays_green_after_a_summary",),
    ),
    Mutant(
        "similarity gate threshold off",
        "app/summary/gate.py",
        "if score > threshold:",
        "if score > 1.0:",
        (
            "test_similarity_gate_rejects_a_near_copy_and_passes_a_fresh_line",
            "test_similarity_threshold_comes_from_the_caller",
            "test_fallback_skips_candidates_too_similar_to_recent_lines",
        ),
    ),
)


def copy_tree(dest: Path) -> None:
    for name in COPIED:
        source = REPO_ROOT / name
        if source.is_dir():
            shutil.copytree(source, dest / name, ignore=IGNORED)
        else:
            shutil.copy(source, dest / name)


def run_summary_tests(tree: Path) -> subprocess.CompletedProcess:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("LIFE_") and k != "PYTEST_CURRENT_TEST"
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/summary",
            "-q",
            "-p",
            "no:cacheprovider",
            "--deselect",
            "tests/summary/test_mutants.py",
            "-rf",
        ],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


@pytest.mark.parametrize("mutant", MUTANTS, ids=[m.name for m in MUTANTS])
def test_mutant_turns_the_gate_red(tmp_path: Path, mutant: Mutant):
    tree = tmp_path / "mutant"
    copy_tree(tree)
    target = tree / mutant.file
    source = target.read_text()
    assert source.count(mutant.old) == 1, f"{mutant.file} no longer contains the mutated line"
    target.write_text(source.replace(mutant.old, mutant.new))

    result = run_summary_tests(tree)

    assert result.returncode != 0, (
        f"mutant '{mutant.name}' was not caught:\n{result.stdout[-2000:]}"
    )
    for name in mutant.must_fail:
        assert "FAILED tests/summary/" in result.stdout and name in result.stdout, (
            f"mutant '{mutant.name}' did not turn {name} red:\n{result.stdout[-3000:]}"
        )


def test_unmutated_copy_is_green(tmp_path: Path):
    """The instrument measures the copy, not the working tree: a clean copy must pass."""
    tree = tmp_path / "clean"
    copy_tree(tree)
    result = run_summary_tests(tree)
    assert result.returncode == 0, result.stdout[-3000:]
