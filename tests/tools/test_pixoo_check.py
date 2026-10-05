"""tools.pixoo_check against a fake device: what it sends, what it prints, how it fails."""

from __future__ import annotations

import json

import httpx
import pytest

from tools import pixoo_check

HOST = "192.168.1.50"


@pytest.fixture
def device(monkeypatch):
    seen: list[dict] = []
    state = {"fail": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["fail"]:
            raise httpx.ConnectTimeout("timed out", request=request)
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"ReturnCode": 0})

    real = pixoo_check.PixooAdapter

    def fake(host: str, timeout_s: float, frame_budget_s: float):
        return real(host, httpx.Client(transport=httpx.MockTransport(handler)))

    monkeypatch.setattr(pixoo_check, "PixooAdapter", fake)
    return seen, state


def test_the_fixture_day_goes_out_in_rotation_order_with_timings(device, capsys):
    seen, _ = device
    assert pixoo_check.main(["--host", HOST, "--no-hold"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split(":")[0] for line in lines] == [
        "week",
        "today",
        "win-workout",
        "win-sleep",
        "books",
    ]
    assert all("sent in" in line and "hold" in line for line in lines)
    sends = [body for body in seen if body["Command"] == "Draw/SendHttpGif"]
    assert sorted({body["PicID"] for body in sends}) == [1, 2, 3, 4, 5]
    assert {body["PicWidth"] for body in sends} == {64}


def test_one_named_screen_and_an_unearned_win_is_skipped(device, capsys):
    seen, _ = device
    assert pixoo_check.main(["--host", HOST, "--no-hold", "--screen", "win-book"]) == 0
    assert "win-book: not earned on this day, skipped" in capsys.readouterr().out
    assert seen == []


def test_an_unreachable_device_fails_with_the_command_named(device, capsys):
    _, state = device
    state["fail"] = True
    assert pixoo_check.main(["--host", HOST, "--no-hold"]) == 1
    assert "week: FAILED" in capsys.readouterr().out


def test_a_host_off_the_lan_is_refused_before_anything_is_sent(capsys):
    assert pixoo_check.main(["--host", "8.8.8.8"]) == 2
    assert "pixoo host must be in" in capsys.readouterr().err
