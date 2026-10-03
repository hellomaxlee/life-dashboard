"""Bartek's QA of Phase 3 (2026-10-02), one test group per finding. Each was red before its
fix; the commit message and report carry the pre-fix red line."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

import pytest

from app.summary import fallback as fb
from app.summary import spend
from app.summary.gate import Recent, check_ban, check_device_line, check_grounding
from app.summary.payload import LENSES, Payload, build_payload
from app.summary.prompt import STABLE_SYSTEM_PROMPT
from app.summary.run import DEVICE_KEY, SUMMARY_KEYS, write_summary
from tests.summary.conftest import golden_cases, payload_for, shown_day

TH = 0.5
MORNING = datetime(2026, 10, 1, 10, 50, tzinfo=UTC)


def put_day(db, day: str, metrics: dict) -> None:
    db.execute(
        "INSERT OR REPLACE INTO daily_metrics (day_local, metrics_json) VALUES (?, ?)",
        (day, json.dumps(metrics)),
    )


def put_week(db, monday: str, metrics: dict) -> None:
    db.execute(
        "INSERT OR REPLACE INTO weekly_metrics (week_start_local, metrics_json) VALUES (?, ?)",
        (monday, json.dumps(metrics)),
    )


def with_lens(payload: Payload, lens: str) -> Payload:
    return Payload(payload.day_local, lens, payload.cell, dict(payload.data, lens=lens))


# 1. The morning line describes yesterday and is stored under today.


def test_morning_line_describes_yesterday_and_is_stored_under_today(db, settings):
    put_day(db, "2026-09-30", golden_cases()[0]["daily_metrics"])
    put_week(db, "2026-09-28", golden_cases()[0]["weekly_metrics"])
    put_day(db, "2026-10-01", {"books_ytd": 3, "workout_count": 0, "quality_workout": False})

    result = write_summary(db, settings, "2026-10-01", now=MORNING)

    payload = build_payload(db, settings, "2026-10-01")
    assert payload.data["describes"] == "2026-09-30"
    assert payload.data["shown_on"] == "2026-10-01"
    assert payload.data["day"]["workout_load"] == 118
    assert "load 118" in result.line
    row = db.execute("SELECT metrics_json FROM daily_metrics WHERE day_local = '2026-10-01'")
    assert json.loads(row.fetchone()[0])[DEVICE_KEY] == result.line
    assert not re.search(r"\b(tonight|this evening|this morning)\b", result.line, re.I)


def test_monday_line_describes_sunday_in_last_weeks_row(db, settings):
    put_day(db, "2026-10-04", golden_cases()[0]["daily_metrics"])
    put_week(db, "2026-09-28", golden_cases()[0]["weekly_metrics"])
    put_week(db, "2026-10-05", {"quality_workouts": 0, "weeks_hit_streak": 5})
    payload = build_payload(db, settings, "2026-10-05")
    assert payload.data["describes"] == "2026-10-04"
    assert payload.data["weekday"] == "Sunday"
    assert payload.data["week"]["quality_workouts"] == 3
    assert payload.data["week"]["relation"] == "last week"


# 2. Number words are grounded like digits.


@pytest.mark.parametrize(
    "line",
    [
        "Four dots this week, load 118. Steady.",
        "Ten books this year. Steady is fine.",
        "Fifth dot this week, load 118. Steady.",
        "Load 118 and nine hours of sleep. Steady.",
        "Load 118, two sessions. Steady is fine.",
    ],
)
def test_invented_number_words_fail_grounding(db, settings, line):
    payload = payload_for(db, settings, golden_cases()[0])
    verdict = check_device_line(line, payload, Recent(), TH)
    assert verdict.reason.startswith("grounding:"), verdict.reason


def test_number_words_matching_the_payload_pass_and_articles_do_not_count(db, settings):
    payload = payload_for(db, settings, golden_cases()[0])
    for line in (
        "Third dot this week, load 118. A quiet week.",
        "Three dots, seven hours the target. A day well spent.",
        "Load 118, a dot and a night of 7.4 h. Enough.",
    ):
        assert check_grounding(line, payload) == [], line


# 3. tools.replay --verify: covered in tests/summary/test_replay_summary.py.


# 4. Ban list catches inflections.


@pytest.mark.parametrize(
    "line",
    [
        "You crushed it.",
        "You are grinding well.",
        "Feeling guilty is optional.",
        "No need to feel disappointed.",
        "Hustling is not the point.",
        "Be beastly about rest.",
        "Laziness is not rest.",
        "Shameful to skip.",
        "Keep the chain going.",
        "You have to sleep more.",
        "Stayed hard all week.",
        "You're failing the week.",
        "Breaking the chain is fine.",
    ],
)
def test_ban_list_catches_inflections(line):
    assert check_ban(line), line


def test_no_legitimate_word_in_the_hand_written_lines_or_bank_is_banned():
    lines = [c["line"] for c in golden_cases()]
    bank = [t for banks in fb.THOUGHTS.values() for ls in banks.values() for t in ls]
    for line in lines + bank:
        assert check_ban(line) == [], line


# 5. health-delayed fires on an engine rollover row.


def test_engine_rollover_row_without_health_data_is_health_delayed(db, settings):
    put_day(
        db,
        "2026-10-07",
        {
            "sleep_hours": None,
            "steps": None,
            "workout_count": 0,
            "quality_workout": False,
            "workout_ids": [],
            "wins": [],
            "books_ytd": 1,
        },
    )
    payload = build_payload(db, settings, "2026-10-08")
    assert payload.cell.completeness == "health-delayed"


# 6. broken-last-week only in the week after the break.


def test_broken_last_week_only_when_last_week_missed_after_a_streak(db, settings):
    put_day(db, "2026-10-14", {"sleep_hours": 7.2, "steps": 100, "workout_count": 0})
    put_week(db, "2026-10-12", {"quality_workouts": 0, "weeks_hit_streak": 0})
    put_week(db, "2026-10-05", {"quality_workouts": 1, "week_hit": False, "weeks_hit_streak": 0})
    put_week(db, "2026-09-28", {"quality_workouts": 3, "week_hit": True, "weeks_hit_streak": 2})
    assert build_payload(db, settings, "2026-10-15").cell.streak_state == "broken-last-week"

    put_day(db, "2026-10-21", {"sleep_hours": 7.2, "steps": 100, "workout_count": 0})
    put_week(db, "2026-10-19", {"quality_workouts": 0, "weeks_hit_streak": 0})
    put_week(db, "2026-10-12", {"quality_workouts": 1, "week_hit": False, "weeks_hit_streak": 0})
    payload = build_payload(db, settings, "2026-10-22")
    assert payload.cell.streak_state == "never-started"
    ok = check_device_line(
        "No workout, 7.2 h of sleep. The streak can wait.", payload, Recent(), TH
    )
    assert ok.ok, ok.reasons


# 7. Sleep truncated like the renderer.


def test_sleep_is_truncated_never_rounded_up_across_the_target(db, settings):
    put_day(
        db,
        "2026-10-06",
        {
            "sleep_hours": 6.97,
            "sleep_win": False,
            "workout_count": 0,
            "quality_workout": False,
            "steps": 5000,
            "wins": [],
        },
    )
    payload = build_payload(db, settings, "2026-10-07")
    assert payload.data["day"]["sleep_hours"] == 6.9
    assert "6.9 h" in fb.fact_clause(payload)
    assert 7.0 not in {payload.data["day"]["sleep_hours"]}


# 8. A session without HR gets its own bank.


def test_session_without_a_dot_draws_from_its_own_bank(db, settings):
    payload = payload_for(db, settings, golden_cases()[2])
    assert payload.cell.completeness == "workout-without-hr"
    for lens in LENSES:
        p = with_lens(payload, lens)
        thoughts = {c.split(". ", 1)[1] for c in fb.candidates(p)}
        assert thoughts <= set(fb.THOUGHTS[lens]["nodot"]), (lens, thoughts)


# 9. Book found by its effective home-timezone date.


def test_book_dated_only_by_date_added_late_evening_keeps_its_title(db, settings):
    db.execute(
        "INSERT INTO books (id, title, read_at, date_added, date_inferred) VALUES "
        "('b', 'Late Book', NULL, '2026-10-09T01:30:00Z', 1)"
    )
    put_day(
        db,
        "2026-10-08",
        {
            "book_finished_today": True,
            "books_ytd": 2,
            "sleep_hours": 7.2,
            "workout_count": 0,
            "quality_workout": False,
            "steps": 100,
        },
    )
    payload = build_payload(db, settings, "2026-10-09")
    assert payload.data["day"]["book_title"] == "Late Book"
    assert "Late Book" in fb.fact_clause(payload)


# 10. Every candidate fits, with the longest real title.

LONG_TITLE = 'The Handmaid’s Garden (The Gardener"s Trilogy, #1)'


def worst_case_variants(case: dict) -> list[dict]:
    """The case with numbers at their widest and the longest title in the fixtures."""
    out = [case]
    metrics = dict(case.get("daily_metrics", {}))
    for key, wide in (
        ("sleep_hours", 10.5),
        ("workout_load", 1180.0),
        ("load_balance", 1.35),
        ("workout_minutes", 125),
        ("books_ytd", 10),
    ):
        if metrics.get(key) is not None:
            metrics[key] = wide
    out.append(dict(case, daily_metrics=metrics))
    if case.get("book"):
        out.append(dict(case, daily_metrics=metrics, book=dict(case["book"], title=LONG_TITLE)))
    return out


def test_every_candidate_fits_every_cell_lens_and_the_longest_title(db, settings):
    for case in golden_cases():
        for variant in worst_case_variants(case):
            for flags in ([], ["travel"]):
                metrics = dict(variant.get("daily_metrics", {}))
                metrics["day_flags"] = sorted(set(metrics.get("day_flags", [])) | set(flags))
                base = payload_for(db, settings, dict(variant, daily_metrics=metrics))
                for lens in LENSES:
                    for line in fb.candidates(with_lens(base, lens)):
                        assert len(line) <= 110, (len(line), line)
                        verdict = check_device_line(line, with_lens(base, lens), Recent(), TH)
                        assert verdict.ok, (verdict.reason, line)


# 11. Worst-case input estimate at 2 chars per token.


def test_worst_case_estimate_uses_two_chars_per_token():
    assert spend.CHARS_PER_TOKEN == 2.0
    request = {"system": "s" * 1000, "messages": [], "max_tokens": 300}
    assert spend.estimate_input_tokens(request) == int(len(json.dumps("s" * 1000) + "[]") / 2) + 1


# 12. Voice rulings.


def test_banned_voice_lines_are_gone_from_the_bank():
    bank = [t for banks in fb.THOUGHTS.values() for ls in banks.values() for t in ls]
    for gone in (
        "Tonight the work is to let this land.",
        "The session is done; the rest of the work is sleep.",
        "Rest feels like nothing; that feeling is the bias.",
        "Notice the urge to call this a miss. It is not one.",
    ):
        assert gone not in bank
    assert not [t for t in bank if re.search(r"\b(tonight|evening)\b", t, re.I)]


def test_system_prompt_states_number_words_and_the_enforced_ban_list():
    assert "Number words count as numbers" in STABLE_SYSTEM_PROMPT
    for stem in ("crush", "grind", "guilt", "disappoint", "hustl", "beast", "lazy", "shame"):
        assert stem in STABLE_SYSTEM_PROMPT
    assert "describes yesterday" in STABLE_SYSTEM_PROMPT


def test_summary_keys_are_named_for_replay():
    assert SUMMARY_KEYS == frozenset({"summary_device_line", "summary_web_line", "summary_source"})


def test_shown_day_helper_is_the_day_after():
    case = golden_cases()[0]
    assert (
        shown_day(case)
        == (datetime.fromisoformat(case["day"]).date() + timedelta(days=1)).isoformat()
    )


def test_constant_last_resort_is_reachable_only_when_the_fact_clause_itself_fails(
    db, settings, monkeypatch
):
    payload = payload_for(db, settings, golden_cases()[1])
    everything = Recent(similarity_lines=tuple(fb.candidates(payload)))
    assert fb.fallback_line(payload, everything, TH).line != fb.ULTIMATE_LINE
    monkeypatch.setattr(fb, "fact_clause", lambda p: "Load 999, crushed it.")
    result = fb.fallback_line(payload, everything, TH)
    assert result.line == fb.ULTIMATE_LINE and result.last_resort
