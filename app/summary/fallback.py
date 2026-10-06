"""Rule-based copy for when the model is unavailable, over budget, or rejected twice.

The line is read in the morning and follows the model's voice: the thought leads and the
day's numbers are optional context. On an ordinary day (a dot, or no workout on record)
the candidates are of three kinds, and the date decides which kind is tried first, so the
kinds take turns:
a quote from the bank for the day's lens with its author (`quotes.display`), a thought of
our own chosen by lens and situation, and a thought followed by the fact clause (the one day
in three that touches a number). A finished book is a win and is always named, after the
thought. A day with no workout on record is never called rest (Max, 2026-10-04: absence of
a record is not evidence of rest, weekends included): its fact clause is the sleep alone,
its thoughts speak of rest only as an idea, and the same gate check that binds the model
binds every line here. Three hard cases keep the plain fact in front, because the brief
requires naming what happened: health data not arrived (missing), a session without
heart-rate data (nodot), a scored workout under the load bar (under: the nodot thoughts
that do not blame the record).

The fact clause is at most FACT_MAX characters and a thought at most THOUGHT_MAX, and every
bank entry fits with its attribution, so every candidate fits the device line whatever the
numbers or the book title. Candidates are tried in order until one passes the same gate the
model's line must pass, so a quote or an author seen in the last 14 lines is skipped. The
last resort is the fact clause alone, checked for everything but similarity, so the display
never blanks. No thought refers to a time of day or tells him what he feels (Ingrid,
2026-10-02).
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
    check_rest_claim,
    check_wellness,
    without_title,
)
from app.summary.payload import Payload
from app.summary.quotes import display, for_lens

FACT_MAX = 60
THOUGHT_MAX = DEVICE_MAX - FACT_MAX - 1
HARD_SITUATIONS = frozenset({"missing", "nodot", "under"})
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
        "quiet": (
            "The schedule is not yours; the response is.",
            "What is in reach is enough to work with.",
            "The day is not yours to grade; the next choice is.",
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
        "quiet": (
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
        "quiet": (
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
        "quiet": (
            "Worth noticing how you count a day like this.",
            "Rest is easy to undercount. It still counts.",
            "What the record leaves out is worth noticing too.",
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
        "quiet": (
            "Recovery is where resilience is actually built.",
            "Holding back is also a kind of holding.",
            "Quiet days carry the loud ones.",
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
        "quiet": (
            "Rest is where the training lands.",
            "Rest does its work unobserved.",
            "Recovery is work too, and it counts.",
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
        "quiet": (
            "An ordinary day is a good thing to have had.",
            "Good to have had the day at all.",
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
        elif day.get("quality_workout") and _credited_by_hand(day, d["targets"]):
            word = week.get("dots_word")
            core = [f"yesterday's dot was logged by hand, {sleep_text}."]
            if word and word != "no":
                core.insert(0, f"yesterday made the {word} dot {week['relation']}, logged by hand.")
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
        elif sleep is not None:
            core = [f"{sleep} h of sleep yesterday."]
        else:
            core = ["no workout or sleep on record yesterday."]
    options: list[str] = []
    for clause in core:
        if cell.day_type == "travel":
            options.append(_cap(f"away from home, {clause}"))
        options.append(_cap(clause))
    return options


def _credited_by_hand(day: dict, targets: dict) -> bool:
    """The dot came from a manual override, not a scored workout: no load, or a load under
    the bar, so no line may speak of heart-rate load."""
    if day.get("manual_workout") is not True:
        return False
    load, bar = day.get("workout_load"), targets.get("load_bar")
    return load is None or (bar is not None and load < bar)


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
    return "under" if (day.get("workout_count") or 0) > 0 else "quiet"


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


def _rotated(items: tuple[str, ...], start: int) -> list[str]:
    if not items:
        return []
    start %= len(items)
    return list(items[start:] + items[:start])


def quote_lines(payload: Payload) -> list[str]:
    """The lens's bank entries as shown, starting one further on each time the lens returns."""
    ordinal = date.fromisoformat(payload.day_local).toordinal()
    entries = tuple(display(q) for q in for_lens(payload.lens))
    return _rotated(entries, ordinal // 7)


def candidates(payload: Payload) -> list[str]:
    fact = fact_clause(payload)
    situation = _situation(payload)
    ordinal = date.fromisoformat(payload.day_local).toordinal()
    thoughts = _rotated(thoughts_for(payload.lens, situation), ordinal)
    if situation in HARD_SITUATIONS:
        return [f"{fact} {thought}" for thought in thoughts]
    with_fact = [f"{thought} {fact}" for thought in thoughts]
    quotes = quote_lines(payload)
    if payload.data["day"].get("book_finished"):
        return with_fact + quotes + thoughts
    return [
        with_fact + quotes + thoughts,
        quotes + thoughts + with_fact,
        thoughts + quotes + with_fact,
    ][ordinal % 3]


def fallback_line(payload: Payload, recent: Recent, threshold: float) -> FallbackResult:
    web = web_fact(payload)
    for line in candidates(payload):
        result = check_device_line(line, payload, recent, threshold)
        if result.ok:
            return FallbackResult(line, web, result)
    fact = fact_clause(payload)
    text = without_title(fact, payload)
    reasons = (
        check_length(fact)
        or check_grounding(fact, payload)
        or check_ban(text)
        or check_names(text, recent.sources_named)
        or check_rest_claim(text, payload)
        or check_hard_day(text, payload)
        or check_wellness(text, payload)
    )
    if reasons:
        note = f"last resort: fact clause rejected ({'; '.join(reasons)}), constant line used"
        return FallbackResult(ULTIMATE_LINE, web, GateResult(True, (note,)), True)
    note = "last resort: similarity not checked"
    return FallbackResult(fact, web, GateResult(True, (note,)), True)
