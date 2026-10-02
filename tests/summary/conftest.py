from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

import anthropic

from app.summary.payload import Payload, build_payload

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures" / "summary"


def golden_cases() -> list[dict]:
    return json.loads((FIXTURES / "golden_lines.json").read_text())["cases"]


def hated_cases() -> list[dict]:
    return json.loads((FIXTURES / "hated_lines.json").read_text())["cases"]


def monday_of(day: str) -> date:
    d = date.fromisoformat(day)
    return d - timedelta(days=d.weekday())


def seed(conn: sqlite3.Connection, case: dict) -> None:
    """Write a golden case's rows: the day, its week, last week, an earlier week, a book."""
    day = case["day"]
    monday = monday_of(day)
    conn.execute(
        "INSERT OR REPLACE INTO daily_metrics (day_local, metrics_json) VALUES (?, ?)",
        (day, json.dumps(case.get("daily_metrics", {}))),
    )
    weeks = {
        monday: case.get("weekly_metrics"),
        monday - timedelta(days=7): case.get("last_week"),
        monday - timedelta(days=14): case.get("earlier_week"),
    }
    for start, metrics in weeks.items():
        if metrics is not None:
            conn.execute(
                "INSERT OR REPLACE INTO weekly_metrics (week_start_local, metrics_json) "
                "VALUES (?, ?)",
                (start.isoformat(), json.dumps(metrics)),
            )
    book = case.get("book")
    if book:
        conn.execute(
            "INSERT OR REPLACE INTO books (id, title, author, read_at, date_added) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                book["id"],
                book["title"],
                book.get("author"),
                book.get("read_at"),
                book.get("date_added"),
            ),
        )


def payload_for(conn: sqlite3.Connection, settings, case: dict, hour: int = 21) -> Payload:
    seed(conn, case)
    return build_payload(conn, settings, case["day"], hour)


def reply(
    text: str,
    stop_reason: str = "end_turn",
    input_tokens: int = 1200,
    output_tokens: int = 40,
    cache_read: int = 0,
    cache_creation: int = 0,
    request_id: str = "req_test_1",
) -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_creation,
        ),
        _request_id=request_id,
    )


class FakeMessages:
    def __init__(self, replies: list) -> None:
        self.replies = list(replies)
        self.requests: list[dict] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if not self.replies:
            raise AssertionError("fake client called more times than it has replies")
        item = self.replies.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeClient:
    """Exposes `messages.create(**kwargs)` and records every request body. Never dials."""

    def __init__(self, *replies) -> None:
        self.messages = FakeMessages(list(replies))

    @property
    def requests(self) -> list[dict]:
        return self.messages.requests


def sdk_error(kind: str):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    if kind == "connection":
        return anthropic.APIConnectionError(request=request)
    status = {"rate": 429, "auth": 401, "server": 529}[kind]
    cls = {
        "rate": anthropic.RateLimitError,
        "auth": anthropic.AuthenticationError,
        "server": anthropic.APIStatusError,
    }[kind]
    return cls(kind, response=httpx.Response(status, request=request), body=None)


@pytest.fixture
def golden() -> list[dict]:
    return golden_cases()


@pytest.fixture
def jobs_settings(settings, tmp_path: Path):
    """Settings whose usage file and backup dir are under tmp_path; scheduler still off."""
    from dataclasses import replace

    from app.config import BackupConfig, ClaudeUsageConfig

    return replace(
        settings,
        claude_usage=ClaudeUsageConfig(tmp_path / "claude_usage.json", 24),
        backup=BackupConfig(tmp_path / "backups", "03:15", 3),
    )
