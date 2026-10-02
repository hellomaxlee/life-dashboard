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
        'if "!" in line:',
        'if "!" in line and False:',
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
