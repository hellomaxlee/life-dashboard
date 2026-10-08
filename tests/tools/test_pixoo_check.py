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
    names = [line.split(":")[0] for line in lines]
    city = [f"city page {n} of 4" for n in range(1, 5)]
    month = [f"month page {n} of 3" for n in range(1, 4)]
    assert names[:9] == ["today", *city, "week", *month]
    wins = [f"win-{win} page {n} of 5" for win in ("workout", "sleep") for n in range(1, 6)]
    assert names[-10:] == wins
    pages = len(names) - 19
    assert pages > 1
    assert names[9:-10] == [f"books page {n} of {pages}" for n in range(1, pages + 1)]
    assert all("sent in" in line and "hold" in line for line in lines)
    assert ["hold 6 s" in line for line in lines[1:5]] == [True, True, False, False]
    assert "hold 5 s" in lines[3] and "hold 5 s" in lines[4], "weather, lines, two alerts"
    assert ["hold 6 s" in lines[6], "hold 5 s" in lines[7], "hold 6 s" in lines[8]] == [True] * 3, (
        "the plate, its note, then the calendar"
    )
    sends = [body for body in seen if body["Command"] == "Draw/SendHttpGif"]
    assert sorted({body["PicID"] for body in sends}) == list(range(1, 20 + pages))
    assert {body["PicWidth"] for body in sends} == {64}


def test_one_named_screen_and_an_unearned_win_is_skipped(device, capsys):
    seen, _ = device
    assert pixoo_check.main(["--host", HOST, "--no-hold", "--screen", "win-book"]) == 0
    assert "win-book: not earned on this day, skipped" in capsys.readouterr().out
    assert seen == []


def test_month_and_party_can_be_sent_by_name(device, capsys):
    seen, _ = device
    assert pixoo_check.main(["--host", HOST, "--no-hold", "--screen", "month"]) == 0
    sends = [body for body in seen if body["Command"] == "Draw/SendHttpGif"]
    assert [(body["PicNum"], body["PicOffset"]) for body in sends] == [(1, 0)] * 3, "three stills"
    assert pixoo_check.main(["--host", HOST, "--no-hold", "--screen", "party"]) == 0
    assert "party: not earned on this day, skipped" in capsys.readouterr().out
    seen.clear()
    done = str(pixoo_check.DEFAULT_FIXTURE.with_name("train__all-sources__alive__peak.json"))
    args = ["--host", HOST, "--no-hold", "--fixture", done, "--screen", "party"]
    assert pixoo_check.main(args) == 0
    out = capsys.readouterr().out
    assert "party page 6 of 6: 1 frame(s) sent" in out and "hold 0.3 s" in out
    assert len([body for body in seen if body["Command"] == "Draw/SendHttpGif"]) == 6
    with pytest.raises(SystemExit):
        pixoo_check.main(["--host", HOST, "--screen", "year"])


def test_city_can_be_sent_by_name_as_one_still_per_page(device, capsys):
    seen, _ = device
    assert pixoo_check.main(["--host", HOST, "--no-hold", "--screen", "city"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split(":")[0] for line in lines] == [f"city page {n} of 4" for n in (1, 2, 3, 4)]
    sends = [body for body in seen if body["Command"] == "Draw/SendHttpGif"]
    assert [(body["PicNum"], body["PicOffset"]) for body in sends] == [(1, 0)] * 4, "four stills"
    assert len({body["PicData"] for body in sends}) == 4, "four different pages"


def test_an_unreachable_device_fails_with_the_command_named(device, capsys):
    _, state = device
    state["fail"] = True
    assert pixoo_check.main(["--host", HOST, "--no-hold"]) == 1
    assert "today: FAILED" in capsys.readouterr().out


def test_a_host_off_the_lan_is_refused_before_anything_is_sent(capsys):
    assert pixoo_check.main(["--host", "8.8.8.8"]) == 2
    assert "pixoo host must be in" in capsys.readouterr().err
