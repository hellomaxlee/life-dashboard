"""ensure_month_feature(conn, settings, month): have the model author the month's feature
once, validate it, store it.

A stored feature is returned without a call unless `force`. Otherwise: check the attempt
guard, build the request, check the monthly cap (the summary's own, app/summary/spend.py),
stream one call, record its spend, pull the JSON object out of the reply, run it through
`spec.parse_feature` and the voice check, and on a rejection ask once more with the reason.
At most MAX_CALLS calls per run. Every way of not succeeding (no key, cap, an API error of
any kind, a cut-off reply, two rejections) returns a result that says why and stores no
feature; the renderer then draws its own calendar screen. Nothing is raised.

The attempt guard: a run that called the model and stored nothing leaves a row in
`month_feature_attempts`. An automatic run (not `force`) makes no call when a row exists for
today's home-timezone day or when the month already has MAX_AUTO_ATTEMPTS_PER_MONTH rows.
`force` skips the guard and never the cap.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

import anthropic

from app.config import Settings
from app.month import store
from app.month.prompt import request_body, user_message
from app.month.spec import MONTH, MonthFeature, SpecError, parse_feature
from app.summary import gate, spend
from app.summary import run as summary_run
from app.summary.run import API_BASE_URL, MessagesClient, ModelReply
from app.timeutil import local_day, now_utc

log = logging.getLogger(__name__)

MAX_CALLS = 2
MAX_AUTO_ATTEMPTS_PER_MONTH = 3
# Seconds without a byte before the stream is given up; the reply itself takes minutes.
STREAM_TIMEOUT_S = 120.0
OBJECT_STARTS_TRIED = 20
SOURCE_MODEL = "model"

STORED = "stored"
GENERATED = "generated"
SKIPPED = "skipped"
FAILED = "failed"


@dataclass(frozen=True)
class MonthResult:
    """`status` is stored (already there, no call), generated (authored and saved now),
    skipped (the attempt guard said no) or failed; `feature` is set for the first two."""

    month: str
    status: str
    feature: MonthFeature | None = None
    reason: str = ""
    calls: int = 0
    spend_usd: float = 0.0
    rejections: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.feature is not None


def make_client(settings: Settings) -> anthropic.Anthropic | None:
    if not settings.anthropic_api_key:
        return None
    return anthropic.Anthropic(
        api_key=settings.anthropic_api_key,
        base_url=API_BASE_URL,
        timeout=STREAM_TIMEOUT_S,
        max_retries=0,
    )


def build_request(
    conn: sqlite3.Connection, settings: Settings, month: str, rejection: str | None = None
) -> dict:
    message = user_message(month, store.previous_themes(conn, month), rejection)
    return request_body(message, settings.summary.model)


def call_model(client: MessagesClient, request: dict) -> ModelReply | str:
    """One streamed call. Returns the final reply, or a string naming why there is none.
    Nothing raised by the call or by reading the stream escapes."""
    try:
        with client.messages.stream(**request) as stream:
            return summary_run._reply_of(stream.get_final_message())
    except anthropic.AuthenticationError as exc:
        return f"authentication error: {exc.__class__.__name__}"
    except anthropic.RateLimitError as exc:
        return f"rate limited: {exc.__class__.__name__}"
    except anthropic.APIStatusError as exc:
        return f"api status {getattr(exc, 'status_code', '?')}: {exc.__class__.__name__}"
    except anthropic.APIConnectionError as exc:
        return f"connection error: {exc.__class__.__name__}"
    except anthropic.APIError as exc:
        return f"api error: {exc.__class__.__name__}"
    except Exception as exc:
        log.exception("month feature: the model call raised outside the SDK's error types")
        return f"unexpected error: {exc.__class__.__name__}"


def extract_object(text: str) -> dict | None:
    """The first JSON object in the reply, whatever stands around it (a code fence, a
    sentence before or after). None when no object decodes."""
    decoder = json.JSONDecoder()
    start = text.find("{")
    for _ in range(OBJECT_STARTS_TRIED):
        if start < 0:
            return None
        try:
            value, _end = decoder.raw_decode(text, start)
        except ValueError:
            value = None
        if isinstance(value, dict):
            return value
        start = text.find("{", start + 1)
    return None


def voice_problem(feature: MonthFeature) -> str | None:
    """The first text that breaks the voice rules, or None. The ban list is the daily
    summary's (`gate.check_ban`); words under a person's name are found with the gate's
    attribution patterns. The theme is web-only, so the spec lets digits through; not here."""
    texts = [("title", feature.title), ("theme", feature.theme)]
    for plate in feature.days:
        texts.append((f"day {plate.day} caption", plate.caption))
        texts.append((f"day {plate.day} note", plate.note))
    for where, text in texts:
        if not text:
            continue
        banned = gate.check_ban(text)
        if banned:
            return f"{where}: {banned[0]}"
        if gate.ATTRIBUTION.search(text) or (gate.quotations_in(text) and gate.persons_in(text)):
            return f"{where}: words attributed to a person are not allowed"
    if any(ch.isdigit() for ch in feature.theme):
        return "theme: digits are not allowed"
    return None


def judge(text: str, month: str) -> tuple[dict, MonthFeature] | str:
    """(the decoded object, the parsed feature), or the reason the reply is rejected."""
    raw = extract_object(text)
    if raw is None:
        return "the reply holds no JSON object"
    try:
        feature = parse_feature(raw, month)
    except SpecError as exc:
        return str(exc)
    problem = voice_problem(feature)
    if problem:
        return problem
    return raw, feature


def guard_reason(conn: sqlite3.Connection, month: str, today: str) -> str | None:
    """Why an automatic run may not call the model now, or None when it may."""
    rows = store.attempts(conn, month)
    if any(row["day_local"] == today for row in rows):
        return f"already tried on {today}; the next automatic run is tomorrow"
    if len(rows) >= MAX_AUTO_ATTEMPTS_PER_MONTH:
        return (
            f"{len(rows)} failed runs for {month}; no more automatic runs "
            "(`python -m tools.month --force` tries again)"
        )
    return None


def ensure_month_feature(
    conn: sqlite3.Connection,
    settings: Settings,
    month: str,
    client: MessagesClient | None = None,
    now: datetime | None = None,
    force: bool = False,
) -> MonthResult:
    try:
        return _ensure(conn, settings, month, client, now or now_utc(), force)
    except Exception as exc:
        log.exception("month feature %s: generation failed", month)
        return MonthResult(month, FAILED, reason=f"unexpected error: {exc.__class__.__name__}")


def _ensure(
    conn: sqlite3.Connection,
    settings: Settings,
    month: str,
    client: MessagesClient | None,
    moment: datetime,
    force: bool,
) -> MonthResult:
    if not MONTH.match(month):
        return MonthResult(month, FAILED, reason=f"month {month!r} is not YYYY-MM")
    today = local_day(moment, settings.home_tz)
    if not force:
        stored = store.load_feature(conn, month)
        if stored is not None:
            return MonthResult(month, STORED, stored)
        blocked = guard_reason(conn, month, today)
        if blocked:
            return MonthResult(month, SKIPPED, reason=blocked)

    live = client if client is not None else make_client(settings)
    if live is None:
        return MonthResult(month, FAILED, reason="no api key")

    tried = 0
    billed = 0
    usd = 0.0
    rejections: list[str] = []
    reason = ""
    for _ in range(MAX_CALLS):
        request = build_request(conn, settings, month, rejections[-1] if rejections else None)
        cap = spend.cap_check(conn, settings, request, moment)
        if not cap.allowed:
            reason = f"cap: {cap.describe()}"
            break
        tried += 1
        reply = call_model(live, request)
        if isinstance(reply, str):
            reason = f"model unavailable: {reply}"
            break
        billed += 1
        usd += spend.record(
            conn, settings, today, reply.request_id, reply.usage, reply.stop_reason, moment
        )
        if reply.stop_reason != "end_turn":
            reason = f"model unavailable: stop_reason {reply.stop_reason}"
            break
        verdict = judge(reply.text, month)
        if not isinstance(verdict, str):
            raw, _parsed = verdict
            feature = store.save_feature(
                conn, month, raw, SOURCE_MODEL, settings.summary.model, reply.text, moment
            )
            log.info("month feature %s: %s (%d call(s))", month, feature.title, billed)
            return MonthResult(month, GENERATED, feature, "", billed, usd, rejections)
        rejections.append(verdict)
        reason = f"rejected: {verdict}"
        log.warning("month feature %s: reply rejected: %s", month, verdict)

    if tried:
        store.record_attempt(conn, month, today, tried, reason, moment)
    log.warning("month feature %s: nothing stored: %s", month, reason)
    return MonthResult(month, FAILED, None, reason, billed, usd, rejections)
