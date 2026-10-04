"""Rule-based copy for when the model is unavailable, over budget, or rejected twice.

The line is read in the morning and describes yesterday. It is a fact clause (numbers
first, from the payload, at most FACT_MAX characters) plus a thought chosen by lens and
situation (at most THOUGHT_MAX characters), so every candidate fits the 110-character device
line whatever the numbers or the book title. Candidates are tried in a day-rotated order
until one passes the same gate the model's line must pass. The last resort is the fact
clause alone, checked for everything but similarity, so the display never blanks.

Situations: train (a dot), rest (no workout), nodot (a session without heart-rate data:
the work happened, the record did not score it), under (a scored workout below the load
bar: the nodot thoughts that do not blame the record), missing (health data not arrived).
No thought refers to a time of day or tells him what he feels (Ingrid, 2026-10-02).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.summary.gate import (
    DEVICE_MAX,
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

FACT_MAX = 60
THOUGHT_MAX = DEVICE_MAX - FACT_MAX - 1
ULTIMATE_LINE = "Today is on the record; the numbers are on the web page."
COUNT_WORDS = ("No", "One", "Two", "Three", "Four", "Five", "Six", "Seven")

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
            "What was yours to decide, you decided.",
            "One thing in your hands, handled.",
        ),
        "rest": (
            "The schedule is not yours; the response is.",
            "What was in reach was rest, and you took it.",
            "You chose the pause; that was yours to choose.",
        ),
        "nodot": (
            "The work was yours; the record was not.",
            "You control the effort, not the sensor.",
            "What you did is done, scored or not.",
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
            "A habit is built from unremarkable days.",
            "The dot is small; the pattern behind it is not.",
        ),
        "rest": (
            "Rest is part of the pattern, not a gap in it.",
            "Habits are kept in weeks, not single days.",
            "The week is the habit; this was one of its days.",
        ),
        "nodot": (
            "The habit was kept; the record missed it.",
            "Showing up is the habit. The rest is bookkeeping.",
            "The pattern held even where the data did not.",
        ),
        "missing": (
            "The habit does not need the record to be real.",
            "A late record changes nothing about the day.",
            "Habits are kept in weeks, not in reports.",
        ),
    },
    "non-attachment": {
        "train": (
            "The effort is yours; the number is just a number.",
            "Done, and then let go of.",
            "The work was the point; the dot is its shadow.",
        ),
        "rest": (
            "Begin again today, without drama.",
            "Nothing is owed to the day before.",
            "A day without a dot is a day, not a verdict.",
        ),
        "nodot": (
            "The effort is yours; the score is not.",
            "Unscored work is still work.",
            "Let the missing dot go; keep the work.",
        ),
        "missing": (
            "The result is not yours to hold, nor the record.",
            "Unknown is a fine thing for a number to be.",
            "Let the gap be a gap.",
        ),
    },
    "self-awareness": {
        "train": (
            "Notice what the number makes you want.",
            "A feeling after is information, not orders.",
            "Notice the pull to do more; it is not a need.",
        ),
        "rest": (
            "Worth noticing how you count a day like this.",
            "Rest is easy to undercount. It still counts.",
            "A quiet day is data too.",
        ),
        "nodot": (
            "A missing number is easy to take personally.",
            "Did the dot matter, or the work?",
            "The score is a reading, not the whole story.",
        ),
        "missing": (
            "A blank is worth noticing, not filling.",
            "A gap in the data is not a gap in the day.",
            "Guesses fill blanks too eagerly; let it be.",
        ),
    },
    "resilience": {
        "train": (
            "Discomfort held, work done.",
            "The hard part was starting; that is behind you.",
            "Steady beats dramatic, and this was steady.",
        ),
        "rest": (
            "Recovery is where resilience is actually built.",
            "Holding back is also a kind of holding.",
            "The quiet day carries the loud one.",
        ),
        "nodot": (
            "The work held up even when the record did not.",
            "A missed reading is not a missed effort.",
            "The session counted for you, if not the chart.",
        ),
        "missing": (
            "A day with no record is still a day lived.",
            "The gap is uncomfortable and harmless.",
            "Nothing here needs fixing.",
        ),
    },
    "rest-as-work": {
        "train": (
            "Adaptation happens after, not during.",
            "The work is done; now it gets absorbed.",
            "Recovery finishes what the workout started.",
        ),
        "rest": (
            "Rest is where the training lands.",
            "The rest day did its job while you did nothing.",
            "Recovery was the work, and it counts.",
        ),
        "nodot": (
            "The body counted it, even if the chart did not.",
            "Rest follows work, scored or not.",
            "Adaptation does not need a heart-rate trace.",
        ),
        "missing": (
            "Rest does not need a reading to count.",
            "The night did its work, logged or not.",
            "Sleep happened; the record is what is late.",
        ),
    },
    "plain gratitude": {
        "train": (
            "A body that could do this is worth thanking.",
            "Good to have had the time and legs for it.",
            "That was a good one to have.",
        ),
        "rest": (
            "A slow day is a good thing to have had.",
            "Good to have a day with room in it.",
            "Nothing to add to the day, which is a gift.",
        ),
        "nodot": (
            "Good to have had the legs for it, dot or not.",
            "Thanks to the body that did the work.",
            "The work was a gift to yourself, scored or not.",
        ),
        "missing": (
            "The day was fine with or without its numbers.",
            "Grateful for a day that needed no measuring.",
            "Some days are not for the record.",
        ),
    },
}


RECORD_ONLY_THOUGHTS = frozenset(
    {
        "The work was yours; the record was not.",
        "You control the effort, not the sensor.",
        "The habit was kept; the record missed it.",
        "The pattern held even where the data did not.",
        "A missing number is easy to take personally.",
        "The work held up even when the record did not.",
        "A missed reading is not a missed effort.",
        "Adaptation does not need a heart-rate trace.",
    }
)


@dataclass(frozen=True)
class FallbackResult:
    line: str
    web_line: str | None
    gate: GateResult
    last_resort: bool = False


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _count(n: int, noun: str) -> str:
    word = COUNT_WORDS[n] if 0 <= n < len(COUNT_WORDS) else str(n)
    return f"{word} {noun}{'' if n == 1 else 's'}"


def fact_options(payload: Payload) -> list[str]:
    """The fact clause, longest first; the first that fits FACT_MAX is used."""
    d = payload.data
    day, week, cell, load, books = d["day"], d["week"], payload.cell, d["load"], d["books"]
    if cell.completeness == "health-delayed":
        core = ["yesterday's health data has not arrived."]
    elif day.get("book_finished"):
        title, ytd, target = day.get("book_title"), books.get("ytd"), books.get("target")
        tally = f", {ytd} of {target}" if ytd is not None else ""
        core = [f"finished a book yesterday{tally}."]
        if ytd is not None:
            core.insert(0, f"finished a book yesterday{tally} this year.")
        if title:
            core[:0] = [
                f"finished *{title}* yesterday{tally} this year.",
                f"finished *{title}* yesterday{tally}.",
            ]
    elif cell.completeness == "workout-without-hr":
        minutes = day.get("workout_minutes")
        session = f"a {minutes}-minute session" if minutes else "a session"
        core = [f"{session} yesterday, no heart rate, no dot."]
    else:
        sleep = day.get("sleep_hours")
        sleep_text = f"{sleep} h of sleep" if sleep is not None else "sleep unrecorded"
        dots = week.get("quality_workouts")
        if (
            cell.day_type == "race-week"
            and isinstance(dots, int)
            and dots > 0
            and load.get("balance") is not None
        ):
            core = [
                f"{_count(dots, 'dot').lower()} by {d['weekday']}, load balance {load['balance']}."
            ]
        elif day.get("quality_workout"):
            workout_load = day.get("workout_load")
            tail = f"load {workout_load}" if workout_load is not None else sleep_text
            word = week.get("dots_word")
            if cell.streak_state == "never-started" and dots == 1 and workout_load is not None:
                core = [f"first quality workout on record yesterday, {tail}."]
            elif word and word != "no":
                core = [f"yesterday made the {word} dot {week['relation']}, {tail}."]
            else:
                core = [f"yesterday earned a dot, {tail}."]
        elif (day.get("workout_count") or 0) > 0:
            session = f"{_count(int(day['workout_count']), 'workout').lower()} yesterday"
            workout_load = day.get("workout_load")
            core = [f"{session}, no dot."]
            if workout_load is not None:
                core[:0] = [
                    f"{session}, load {workout_load}, under the bar, no dot.",
                    f"{session}, load {workout_load}, no dot.",
                ]
        elif (
            cell.streak_state == "broken-last-week"
            and week.get("previous_week_quality_workouts") is not None
        ):
            when = "last week" if week["relation"] == "this week" else "the week before"
            count = _count(int(week["previous_week_quality_workouts"]), "dot").lower()
            core = [f"{count} {when}, {sleep_text}."]
        else:
            core = [f"no workout yesterday, {sleep_text}."]
    options: list[str] = []
    for clause in core:
        if cell.day_type == "travel":
            options.append(_cap(f"away from home, {clause}"))
        options.append(_cap(clause))
    return options


def fact_clause(payload: Payload) -> str:
    options = fact_options(payload)
    return next((o for o in options if len(o) <= FACT_MAX), options[-1])


def _situation(payload: Payload) -> str:
    if payload.cell.completeness == "health-delayed":
        return "missing"
    if payload.cell.completeness == "workout-without-hr":
        return "nodot"
    day = payload.data["day"]
    if day.get("quality_workout"):
        return "train"
    return "under" if (day.get("workout_count") or 0) > 0 else "rest"


def thoughts_for(lens: str, situation: str) -> tuple[str, ...]:
    if situation == "under":
        return tuple(t for t in THOUGHTS[lens]["nodot"] if t not in RECORD_ONLY_THOUGHTS)
    return THOUGHTS[lens][situation]


def web_fact(payload: Payload) -> str | None:
    parts: list[str] = []
    fact = payload.data.get("wellness_fact")
    if fact:
        label, unit = WELLNESS_LABELS[fact["metric"]]
        unit_text = f" {unit}" if unit else ""
        if fact.get("baseline") is not None:
            parts.append(
                f"{label} {fact['value']}{unit_text} against a {fact['baseline']}{unit_text} "
                f"baseline, {fact['direction']} the band."
            )
        else:
            parts.append(f"{label} {fact['value']}{unit_text}, {fact['direction']} the band.")
    title = payload.book_title
    if title and title not in fact_clause(payload):
        parts.append(f"The book was *{title}*.")
    return " ".join(parts) or None


def candidates(payload: Payload) -> list[str]:
    fact = fact_clause(payload)
    thoughts = thoughts_for(payload.lens, _situation(payload))
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
