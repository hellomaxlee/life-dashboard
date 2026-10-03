"""The rule-based fallback passes the same gate in every cell and under every lens, and the
display never blanks even when every candidate collides with recent lines."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from app.summary import payload as payload_mod
from app.summary.fallback import ULTIMATE_LINE, candidates, fallback_line
from app.summary.gate import DEVICE_MAX, Recent, check_device_line
from app.summary.payload import LENSES, Payload, lens_for
from tests.summary.conftest import golden_cases, payload_for, seed

THRESHOLD = 0.5


def shown(day: str) -> str:
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def with_lens(payload: Payload, lens: str) -> Payload:
    data = dict(payload.data, lens=lens)
    return Payload(payload.day_local, lens, payload.cell, data)


@pytest.mark.parametrize("case", golden_cases(), ids=[c["cell"] for c in golden_cases()])
@pytest.mark.parametrize("lens", LENSES)
def test_every_cell_under_every_lens_yields_a_gated_line(db, settings, case, lens):
    payload = with_lens(payload_for(db, settings, case), lens)
    result = fallback_line(payload, Recent(), THRESHOLD)
    assert not result.last_resort
    assert result.gate.ok
    assert len(result.line) <= DEVICE_MAX
    assert check_device_line(result.line, payload, Recent(), THRESHOLD).ok


def test_fallback_uses_only_payload_numbers_in_the_fact_clause(db, settings):
    for case in golden_cases():
        payload = payload_for(db, settings, case)
        for line in candidates(payload):
            verdict = check_device_line(line, payload, Recent(), THRESHOLD)
            assert not verdict.reason.startswith("grounding"), (line, verdict.reason)


def test_candidates_rotate_with_the_day_so_consecutive_days_differ(db, settings):
    base = golden_cases()[1]
    lines = []
    for offset in range(3):
        case = dict(
            base, day=(date.fromisoformat(base["day"]) + timedelta(days=offset)).isoformat()
        )
        payload = with_lens(payload_for(db, settings, case), "rest-as-work")
        lines.append(candidates(payload)[0])
    assert len(set(lines)) == 3


def test_fallback_skips_candidates_too_similar_to_recent_lines(db, settings):
    payload = with_lens(payload_for(db, settings, golden_cases()[1]), "rest-as-work")
    first, second = candidates(payload)[:2]
    recent = Recent(opening_lines=(), similarity_lines=(first,))
    result = fallback_line(payload, recent, THRESHOLD)
    assert result.line == second
    assert not result.last_resort


def test_last_resort_when_every_candidate_collides_still_shows_a_line(db, settings):
    payload = with_lens(payload_for(db, settings, golden_cases()[1]), "rest-as-work")
    recent = Recent(similarity_lines=tuple(candidates(payload)))
    result = fallback_line(payload, recent, THRESHOLD)
    assert result.last_resort
    assert result.line.startswith("No workout yesterday, 7.4 h of sleep.")
    assert "similarity not checked" in result.gate.reason
    assert result.line != ULTIMATE_LINE


def test_broken_week_fallback_never_mentions_the_streak(db, settings):
    payload = payload_for(db, settings, golden_cases()[3])
    assert payload.cell.streak_state == "broken-last-week"
    for lens in LENSES:
        for line in candidates(with_lens(payload, lens)):
            assert "streak" not in line.lower() and "next week" not in line.lower()
    assert fallback_line(payload, Recent(), THRESHOLD).line.startswith("One dot last week")


def test_wellness_fact_goes_to_the_web_line_only(db, settings):
    case = dict(golden_cases()[0])
    case["daily_metrics"] = {
        **case["daily_metrics"],
        "wellness_fact": {
            "metric": "hrv_ms",
            "value": 38.4,
            "baseline": 52.0,
            "direction": "below",
        },
    }
    payload = payload_for(db, settings, case)
    result = fallback_line(payload, Recent(), THRESHOLD)
    assert "HRV" not in result.line
    assert result.web_line == "HRV 38 ms against a 52 ms baseline, below the band."


def test_lens_cycles_over_seven_days():
    days = [date(2026, 10, 1) + timedelta(days=i) for i in range(14)]
    seen = [lens_for(d) for d in days]
    assert set(seen[:7]) == set(LENSES)
    assert seen[:7] == seen[7:]


def test_classification_reads_flags_and_infers_only_what_the_data_supports(db, settings):
    case = dict(golden_cases()[0])
    payload = payload_for(db, settings, case)
    assert payload.cell.inferred == ("day_type", "season")
    flagged = payload_for(db, settings, golden_cases()[6])
    assert flagged.cell.inferred == ()
    assert flagged.data["cell"]["inferred"] == []


def test_payload_rounds_to_what_the_line_may_say(db, settings):
    payload = payload_for(db, settings, golden_cases()[0])
    assert payload.data["load"] == {"trimp": 118, "acute": 410, "chronic": 380, "balance": 1.1}
    assert payload.data["day"]["sleep_hours"] == 7.4
    assert payload.data["week"]["dots_word"] == "third"
    assert "wellness_fact" not in payload.data
    assert payload.data["books"] == {"ytd": 3, "target": 12}


def test_payload_takes_workout_minutes_from_activities_when_the_engine_did_not_write_them(
    db, settings
):
    case = dict(golden_cases()[2])
    case["daily_metrics"] = {
        k: v for k, v in case["daily_metrics"].items() if k != "workout_minutes"
    }
    seed(db, case)
    db.execute(
        "INSERT INTO activities (id, type, start_utc, end_utc, duration_s) "
        "VALUES ('w1', 'run', '2026-10-02T22:00:00Z', '2026-10-02T22:52:00Z', 3120)"
    )
    payload = payload_mod.build_payload(db, settings, "2026-10-03")
    assert payload.data["day"]["workout_minutes"] == 52
    assert 52.0 in payload.numbers()


def test_missing_rows_classify_as_health_delayed_and_never_crash(db, settings):
    payload = payload_mod.build_payload(db, settings, "2026-11-11")
    assert payload.cell.completeness == "health-delayed"
    assert payload.cell.streak_state == "never-started"
    assert fallback_line(payload, Recent(), THRESHOLD).line.startswith(
        "Yesterday's health data has not arrived"
    )


def test_malformed_metrics_json_is_treated_as_missing(db, settings):
    db.execute(
        "INSERT INTO daily_metrics (day_local, metrics_json) VALUES ('2026-11-12', 'not json')"
    )
    db.execute(
        "INSERT INTO daily_metrics (day_local, metrics_json) VALUES ('2026-11-13', ?)",
        (json.dumps({"sleep_hours": "seven", "steps": True, "workout_load": float("nan")}),),
    )
    for day in ("2026-11-12", "2026-11-13"):
        payload = payload_mod.build_payload(db, settings, shown(day))
        assert payload.data["day"]["sleep_hours"] is None
        assert fallback_line(payload, Recent(), THRESHOLD).gate.ok
