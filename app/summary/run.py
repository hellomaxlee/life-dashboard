"""write_summary(conn, settings, D): the line shown on day D. It describes D - 1, the latest
complete day, and is stored under D's `daily_metrics` row (ruling 2026-10-02).

Build the payload, check the cap, call the model (or not), gate the line,
regenerate once, fall back, store. Idempotent per day: a stored line is returned without a
call unless `force`.

The model is "unavailable" when there is no API key, when the cap check refuses the call,
when the call raises anything at all, and when the response's stop_reason is not end_turn;
every one of those ends in rule-based copy, never an empty line.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import anthropic

from app.config import Settings
from app.summary import memory, spend
from app.summary.fallback import fallback_line
from app.summary.gate import Recent, check_device_line, check_web_line
from app.summary.payload import Payload, build_payload
from app.summary.prompt import STABLE_SYSTEM_PROMPT, request_body, user_message
from app.timeutil import now_utc

log = logging.getLogger(__name__)

DEVICE_KEY = "summary_device_line"
WEB_KEY = "summary_web_line"
SOURCE_KEY = "summary_source"
SUMMARY_KEYS = frozenset({DEVICE_KEY, WEB_KEY, SOURCE_KEY})
MAX_MODEL_ATTEMPTS = 2
API_BASE_URL = "https://api.anthropic.com"
FILLER = re.compile(r"^(?:sure|certainly|of course|okay|ok)[\s,.]*$", re.IGNORECASE)
LABEL = re.compile(r"^here(?:'s|\s+is|\s+are)\b[^:]{0,40}:\s*", re.IGNORECASE)


class MessagesClient(Protocol):
    messages: Any


@dataclass(frozen=True)
class ModelReply:
    text: str
    stop_reason: str | None
    usage: spend.Usage
    request_id: str | None


@dataclass(frozen=True)
class SummaryResult:
    day_local: str
    line: str
    web_line: str | None
    source: str
    gate_result: str
    attempts: list[dict] = field(default_factory=list)
    called: bool = False
    spend_usd: float = 0.0


def make_client(settings: Settings) -> anthropic.Anthropic | None:
    if not settings.anthropic_api_key:
        return None
    return anthropic.Anthropic(
        api_key=settings.anthropic_api_key, base_url=API_BASE_URL, timeout=30, max_retries=1
    )


def build_request(payload: Payload, recent: Recent, settings: Settings, rejection: str | None):
    message = user_message(
        payload, list(recent.opening_lines), list(recent.sources_named), rejection
    )
    return request_body(STABLE_SYSTEM_PROMPT, message, settings.summary.model)


def _usage_of(response: Any) -> spend.Usage:
    usage = getattr(response, "usage", None)

    def count(name: str) -> int:
        value = getattr(usage, name, 0) if usage is not None else 0
        return int(value) if isinstance(value, int | float) and value == value else 0

    return spend.Usage(
        input_tokens=count("input_tokens"),
        output_tokens=count("output_tokens"),
        cache_read_tokens=count("cache_read_input_tokens"),
        cache_creation_tokens=count("cache_creation_input_tokens"),
    )


def call_model(client: MessagesClient, request: dict) -> ModelReply | str:
    """One call. Returns the reply, or a string naming why the model is unavailable.

    Nothing raised by the call or by reading its response escapes: the caller falls back."""
    try:
        return _reply_of(client.messages.create(**request))
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
        log.exception("summary: the model call raised outside the SDK's error types")
        return f"unexpected error: {exc.__class__.__name__}"


def _reply_of(response: Any) -> ModelReply:
    blocks = getattr(response, "content", []) or []
    text = "\n".join(b.text for b in blocks if getattr(b, "type", "") == "text")
    return ModelReply(
        text,
        getattr(response, "stop_reason", None),
        _usage_of(response),
        getattr(response, "_request_id", None),
    )


def split_reply(text: str) -> tuple[str, str | None]:
    """(device line, web line). A leading label or preamble ("Here is the line:") is not the
    line; when nothing follows it the device line is empty and the gate rejects it."""
    lines = [ln.strip().strip('"').strip() for ln in text.splitlines() if ln.strip()]
    while lines and (lines[0].endswith(":") or FILLER.match(lines[0])):
        lines = lines[1:]
    if lines:
        lines[0] = LABEL.sub("", lines[0]).strip('"').strip()
    if not lines:
        return "", None
    return lines[0], (lines[1] if len(lines) > 1 else None)


def store(
    conn: sqlite3.Connection, day_local: str, line: str, web_line: str | None, source: str
) -> None:
    row = conn.execute(
        "SELECT metrics_json FROM daily_metrics WHERE day_local = ?", (day_local,)
    ).fetchone()
    try:
        metrics = json.loads(row["metrics_json"]) if row is not None else {}
    except (TypeError, ValueError):
        metrics = {}
    if not isinstance(metrics, dict):
        metrics = {}
    metrics[DEVICE_KEY] = line
    metrics[WEB_KEY] = web_line
    metrics[SOURCE_KEY] = source
    conn.execute(
        "INSERT INTO daily_metrics (day_local, metrics_json) VALUES (?, ?) "
        "ON CONFLICT (day_local) DO UPDATE SET metrics_json = excluded.metrics_json",
        (day_local, json.dumps(metrics, sort_keys=True)),
    )


def write_summary(
    conn: sqlite3.Connection,
    settings: Settings,
    day_local: str,
    client: MessagesClient | None = None,
    force: bool = False,
    now: datetime | None = None,
) -> SummaryResult:
    moment = now or now_utc()
    if not force:
        stored = memory.stored_line(conn, day_local)
        if stored is not None:
            return SummaryResult(
                day_local,
                stored.line,
                stored.web_line,
                "stored",
                stored.gate_result,
                stored.attempts,
            )

    payload = build_payload(conn, settings, day_local)
    recent = memory.recent_before(conn, day_local)
    threshold = settings.summary.similarity_threshold
    attempts: list[dict] = []
    called = False
    usd = 0.0

    live = client if client is not None else make_client(settings)
    if live is None:
        attempts.append({"source": "model", "result": "unavailable: no api key"})
    else:
        rejection: str | None = None
        for _ in range(MAX_MODEL_ATTEMPTS):
            request = build_request(payload, recent, settings, rejection)
            cap = spend.cap_check(conn, settings, request, moment)
            if not cap.allowed:
                attempts.append({"source": "model", "result": f"cap: {cap.describe()}"})
                break
            reply = call_model(live, request)
            if isinstance(reply, str):
                attempts.append({"source": "model", "result": f"unavailable: {reply}"})
                log.warning("summary %s: model unavailable: %s", day_local, reply)
                break
            called = True
            usd += spend.record(
                conn, settings, day_local, reply.request_id, reply.usage, reply.stop_reason, moment
            )
            if reply.stop_reason != "end_turn":
                attempts.append(
                    {"source": "model", "result": f"unavailable: stop_reason {reply.stop_reason}"}
                )
                break
            line, web = split_reply(reply.text)
            verdict = check_device_line(line, payload, recent, threshold)
            if web is not None and not check_web_line(web, payload, recent).ok:
                web = None
            attempts.append({"source": "model", "line": line, "result": verdict.reason})
            if verdict.ok:
                memory.remember(
                    conn, day_local, line, web, payload.lens, "model", verdict.reason, attempts
                )
                store(conn, day_local, line, web, "model")
                return SummaryResult(
                    day_local, line, web, "model", verdict.reason, attempts, called, usd
                )
            rejection = verdict.reason

    fallback = fallback_line(payload, recent, threshold)
    attempts.append({"source": "fallback", "line": fallback.line, "result": fallback.gate.reason})
    memory.remember(
        conn,
        day_local,
        fallback.line,
        fallback.web_line,
        payload.lens,
        "fallback",
        fallback.gate.reason,
        attempts,
    )
    store(conn, day_local, fallback.line, fallback.web_line, "fallback")
    return SummaryResult(
        day_local,
        fallback.line,
        fallback.web_line,
        "fallback",
        fallback.gate.reason,
        attempts,
        called,
        usd,
    )
