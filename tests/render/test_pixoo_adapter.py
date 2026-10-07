"""The Pixoo adapter against a fake transport only. Unverified on hardware (none is owned)."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import httpx
import pytest

from app.render.adapters.pixoo import (
    RESET_EVERY,
    PixooAdapter,
    PixooError,
    pixoo_from_settings,
    require_lan_host,
)
from app.render.frame import Clip
from tests.render import STALE, WEEK_41, load, rotation

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOST = "192.168.1.50"


def fake_device(seen: list[httpx.Request], fail_on: str | None = None):
    """Replies as the real panel does: ReturnCode 0, or 1 with a message."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        command = json.loads(request.content)["Command"]
        if command == fail_on:
            return httpx.Response(
                200, json={"ReturnCode": 1, "ReturnMessage": "Only accept JSON parameters"}
            )
        return httpx.Response(200, json={"Command": command, "ReturnCode": 0})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_still_frame_goes_as_one_send_http_gif(settings):
    view, now = load(WEEK_41, settings)
    clip = rotation(view, now)["week"]
    seen: list[httpx.Request] = []
    report = PixooAdapter(HOST, fake_device(seen)).send(clip)
    assert (report.pic_id, report.frames_sent) == (1, 1)
    assert [str(r.url) for r in seen] == [f"http://{HOST}:9000/divoom_api"] * 2
    assert all(r.method == "POST" for r in seen)
    assert all(r.headers["connection"] == "close" for r in seen)
    assert json.loads(seen[0].content) == {"Command": "Draw/ResetHttpGifId"}
    body = json.loads(seen[1].content)
    data = base64.b64decode(body.pop("PicData"))
    assert body == {
        "Command": "Draw/SendHttpGif",
        "PicNum": 1,
        "PicWidth": 64,
        "PicOffset": 0,
        "PicID": 1,
        "PicSpeed": 8000,
    }
    assert len(data) == 64 * 64 * 3
    assert data == clip.poster.tobytes()
    assert tuple(data[:3]) == clip.poster.getpixel((0, 0))


def sixteen_frame_clip(settings) -> Clip:
    """A multi-frame animation for the adapter's frame-by-frame path; no rotation screen is
    an animation any more (the stale Week became a still, 2026-10-07)."""
    view, now = load(STALE, settings)
    poster = rotation(view, now)["week"].poster
    return Clip(tuple(poster.copy() for _ in range(16)), (250,) * 16)


def test_clip_sends_each_frame_with_its_offset_and_duration(settings):
    clip = sixteen_frame_clip(settings)
    seen: list[httpx.Request] = []
    report = PixooAdapter(HOST, fake_device(seen)).send(clip)
    assert report.frames_sent == 16
    bodies = [json.loads(r.content) for r in seen[1:]]
    assert [b["PicOffset"] for b in bodies] == list(range(16))
    assert {b["PicNum"] for b in bodies} == {16}
    assert {b["PicSpeed"] for b in bodies} == {250}
    assert base64.b64decode(bodies[5]["PicData"]) == clip.frames[5].tobytes()


def test_paged_books_clip_goes_whole_with_its_page_durations(settings):
    view, now = load(WEEK_41, settings)
    books = rotation(view, now)["books"]
    assert 1 < len(books.frames) <= 59
    seen: list[httpx.Request] = []
    report = PixooAdapter(HOST, fake_device(seen)).send(books)
    assert report.frames_sent == len(books.frames)
    bodies = [json.loads(r.content) for r in seen[1:]]
    assert [b["PicSpeed"] for b in bodies] == [2000] * len(books.frames)
    assert base64.b64decode(bodies[-1]["PicData"]) == books.frames[-1].tobytes()


def test_gif_id_is_reset_before_the_first_clip_and_every_32_after(settings):
    view, now = load(WEEK_41, settings)
    clip = rotation(view, now)["week"]
    seen: list[httpx.Request] = []
    adapter = PixooAdapter(HOST, fake_device(seen))
    for _ in range(2 * RESET_EVERY + 1):
        adapter.send(clip)
    commands = [json.loads(r.content)["Command"] for r in seen]
    sends = [commands[: i + 1].count("Draw/SendHttpGif") for i, c in enumerate(commands)]
    resets_after = [sends[i] for i, c in enumerate(commands) if c == "Draw/ResetHttpGifId"]
    assert RESET_EVERY == 32
    assert resets_after == [0, 32, 64]
    ids = [json.loads(r.content)["PicID"] for r in seen if b"SendHttpGif" in r.content]
    assert ids == [*range(1, 33), *range(1, 33), 1]


def test_a_failed_reset_is_tried_again_on_the_next_clip(settings):
    view, now = load(WEEK_41, settings)
    clip = rotation(view, now)["week"]
    seen: list[httpx.Request] = []
    adapter = PixooAdapter(HOST, fake_device(seen, fail_on="Draw/ResetHttpGifId"))
    for _ in range(2):
        with pytest.raises(PixooError, match="Draw/ResetHttpGifId"):
            adapter.send(clip)
    assert [json.loads(r.content)["Command"] for r in seen] == ["Draw/ResetHttpGifId"] * 2


def test_device_error_raises(settings):
    view, now = load(WEEK_41, settings)
    clip = rotation(view, now)["week"]
    with pytest.raises(PixooError, match="Draw/SendHttpGif"):
        PixooAdapter(HOST, fake_device([], fail_on="Draw/SendHttpGif")).send(clip)
    with pytest.raises(PixooError, match="Only accept JSON parameters"):
        PixooAdapter(HOST, fake_device([], fail_on="Draw/SendHttpGif")).send(clip)

    def old_api(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error_code": 0})

    with pytest.raises(PixooError, match="device replied"):
        PixooAdapter(HOST, httpx.Client(transport=httpx.MockTransport(old_api))).send(clip)

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


def test_send_budget_stops_a_slow_clip_between_frames(settings):
    clip = sixteen_frame_clip(settings)
    seen: list[httpx.Request] = []
    with pytest.raises(PixooError, match="send budget at frame 0 of 16"):
        PixooAdapter(HOST, fake_device(seen), frame_budget_s=0).send(clip)
    assert len(seen) == 1
    assert PixooAdapter(HOST, fake_device([]), frame_budget_s=4).send(clip).frames_sent == 16


def test_disabled_by_default_and_only_the_rotation_job_and_the_hand_check_call_it(settings):
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
    assert sorted(users) == [
        "app/jobs/rotation.py",
        "app/render/adapters/served.py",
        "tools/pixoo_check.py",
    ]


def test_a_reply_that_drips_past_the_command_budget_fails_the_send(settings):
    view, now = load(WEEK_41, settings)
    clip = rotation(view, now)["week"]

    class Drip(httpx.SyncByteStream):
        def __iter__(self):
            for byte in b'{"ReturnCode": 0, "ReturnMessage": ""}':
                time.sleep(0.01)
                yield bytes([byte])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=Drip())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    slow = PixooAdapter(HOST, client, timeout_s=0.02)
    started = time.monotonic()
    with pytest.raises(PixooError, match="Draw/ResetHttpGifId: reply took over"):
        slow.send(clip)
    assert time.monotonic() - started < 0.2
    assert PixooAdapter(HOST, client, timeout_s=5).send(clip).frames_sent == 1


def test_a_refused_connection_is_retried_and_three_in_a_row_fail(settings, monkeypatch):
    view, now = load(WEEK_41, settings)
    clip = rotation(view, now)["week"]
    monkeypatch.setattr("app.render.adapters.pixoo.CONNECT_RETRY_S", 0)
    refusals = {"left": 2}
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if refusals["left"] > 0:
            refusals["left"] -= 1
            raise httpx.ConnectError("[Errno 61] Connection refused", request=request)
        seen.append(json.loads(request.content)["Command"])
        return httpx.Response(200, json={"ReturnCode": 0})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert PixooAdapter(HOST, client).send(clip).frames_sent == 1
    assert seen == ["Draw/ResetHttpGifId", "Draw/SendHttpGif"]
    refusals["left"] = 3
    with pytest.raises(PixooError, match="Connection refused"):
        PixooAdapter(HOST, client).send(clip)


def test_brightness_goes_as_one_channel_command():
    seen: list[httpx.Request] = []
    PixooAdapter(HOST, fake_device(seen)).set_brightness(10)
    assert [json.loads(r.content) for r in seen] == [
        {"Command": "Channel/SetBrightness", "Brightness": 10}
    ]


def test_play_url_is_one_command_and_an_unknown_command_reply_means_no(settings):
    seen: list[httpx.Request] = []
    adapter = PixooAdapter(HOST, fake_device(seen))
    assert adapter.play_url("http://192.168.1.171:8080/pixoo/clip/abc.gif") is True
    assert json.loads(seen[0].content) == {
        "Command": "Device/PlayTFGif",
        "FileType": 2,
        "FileName": "http://192.168.1.171:8080/pixoo/clip/abc.gif",
    }
    deaf = PixooAdapter(HOST, fake_device([], fail_on="Device/PlayTFGif"))
    assert deaf.play_url("http://192.168.1.171:8080/x.gif") is False

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(PixooError):
        PixooAdapter(HOST, httpx.Client(transport=httpx.MockTransport(refuse))).play_url("u")


def test_published_clips_are_served_as_gifs_and_only_the_newest_are_kept(settings, client):
    from app.render.adapters import served
    from app.render.celebrate import sparkle_clip

    served.clear()
    token = served.publish(sparkle_clip("workout"))
    assert served.publish(sparkle_clip("workout")) == token, "the same clip is one entry"
    body = client.get(f"/pixoo/clip/{token}.gif")
    assert body.status_code == 200 and body.headers["content-type"] == "image/gif"
    assert body.content[:6] in (b"GIF87a", b"GIF89a") and len(body.content) < 20_000
    assert client.get("/pixoo/clip/nope.gif").status_code == 404
    for seed in range(served.KEEP + 2):
        served.publish(sparkle_clip("sleep", seed=seed))
    assert served.served(token) is None, "pushed out by newer clips"
    assert served.clip_url("192.168.1.185", 8080, "t").endswith(":8080/pixoo/clip/t.gif")
