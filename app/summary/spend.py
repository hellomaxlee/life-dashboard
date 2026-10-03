"""The spend counter: table `model_spend`, one row per model call, priced from config.

The cap check runs BEFORE a call: month-to-date (the home-timezone calendar month of the
moment of the call) plus the worst case for the call about to be made must stay under
`summary.monthly_cap_usd`. The worst case prices every input token at the cache-write rate
(the dearest an input token can be) and assumes the full `max_tokens` of output. Input is
counted locally at CHARS_PER_TOKEN (2.0) characters per token until a real usage readback
calibrates it, a deliberate under-estimate so the token count errs high; no network call is
made to count.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import Settings
from app.timeutil import local_day, now_utc, to_utc_iso

CHARS_PER_TOKEN = 2.0
MTOK = 1_000_000


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


@dataclass(frozen=True)
class CapCheck:
    month_local: str
    month_to_date_usd: float
    worst_case_usd: float
    cap_usd: float

    @property
    def allowed(self) -> bool:
        return self.month_to_date_usd + self.worst_case_usd < self.cap_usd

    def describe(self) -> str:
        verdict = "call allowed" if self.allowed else "cap reached, no call"
        return (
            f"{self.month_local}: month-to-date ${self.month_to_date_usd:.4f} + worst case "
            f"${self.worst_case_usd:.4f} vs cap ${self.cap_usd:.2f}: {verdict}"
        )


def price_usd(settings: Settings, usage: Usage) -> float:
    s = settings.summary
    return (
        usage.input_tokens * s.price_input_per_mtok
        + usage.output_tokens * s.price_output_per_mtok
        + usage.cache_read_tokens * s.price_cache_read_per_mtok
        + usage.cache_creation_tokens * s.price_cache_write_per_mtok
    ) / MTOK


def estimate_input_tokens(request: dict) -> int:
    chars = len(json.dumps(request.get("system", "")) + json.dumps(request.get("messages", [])))
    return int(chars / CHARS_PER_TOKEN) + 1


def worst_case_usd(settings: Settings, request: dict) -> float:
    s = settings.summary
    input_rate = max(s.price_input_per_mtok, s.price_cache_write_per_mtok)
    return (
        estimate_input_tokens(request) * input_rate
        + int(request.get("max_tokens", 0)) * s.price_output_per_mtok
    ) / MTOK


def month_of(moment: datetime, tz: str) -> str:
    return moment.astimezone(ZoneInfo(tz)).strftime("%Y-%m")


def month_to_date(conn: sqlite3.Connection, month_local: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(usd), 0) AS usd FROM model_spend WHERE month_local = ?",
        (month_local,),
    ).fetchone()
    return float(row["usd"])


def cap_check(
    conn: sqlite3.Connection, settings: Settings, request: dict, now: datetime | None = None
) -> CapCheck:
    moment = now or now_utc()
    month = month_of(moment, settings.home_tz)
    return CapCheck(
        month,
        month_to_date(conn, month),
        worst_case_usd(settings, request),
        settings.summary.monthly_cap_usd,
    )


def record(
    conn: sqlite3.Connection,
    settings: Settings,
    day_local: str,
    request_id: str | None,
    usage: Usage,
    stop_reason: str | None,
    now: datetime | None = None,
) -> float:
    moment = now or now_utc()
    usd = price_usd(settings, usage)
    conn.execute(
        "INSERT INTO model_spend (day_local, month_local, request_id, model, input_tokens, "
        "output_tokens, cache_read_tokens, cache_creation_tokens, usd, stop_reason, "
        "created_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            day_local,
            month_of(moment, settings.home_tz),
            request_id,
            settings.summary.model,
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_read_tokens,
            usage.cache_creation_tokens,
            usd,
            stop_reason,
            to_utc_iso(moment),
        ),
    )
    return usd


def readback(conn: sqlite3.Connection, settings: Settings, now: datetime | None = None) -> str:
    moment = now or now_utc()
    month = month_of(moment, settings.home_tz)
    calls = conn.execute(
        "SELECT COUNT(*) AS n FROM model_spend WHERE month_local = ?", (month,)
    ).fetchone()["n"]
    return (
        f"{month} ({local_day(moment, settings.home_tz)}): {calls} call(s), "
        f"month-to-date ${month_to_date(conn, month):.4f} of cap "
        f"${settings.summary.monthly_cap_usd:.2f}, model {settings.summary.model}"
    )
