"""Table `judged_workouts`: a day credited with a quality workout by a model's verdict on
its heart-rate aggregates (notes.txt § Goal model, Judged workout).

Rules the engine and the job rely on:
- A day is judged only when it has a max heart rate on record, lies in the current or the
  previous home-timezone week, and has no scored activity at or over the bar and no manual
  override (the caller filters those; this module filters the rest).
- `inputs_hash` is the sha256 of the day's heart-rate fields (HASHED_KEYS: max, avg, min,
  resolution, bouts); a day whose stored hash equals the current one is never sent again,
  whatever its verdict. A later push that changes a heart-rate field re-judges the day; a
  'denied' row (Max removed the credit on /workouts) stands until those inputs change, so
  the model never re-credits a day Max rejected.
- Spend goes through app.summary.spend, priced at the judge's own model: the cap check runs
  before every call and a refused call is not a judgement: the day stays unstored and is
  retried next recompute.
- Every answered call is stored. A verdict the parser or the grounding gate rejects, or a
  response that stopped for any reason but `end_turn`, becomes a 'failed' row with the hash
  and the failure in `error`, so the day is not sent again until its inputs change (Bartek,
  2026-10-07: unstored failures were re-called every tick). A transient API error
  (connection, 429, 529) stores nothing: the run stops there and the day is retried next
  run. No verdict is ever guessed, and a failure never revokes an earlier yes.
- `hr_resolution` is 'minutes' only when the day holds at least MIN_MINUTE_ROWS minute rows
  whose max lies within MINUTE_MAX_TOLERANCE of the day's heart_rate_max; a thin or partial
  minute series would describe near-empty bouts as the whole day, so it is sent as 'daily'
  with no bouts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any

import anthropic

from app.config import Settings
from app.metrics.judge_prompt import INPUT_KEYS, PROMPT_NUMBERS, REASON_MAX, request_body
from app.metrics.zones import zone_floors, zone_of
from app.summary import spend
from app.timeutil import from_utc_iso, local_day, now_utc, to_utc_iso

TABLE = "judged_workouts"
YES, NO, DENIED, FAILED = "yes", "no", "denied", "failed"
MIN_MINUTE_ROWS = 60
MINUTE_MAX_TOLERANCE = 0.10
log = logging.getLogger(__name__)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_REASON_TAIL = re.compile(r"""[\s{}'"\]\[]+$""")
_NON_ASCII = re.compile(r"[^\x20-\x7e]+")


@dataclass(frozen=True)
class Judgement:
    day_local: str
    verdict: str
    confidence: float
    reason: str
    model: str
    inputs_hash: str
    created_at_utc: str
    error: str = ""

    @property
    def credited(self) -> bool:
        return self.verdict == YES

    @property
    def failed(self) -> bool:
        return self.verdict == FAILED


def day_inputs(
    conn: sqlite3.Connection, day_local: str, settings: Settings
) -> dict[str, Any] | None:
    """The day's whole-day aggregates, or None without a max heart rate."""
    values: dict[str, Any] = {"day_local": day_local}
    for row in conn.execute(
        "SELECT metric, value FROM wellness_daily WHERE day_local = ?", (day_local,)
    ):
        if row["metric"] in INPUT_KEYS:
            values[row["metric"]] = round(float(row["value"]), 1)
    steps = conn.execute(
        "SELECT steps FROM steps_daily WHERE day_local = ?", (day_local,)
    ).fetchone()
    if steps is not None:
        values["steps"] = int(steps["steps"])
    if values.get("heart_rate_max") is None:
        return None
    bouts = (
        day_bouts(conn, day_local, settings)
        if minutes_cover_the_day(conn, day_local, values["heart_rate_max"])
        else None
    )
    values["hr_resolution"] = "minutes" if bouts is not None else "daily"
    out: dict[str, Any] = {key: values.get(key) for key in INPUT_KEYS}
    out["bouts"] = bouts or []
    return out


def minutes_cover_the_day(conn: sqlite3.Connection, day_local: str, daily_max: float) -> bool:
    """At least MIN_MINUTE_ROWS minute rows whose max agrees with the day's max heart rate
    to within MINUTE_MAX_TOLERANCE; anything thinner is not a minute-resolution day."""
    row = conn.execute(
        "SELECT COUNT(*) AS n, MAX(hr_max) AS peak FROM hr_minutes WHERE day_local = ? "
        "AND hr_avg IS NOT NULL",
        (day_local,),
    ).fetchone()
    if row["n"] < MIN_MINUTE_ROWS or row["peak"] is None:
        return False
    return abs(float(row["peak"]) - daily_max) <= MINUTE_MAX_TOLERANCE * daily_max


BOUT_GAP_MIN = 3
BOUT_KEYS = ("duration_min", "avg_hr", "peak_hr", "z1", "z2", "z3", "z4", "z5", "load")


def day_bouts(
    conn: sqlite3.Connection, day_local: str, settings: Settings
) -> list[dict[str, Any]] | None:
    """Contiguous runs of minutes at or above the zone-one floor (gaps of up to
    BOUT_GAP_MIN minutes tolerated), each described by duration and intensity only, ordered
    by load. None when the day has no minute rows."""
    rows = conn.execute(
        "SELECT minute_utc, hr_avg FROM hr_minutes WHERE day_local = ? AND hr_avg IS NOT NULL "
        "ORDER BY minute_utc",
        (day_local,),
    ).fetchall()
    if not rows:
        return None
    floors = zone_floors(settings.hr_max, settings.zones_pct)
    runs: list[list[tuple[datetime, float]]] = []
    for row in rows:
        moment, bpm = from_utc_iso(row["minute_utc"]), float(row["hr_avg"])
        if zone_of(bpm, floors) == 0:
            continue
        if runs and (moment - runs[-1][-1][0]) <= timedelta(minutes=BOUT_GAP_MIN + 1):
            runs[-1].append((moment, bpm))
        else:
            runs.append([(moment, bpm)])
    bouts = []
    for run in runs:
        zones = [0] * 6
        for _, bpm in run:
            zones[zone_of(bpm, floors)] += 1
        bouts.append(
            {
                "duration_min": int((run[-1][0] - run[0][0]).total_seconds() // 60) + 1,
                "avg_hr": round(sum(b for _, b in run) / len(run)),
                "peak_hr": round(max(b for _, b in run)),
                **{f"z{z}": zones[z] for z in range(1, 6)},
                "load": sum(z * zones[z] for z in range(1, 6)),
            }
        )
    return sorted(bouts, key=lambda b: b["load"], reverse=True)


HASHED_KEYS = ("heart_rate_max", "heart_rate_avg", "heart_rate_min", "hr_resolution", "bouts")


def inputs_hash(inputs: dict[str, Any]) -> str:
    """Over the heart-rate fields only (HASHED_KEYS); steps, daylight, HRV, resting HR and
    VO2 max still go in the payload but a change to them alone never re-judges a day."""
    hashed = {key: inputs.get(key) for key in HASHED_KEYS}
    return hashlib.sha256(json.dumps(hashed, sort_keys=True).encode()).hexdigest()


def window(now: datetime, tz: str) -> tuple[str, str]:
    """First day of last week to today, home timezone, Monday-start weeks."""
    today = date.fromisoformat(local_day(now, tz))
    first = today - timedelta(days=today.weekday() + 7)
    return first.isoformat(), today.isoformat()


ZONE_NUMBERS = frozenset(str(n) for n in range(1, 6))


def grounded(reason: str, inputs: dict[str, Any]) -> bool:
    """Every number in the reason is an input value (daily or bout), a bare zone number one
    to five (the prompt asks for zones in words, but "zone 3" is not an invented figure), or
    a constant the prompt itself states (PROMPT_NUMBERS: the bar, max HR, the zone bounds)."""
    values = [v for v in inputs.values() if isinstance(v, (int, float))]
    values += [v for bout in inputs.get("bouts") or [] for v in bout.values()]
    given = {str(v) for v in values}
    given |= {str(int(v)) for v in values if isinstance(v, float)}
    given |= ZONE_NUMBERS | PROMPT_NUMBERS
    return all(n in given or _plain(n) in given for n in _NUMBER.findall(reason))


def _plain(token: str) -> str:
    """'45.0' reads as '45'; an integer keeps its zeros ('300' is never '3')."""
    return token.rstrip("0").rstrip(".") if "." in token else token


def stored(conn: sqlite3.Connection, day_local: str) -> Judgement | None:
    row = conn.execute(f"SELECT * FROM {TABLE} WHERE day_local = ?", (day_local,)).fetchone()
    return Judgement(**dict(row)) if row else None


def store(conn: sqlite3.Connection, judgement: Judgement) -> None:
    conn.execute(
        f"INSERT INTO {TABLE} (day_local, verdict, confidence, reason, model, inputs_hash, "
        "created_at_utc, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (day_local) DO "
        "UPDATE SET verdict = excluded.verdict, confidence = excluded.confidence, "
        "reason = excluded.reason, model = excluded.model, inputs_hash = excluded.inputs_hash, "
        "created_at_utc = excluded.created_at_utc, error = excluded.error",
        (
            judgement.day_local,
            judgement.verdict,
            judgement.confidence,
            judgement.reason,
            judgement.model,
            judgement.inputs_hash,
            judgement.created_at_utc,
            judgement.error,
        ),
    )


def deny(conn: sqlite3.Connection, day_local: str, now: datetime | None = None) -> bool:
    """Max's removal of a judged credit: the row becomes 'denied' with its hash kept."""
    cursor = conn.execute(
        f"UPDATE {TABLE} SET verdict = ?, created_at_utc = ? WHERE day_local = ?",
        (DENIED, to_utc_iso(now or now_utc()), day_local),
    )
    return cursor.rowcount > 0


def list_judgements(conn: sqlite3.Connection) -> list[Judgement]:
    return [
        Judgement(**dict(row))
        for row in conn.execute(f"SELECT * FROM {TABLE} ORDER BY day_local DESC")
    ]


def clean_stored_reasons(conn: sqlite3.Connection) -> int:
    """Re-apply `clean_reason` to every stored reason, touching nothing else; the number of
    rows whose reason changed."""
    changed = 0
    for row in list_judgements(conn):
        cleaned = clean_reason(row.reason)
        if cleaned != row.reason:
            conn.execute(
                f"UPDATE {TABLE} SET reason = ? WHERE day_local = ?", (cleaned, row.day_local)
            )
            changed += 1
    conn.commit()
    return changed


def read_credits(conn: sqlite3.Connection) -> dict[date, Judgement]:
    """The engine's input: every credited day keyed by its home-timezone day."""
    return {date.fromisoformat(j.day_local): j for j in list_judgements(conn) if j.credited}


def parse_verdict(response: Any) -> tuple[bool, float, str] | str:
    """The verdict from the response's text blocks (thinking blocks are skipped), parsed
    strictly against the schema: trailing garbage, a missing or mistyped field, or a stop
    reason other than end_turn is a failure, never a salvaged partial. A complete verdict
    whose reason string ends in stray braces or quotes (the model closes the object inside
    the string under the schema grammar; seen live 2026-10-07) keeps the clause without
    its tail."""
    stop = getattr(response, "stop_reason", None)
    if stop != "end_turn":
        return f"stop_reason {stop}"
    text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
    try:
        data = json.loads(text)
    except ValueError as exc:
        return f"unparseable verdict: {exc}"
    if not isinstance(data, dict) or set(data) != {"workout", "confidence", "reason"}:
        return "unparseable verdict: keys do not match the schema"
    workout, confidence, reason = data["workout"], data["confidence"], data["reason"]
    if not isinstance(workout, bool) or isinstance(confidence, bool):
        return "unparseable verdict: workout is not a boolean"
    if not isinstance(confidence, (int, float)) or not isinstance(reason, str):
        return "unparseable verdict: confidence or reason mistyped"
    return workout, float(confidence), clean_reason(reason)


_SENTENCE_END = re.compile(r"\.(?!\d)")


def clean_reason(reason: str) -> str:
    """The model sometimes closes the JSON object inside the reason string (seen live
    2026-10-07: `session.}`, `evident.}`, `170.0.','x':''}`). A brace or quote after the
    last sentence-ending period (one not followed by a digit) cuts the reason at that
    period; any bare tail of braces, quotes or brackets is stripped either way; anything
    outside printable ASCII is dropped first (seen live 2026-10-07: "occurred.Â ĂŻ"). A reason
    over REASON_MAX is cut at the last sentence end that fits, never mid-word (seen live:
    "...sustained effort.Day too c")."""
    reason = _NON_ASCII.sub("", reason)
    at_limit = len(reason) >= REASON_MAX
    ends = [m.end() for m in _SENTENCE_END.finditer(reason)]
    if ends and any(c in reason[ends[-1] :] for c in "{}'\""):
        reason = reason[: ends[-1]]
    reason = _REASON_TAIL.sub("", reason).strip()
    if len(reason) > REASON_MAX or (at_limit and not reason.endswith(".")):
        fits = [end for end in ends if end <= REASON_MAX]
        reason = reason[: fits[-1]] if fits else reason[:REASON_MAX]
    return reason.strip()


def judge_day(
    conn: sqlite3.Connection,
    settings: Settings,
    client: Any,
    day_local: str,
    now: datetime | None = None,
) -> Judgement | str:
    """Judge one day if its inputs changed; returns the judgement or why no call was made."""
    inputs = day_inputs(conn, day_local, settings)
    if inputs is None:
        return "no heart rate"
    digest = inputs_hash(inputs)
    previous = stored(conn, day_local)
    if previous is not None and previous.inputs_hash == digest:
        return "unchanged"
    model = settings.judge_model
    request = request_body(inputs, model)
    cap = spend.cap_check(conn, settings, request, now)
    if not cap.allowed:
        return f"cap: {cap.describe()}"
    moment = now or now_utc()
    try:
        response = client.messages.create(**request)
    except anthropic.APIError as exc:
        log.warning("judge %s: model unavailable: %s", day_local, exc)
        return f"unavailable: {exc}"
    spend.record(
        conn,
        settings,
        day_local,
        getattr(response, "_request_id", None),
        spend.Usage(
            getattr(response.usage, "input_tokens", 0) or 0,
            getattr(response.usage, "output_tokens", 0) or 0,
            getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        ),
        response.stop_reason,
        moment,
        model=model,
    )
    log.info("judge %s: stop_reason %s", day_local, getattr(response, "stop_reason", None))
    parsed = parse_verdict(response)
    if isinstance(parsed, str):
        log.warning("judge %s: %s", day_local, parsed)
        store_failure(conn, previous, day_local, model, digest, moment, parsed)
        return parsed
    workout, confidence, reason = parsed
    if not grounded(reason, inputs):
        log.warning("judge %s: reason cites a number not in the inputs: %r", day_local, reason)
        store_failure(conn, previous, day_local, model, digest, moment, f"ungrounded: {reason}")
        return "ungrounded reason"
    if previous is not None and previous.credited and not workout:
        log.info("judge %s: a later no never revokes a yes; keeping the credit", day_local)
        store(conn, replace(previous, inputs_hash=digest))
        return "kept"
    judgement = Judgement(
        day_local, YES if workout else NO, confidence, reason, model, digest, to_utc_iso(moment)
    )
    store(conn, judgement)
    return judgement


def store_failure(
    conn: sqlite3.Connection,
    previous: Judgement | None,
    day_local: str,
    model: str,
    digest: str,
    moment: datetime,
    error: str,
) -> None:
    """A failed call is stored under the current hash so it is not repeated; an earlier yes
    keeps its credit (a failure revokes nothing)."""
    if previous is not None and previous.credited:
        store(conn, replace(previous, inputs_hash=digest, error=error))
        return
    store(conn, Judgement(day_local, FAILED, 0.0, "", model, digest, to_utc_iso(moment), error))
    conn.commit()


@dataclass
class JudgeRun:
    verdicts: list[Judgement] = field(default_factory=list)
    failed: int = 0
    kept: int = 0
    skipped: int = 0

    @property
    def yes(self) -> int:
        return sum(1 for j in self.verdicts if j.credited)

    @property
    def judged(self) -> int:
        """Calls made: verdicts, failures and a 'no' that kept an earlier yes."""
        return len(self.verdicts) + self.failed + self.kept

    def describe(self) -> str:
        return (
            f"judged {self.judged}, yes {self.yes}, failed {self.failed}, kept {self.kept}, "
            f"skipped (hash) {self.skipped}"
        )


def judge_window(
    conn: sqlite3.Connection,
    settings: Settings,
    client: Any,
    skip: set[str],
    now: datetime | None = None,
) -> JudgeRun:
    """Judge every day in the two-week window that is not in `skip` (days already credited
    by a scored activity or a manual override). Commits after each verdict and logs one
    summary line per run."""
    first, last = window(now or now_utc(), settings.home_tz)
    run = JudgeRun()
    for row in conn.execute(
        "SELECT DISTINCT day_local FROM wellness_daily WHERE metric = 'heart_rate_max' "
        "AND day_local BETWEEN ? AND ? ORDER BY day_local",
        (first, last),
    ):
        day = row["day_local"]
        if day in skip:
            continue
        result = judge_day(conn, settings, client, day, now)
        if isinstance(result, Judgement):
            run.verdicts.append(result)
            conn.commit()
        elif result == "unchanged":
            run.skipped += 1
        elif result == "kept":
            run.kept += 1
            conn.commit()
        elif result == "no heart rate":
            continue
        elif result.startswith(("cap:", "unavailable:")):
            log.info("judge: stopping this run at %s: %s", day, result)
            break
        else:
            run.failed += 1
    log.info("judge: %s", run.describe())
    return run
