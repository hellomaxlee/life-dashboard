"""The judged-workout prompt: whole-day heart-rate aggregates in, a structured verdict out.

Inputs are day-level numbers only (invariant "Health data stays home"): no samples, no
sub-day timestamps, no free text. The schema is enforced by the API's structured output,
so the verdict is parsed, never scraped from prose.

Every number the system prompt states lives in PROMPT_NUMBERS, which the grounding gate
(app/metrics/judge.py) allows alongside the inputs: the prompt and the gate are built from
the same constants, so neither can drift from the other.
"""

from __future__ import annotations

REASON_MAX = 140
MAX_TOKENS = 600
HR_MAX = 189
LOAD_BAR = 60
BAR_MINUTES = 30
MIN_BOUT_MINUTES = 10
BOUT_JOIN_MIN = 10
ZONE_BOUNDS = ((95, 112), (113, 131), (132, 150), (151, 169), (170, None))
ZONE_FLOORS = tuple(low for low, _ in ZONE_BOUNDS)

PROMPT_NUMBERS = frozenset(
    str(n)
    for n in (
        HR_MAX,
        LOAD_BAR,
        BAR_MINUTES,
        MIN_BOUT_MINUTES,
        BOUT_JOIN_MIN,
        *(b for pair in ZONE_BOUNDS for b in pair if b),
    )
)

SYSTEM_PROMPT = (
    "You are a fitness coach deciding whether one day's heart-rate pattern was a workout. "
    f"Max HR is {HR_MAX}; zones by percent of max: zone one {ZONE_BOUNDS[0][0]}-"
    f"{ZONE_BOUNDS[0][1]}, zone two {ZONE_BOUNDS[1][0]}-{ZONE_BOUNDS[1][1]}, zone three "
    f"{ZONE_BOUNDS[2][0]}-{ZONE_BOUNDS[2][1]}, zone four {ZONE_BOUNDS[3][0]}-"
    f"{ZONE_BOUNDS[3][1]}, zone five {ZONE_BOUNDS[4][0]}+. The goal model's bar is about "
    f"{BAR_MINUTES} minutes mostly in zone two, an Edwards load of {LOAD_BAR} (load = zone "
    "minutes weighted one to five). "
    "A sustained effort counts: a 20-minute HIIT session on a bike with real zone three to "
    "five minutes is a workout even under the bar. A brief spike does not: a two-minute "
    "sprint for a bus or a stressful meeting is not a workout, however high the peak; a bout "
    f"with fewer than {MIN_BOUT_MINUTES} minutes at or above zone two (zones two to five "
    "summed) is not a workout, however intense. "
    "When hr_resolution is 'minutes' you get the day's bouts (contiguous minutes at or above "
    "zone one, ordered by load) with duration, average and peak HR, zone minutes and load. "
    "Outside a recorded workout the watch reads the heart only every few minutes, so a bout "
    f"joins readings up to {BOUT_JOIN_MIN} minutes apart: a minute with no reading between "
    "two readings counts in the lower of their two zones, and observed_min is how many of "
    "the bout's minutes had a reading. A bout whose readings are sparse but all elevated is "
    "a sustained effort, not a spike. Judge the bout with the most minutes at or above zone "
    "two. When hr_resolution is 'daily' you get whole-day figures "
    "only: answer no, say the resolution is too low to tell a bout from a spike, and credit "
    "waits for minute data or a recorded workout. "
    "Reply with the JSON object only. The reason is one plain clause of at "
    f"most {REASON_MAX} characters, may cite only numbers given in the inputs or stated here, "
    "and names zones in words (zone three, never zone 3) so every digit in it is an input. "
    "The reason is read by Max on his dashboard: state what the numbers show in one sentence, "
    "no advice, no praise, no blame."
)
DAILY_RESOLUTION_RULE = "answer no, say the resolution is too low to tell a bout from a spike"
REASON_AUDIENCE_RULE = "no advice, no praise, no blame"

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "workout": {"type": "boolean"},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["workout", "confidence", "reason"],
    "additionalProperties": False,
}

INPUT_KEYS = (
    "day_local",
    "hr_resolution",
    "heart_rate_max",
    "heart_rate_avg",
    "heart_rate_min",
    "resting_heart_rate",
    "heart_rate_variability",
    "steps",
    "time_in_daylight",
    "vo2_max",
)


THINKING_OFF: dict[str, dict] = {
    "claude-sonnet-5-5": {"thinking": {"type": "between_tools"}, "effort": "low"},
}


def request_body(inputs: dict[str, object], model: str) -> dict:
    """The verdict schema as the output format. Haiku 4.5 (the pinned judge, Max 2026-10-07:
    "use haiku (cheapest model)") does not think unless asked and rejects `effort`, so it
    gets neither; a Sonnet 5.5 judge thinks by default and would spend the budget on it, so
    THINKING_OFF gives it the one accepted off-switch and low effort."""
    lines = [f"{key}: {inputs.get(key)}" for key in INPUT_KEYS]
    for index, bout in enumerate(inputs.get("bouts") or [], start=1):
        lines.append(f"bout {index}: " + ", ".join(f"{k} {v}" for k, v in bout.items()))
    body = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": "\n".join(lines)}],
        "output_config": {"format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
    }
    quiet = THINKING_OFF.get(model)
    if quiet:
        body["thinking"] = quiet["thinking"]
        body["output_config"]["effort"] = quiet["effort"]
    return body
