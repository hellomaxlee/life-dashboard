"""The Pixoo adapter against a fake transport only. Unverified on hardware (none is owned)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest

from app.render.adapters.pixoo import (
    MAX_FRAMES,
    PixooAdapter,
    PixooError,
    pixoo_from_settings,
    require_lan_host,
    thin,
)
from app.render.screens import render_rotation
from tests.render import STALE, WEEK_41, load

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOST = "192.168.1.50"


def fake_device(seen: list[httpx.Request], pic_id: int = 7, fail_on: str | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        command = json.loads(request.content)["Command"]
        if command == fail_on:
            return httpx.Response(200, json={"error_code": 1})
        if command == "Draw/GetHttpGifId":
            return httpx.Response(200, json={"error_code": 0, "PicId": pic_id})
        return httpx.Response(200, json={"error_code": 0})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_still_frame_goes_as_one_send_http_gif(settings):
    view, now = load(WEEK_41, settings)
    clip = render_rotation(view, now)["week"]
    seen: list[httpx.Request] = []
    report = PixooAdapter(HOST, fake_device(seen)).send(clip)
    assert (report.pic_id, report.frames_sent, report.frames_dropped) == (7, 1, 0)
    assert [str(r.url) for r in seen] == [f"http://{HOST}/post"] * 2
    assert all(r.method == "POST" for r in seen)
    assert json.loads(seen[0].content) == {"Command": "Draw/GetHttpGifId"}
    body = json.loads(seen[1].content)
    data = base64.b64decode(body.pop("PicData"))
    assert body == {
        "Command": "Draw/SendHttpGif",
        "PicNum": 1,
        "PicWidth": 64,
        "PicOffset": 0,
        "PicID": 7,
        "PicSpeed": 8000,
    }
    assert len(data) == 64 * 64 * 3
    assert data == clip.poster.tobytes()
    assert tuple(data[:3]) == clip.poster.getpixel((0, 0))


def test_clip_sends_each_frame_with_its_offset_and_duration(settings):
    view, now = load(STALE, settings)
    clip = render_rotation(view, now)["week"]
    seen: list[httpx.Request] = []
    report = PixooAdapter(HOST, fake_device(seen)).send(clip)
    assert report.frames_sent == 16
    bodies = [json.loads(r.content) for r in seen[1:]]
    assert [b["PicOffset"] for b in bodies] == list(range(16))
    assert {b["PicNum"] for b in bodies} == {16}
    assert {b["PicSpeed"] for b in bodies} == {250}
    assert base64.b64decode(bodies[5]["PicData"]) == clip.frames[5].tobytes()


def test_long_clip_is_thinned_to_the_frame_limit_keeping_total_time(settings):
    view, now = load(WEEK_41, settings)
    books = render_rotation(view, now)["books"]
    assert len(books.frames) > MAX_FRAMES
    fitted = thin(books)
    assert len(fitted.frames) == MAX_FRAMES
    assert fitted.total_ms == books.total_ms
    assert fitted.frames[0] is books.frames[0]
    seen: list[httpx.Request] = []
    report = PixooAdapter(HOST, fake_device(seen)).send(books)
    assert report.frames_sent == MAX_FRAMES
    assert report.frames_dropped == len(books.frames) - MAX_FRAMES
    assert len(seen) == MAX_FRAMES + 1


def test_device_error_raises(settings):
    view, now = load(WEEK_41, settings)
    clip = render_rotation(view, now)["week"]
    with pytest.raises(PixooError, match="Draw/SendHttpGif"):
        PixooAdapter(HOST, fake_device([], fail_on="Draw/SendHttpGif")).send(clip)
    with pytest.raises(PixooError, match="Draw/GetHttpGifId"):
        PixooAdapter(HOST, fake_device([], fail_on="Draw/GetHttpGifId")).send(clip)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(PixooError):
        PixooAdapter(HOST, httpx.Client(transport=httpx.MockTransport(refuse))).send(clip)


@pytest.mark.parametrize(
    "host", ["8.8.8.8", "pixoo.example.com", "127.0.0.1", "", "192.168.1.50:80", "192.168.1.50/x"]
)
def test_only_private_lan_addresses_are_accepted(host):
    with pytest.raises(ValueError):
        require_lan_host(host)
    with pytest.raises(ValueError):
        PixooAdapter(host)


def test_lan_hosts_pass():
    assert require_lan_host("192.168.1.50") == "192.168.1.50"
    assert require_lan_host("10.0.0.9") == "10.0.0.9"


def test_disabled_by_default_and_never_called_by_the_app(settings):
    assert settings.device.pixoo_host == ""
    assert pixoo_from_settings(settings) is None
    users = []
    for folder in ("app", "tools"):
        for path in (REPO_ROOT / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if path.name != "pixoo.py" and any(
                name in text for name in ("PixooAdapter", "pixoo_from_settings", "adapters.pixoo")
            ):
                users.append(str(path.relative_to(REPO_ROOT)))
    assert users == []
