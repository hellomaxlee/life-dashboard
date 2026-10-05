from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.month.spec import ART_SIZE, days_in

WORDS = (
    "ash birch cedar dune ember fern gale heath iris juniper kelp larch moss nettle oak "
    "pine quince reed sage thorn umber vetch willow xylem yarrow zinnia alder briar clover "
    "dock elder"
).split()
PALETTE = ["#ff4020", "#20c0ff", "#ffd000"]


def plate_art(day: int) -> list[str]:
    """A four-by-four block in a place no other day uses, plus a two-cell accent."""
    left, top = (day - 1) % 12, 4 * ((day - 1) // 12)
    rows = [["."] * ART_SIZE for _ in range(ART_SIZE)]
    for y in range(top, top + 4):
        for x in range(left, left + 4):
            rows[y][x] = "1"
    rows[15][0] = rows[15][1] = "2"
    return ["".join(row) for row in rows]


def feature_object(month: str) -> dict:
    """A valid feature for `month`, built the way the model is asked to build one."""
    return {
        "month": month,
        "title": "Small Woods",
        "theme": "A walk through a wood that grows one plant a day, and is in no hurry.",
        "palette": list(PALETTE),
        "days": [
            {
                "day": day,
                "caption": f"The {WORDS[day - 1]}",
                "note": f"A {WORDS[day - 1]} keeps its own slow calendar.",
                "art": plate_art(day),
            }
            for day in range(1, days_in(month) + 1)
        ],
    }


def feature_text(month: str, **changes) -> str:
    return json.dumps({**feature_object(month), **changes})


def reply(
    text: str,
    stop_reason: str = "end_turn",
    input_tokens: int = 2400,
    output_tokens: int = 15000,
    request_id: str = "req_month_1",
) -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[
            SimpleNamespace(type="thinking", thinking="planning"),
            SimpleNamespace(type="text", text=text),
        ],
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
        _request_id=request_id,
    )


class FakeStream:
    def __init__(self, item) -> None:
        self.item = item

    def __enter__(self) -> FakeStream:
        if isinstance(self.item, BaseException) and getattr(self.item, "on_open", False):
            raise self.item
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def get_final_message(self):
        if isinstance(self.item, BaseException):
            raise self.item
        return self.item


class FakeMessages:
    def __init__(self, replies: list) -> None:
        self.replies = list(replies)
        self.requests: list[dict] = []

    def stream(self, **kwargs) -> FakeStream:
        self.requests.append(kwargs)
        if not self.replies:
            raise AssertionError("fake client called more times than it has replies")
        return FakeStream(self.replies.pop(0))

    def create(self, **kwargs):
        raise AssertionError("the month feature must stream, not call messages.create")


class FakeClient:
    """Exposes `messages.stream(**kwargs)` as a context manager with `get_final_message()`,
    the call style app/month/generate.py uses. Records every request body. Never dials."""

    def __init__(self, *replies) -> None:
        self.messages = FakeMessages(list(replies))

    @property
    def requests(self) -> list[dict]:
        return self.messages.requests


@pytest.fixture
def keyed(settings):
    """Settings with a key that is never sent anywhere: every test passes a fake client or
    replaces `make_client`."""
    return replace(settings, anthropic_api_key="test-key-never-sent")
