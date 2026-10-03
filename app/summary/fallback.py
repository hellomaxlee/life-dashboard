"""Rule-based copy for when the model is unavailable, over budget, or rejected twice.

A line is a fact clause (numbers first, from the payload) plus a thought clause chosen by
lens and situation. Candidates are tried in a day-rotated order until one passes the same
gate the model's line must pass. The last resort is the fact clause alone, which is checked
for everything but similarity, so the display never blanks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.summary.gate import (
    GateResult,
    Recent,
    check_ban,
    check_device_line,
    check_grounding,
    check_hard_day,
    check_length,
    check_names,
    check_wellness,
)
from app.summary.payload import Payload

ULTIMATE_LINE = "Today is on the record; the numbers are on the web page."

WELLNESS_LABELS = {
    "hrv_ms": ("HRV", "ms"),
    "resting_hr": ("Resting heart rate", "bpm"),
    "vo2_max": ("VO2 max", ""),
    "daylight_min": ("Daylight", "min"),
}

THOUGHTS: dict[str, dict[str, tuple[str, ...]]] = {
    "control": {
        "train": (
            "The effort was yours to give, and you gave it.",
            "What you could decide today, you decided.",
            "One thing in your hands, handled.",
        ),
        "rest": (
            "You control the choice to rest, not the clock.",
            "The schedule is not yours; the response to it is.",
            "What was in reach today was rest, and you took it.",
        ),
        "missing": (
            "Not knowing is fine. Guessing is what to avoid.",
            "The record is late; the day still happened.",
            "The data will come when it comes.",
        ),
    },
    "habit": {
        "train": (
            "What you repeat is what you become.",
            "A habit is built one unremarkable day at a time.",
            "The dot is small; the pattern behind it is not.",
        ),
        "rest": (
            "Rest is part of the pattern, not a gap in it.",
            "Habits are kept in weeks, not in single days.",
            "The habit is the week; today is one of its days.",
        ),
        "missing": (
            "The habit does not need the record to be real.",
            "The pattern holds even where the data does not.",
            "A late record changes nothing about the day.",
        ),
    },
    "non-attachment": {
        "train": (
            "The effort is yours; the number is just the number.",
            "Done, and then let go of.",
            "The work was the point; the dot is only its shadow.",
        ),
        "rest": (
            "Begin again tomorrow, without drama.",
            "Nothing is owed to yesterday.",
            "A day without a dot is a day, not a verdict.",
        ),
        "missing": (
            "The result is not yours to hold; neither is the record.",
            "Unknown is a fine thing for a number to be.",
            "Let the gap be a gap.",
        ),
    },
    "self-awareness": {
        "train": (
            "Notice the pull to do more, and notice it is not the same as needing to.",
            "Notice what the number makes you want, then decide anyway.",
            "The feeling after is information, not instruction.",
        ),
        "rest": (
            "Notice the urge to call this a miss. It is not one.",
            "The itch to make up for today is worth noticing, not obeying.",
            "Rest feels like nothing; that feeling is the bias.",
        ),
        "missing": (
            "Notice how much a missing number pulls at you.",
            "The urge to fill a blank with a guess is worth watching.",
            "A gap in the data is not a gap in the day.",
        ),
    },
    "resilience": {
        "train": (
            "Discomfort held, work done.",
            "The hard part was the start, and the start is behind you.",
            "Steady beats dramatic, and this was steady.",
        ),
        "rest": (
            "Recovery is where resilience is actually built.",
            "Holding back is also a kind of holding.",
            "The quiet day carries the loud one.",
        ),
        "missing": (
            "A day with no record is still a day you got through.",
            "The gap is uncomfortable and harmless.",
            "Nothing here needs fixing.",
        ),
    },
    "rest-as-work": {
        "train": (
            "Tonight the work is to let this land.",
            "The session is done; the rest of the work is sleep.",
            "Adaptation happens after, not during.",
        ),
        "rest": (
            "Rest is not the absence of training. It is where the training lands.",
            "The rest day is doing its job while you do nothing.",
            "Today's work was recovery, and it counts.",
        ),
        "missing": (
            "Rest does not need a reading to count.",
            "The night did its work whether or not it was logged.",
            "Sleep happened; the record is what is late.",
        ),
    },
    "plain gratitude": {
        "train": (
            "A body that could do this today is worth a quiet thanks.",
            "Good to have had the hour and the legs for it.",
            "That was a good one to have.",
        ),
        "rest": (
            "A slow day is a good thing to have had.",
            "Good to have a day with room in it.",
            "Nothing to add to today, which is its own gift.",
        ),
        "missing": (
            "The day was fine with or without its numbers.",
            "Grateful for a day that needed no measuring.",
            "Some days are not for the record.",
        ),
    },
}


@dataclass(frozen=True)
class FallbackResult:
    line: str
    web_line: str | None
    gate: GateResult
    last_resort: bool = False


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def fact_clause(payload: Payload) -> str:
    prefix = "away from home, " if payload.cell.day_type == "travel" else ""
    return _cap(prefix + _fact(payload))


def _fact(payload: Payload) -> str:
    d = payload.data
    today, week, cell, load = d["today"], d["week"], payload.cell, d["load"]
    if cell.completeness == "health-delayed":
        return "health data has not arrived; last night's sleep is unknown."
    if today.get("book_finished_today") and today.get("book_title"):
        books = d["books"]
        return f"finished *{today['book_title']}*, {books['ytd']} of {books['target']} this year."
    if cell.completeness == "workout-without-hr":
        minutes = today.get("workout_minutes")
        session = f"a {minutes}-minute session" if minutes else "a session"
        return f"{session} with no heart-rate data, so no dot."
    sleep = today.get("sleep_hours")
    sleep_text = f"{sleep} h of sleep" if sleep is not None else "sleep unrecorded"
    if cell.day_type == "race-week" and week.get("dots_word") and load.get("balance") is not None:
        return f"{week['dots_word']} dot by {d['weekday']}, load balance {load['balance']}."
    if today.get("quality_workout"):
        if cell.streak_state == "never-started" and week.get("quality_workouts") == 1:
            return f"first quality workout on record, load {today['workout_load']}."
        word = week.get("dots_word") or "a"
        if today.get("workout_load") is not None:
            return f"{word} dot this week, load {today['workout_load']}."
        return f"{word} dot this week, {sleep_text}."
    last_count = week.get("last_week_quality_workouts")
    if cell.streak_state == "broken-last-week" and last_count is not None:
        dots = {0: "no dots", 1: "one dot", 2: "two dots"}.get(last_count, f"{last_count} dots")
        return f"{dots} last week, {sleep_text}."
    if d["yesterday"].get("quality_workout") and d["time_of_day"] == "morning":
        return f"yesterday's dot landed, {sleep_text}."
    return f"no workout, {sleep_text}."


def _situation(payload: Payload) -> str:
    if payload.cell.completeness == "health-delayed":
        return "missing"
    if payload.cell.completeness == "workout-without-hr":
        return "rest"
    return "train" if payload.data["today"].get("quality_workout") else "rest"


def web_fact(payload: Payload) -> str | None:
    fact = payload.data.get("wellness_fact")
    if not fact:
        return None
    label, unit = WELLNESS_LABELS[fact["metric"]]
    unit_text = f" {unit}" if unit else ""
    return (
        f"{label} {fact['value']}{unit_text} against a {fact['baseline']}{unit_text} "
        f"baseline, {fact['direction']} the band."
    )


def candidates(payload: Payload) -> list[str]:
    fact = fact_clause(payload)
    thoughts = THOUGHTS[payload.lens][_situation(payload)]
    start = date.fromisoformat(payload.day_local).toordinal() % len(thoughts)
    ordered = thoughts[start:] + thoughts[:start]
    return [f"{fact} {thought}" for thought in ordered]


def fallback_line(payload: Payload, recent: Recent, threshold: float) -> FallbackResult:
    web = web_fact(payload)
    for line in candidates(payload):
        result = check_device_line(line, payload, recent, threshold)
        if result.ok:
            return FallbackResult(line, web, result)
    fact = fact_clause(payload)
    reasons = (
        check_length(fact)
        or check_grounding(fact, payload)
        or check_ban(fact)
        or check_names(fact, recent.sources_named)
        or check_hard_day(fact, payload)
        or check_wellness(fact, payload)
    )
    if reasons:
        note = f"last resort: fact clause rejected ({'; '.join(reasons)}), constant line used"
        return FallbackResult(ULTIMATE_LINE, web, GateResult(True, (note,)), True)
    note = "last resort: similarity not checked"
    return FallbackResult(fact, web, GateResult(True, (note,)), True)
