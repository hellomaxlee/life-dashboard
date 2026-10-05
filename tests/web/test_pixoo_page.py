"""GET /pixoo: the device page, its rotation JSON and its frame endpoint.

Brightness and gamma are client-side: the page applies lookup tables the server computed with
`led_lut`, so the frame URLs carry neither and nothing is re-rendered when they change. The
tests prove the shipped tables are the emulator (pixel-for-pixel against `led_gamma`) and that
brightness scales before the panel curve, not after.
"""

from __future__ import annotations

import io
import re
from dataclasses import replace
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from PIL import Image, ImageChops

from app.main import create_app
from app.render.celebrate import celebrations_for, sparkle_clip
from app.render.frame import Clip, new_frame
from app.render.gamma import led_gamma, led_lut
from app.render.rotation import hold_ms, render_screen, rotation_sequence, sequence_names
from app.render.view import DayView
from app.timeutil import local_day
from app.web.pixoo import BRIGHTNESS_STEPS
from tests.render import WEEK_41, WEEK_COMPLETE, load

PAGES = ("/", "/preview", "/pixoo")
NOW = datetime(2026, 10, 2, 16, 0, tzinfo=UTC)
EXTERNAL = ("http://", "https://", "<script src", "<link ", "@import", "url(")


def _png(response) -> Image.Image:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    return Image.open(io.BytesIO(response.content)).convert("RGB")


def test_pixoo_page_is_self_contained_and_links_the_other_pages(client):
    page = client.get(f"/pixoo?fixture={WEEK_41}")
    assert page.status_code == 200
    for marker in EXTERNAL:
        assert marker not in page.text, marker
    assert "<canvas" in page.text and page.text.count("<script>") == 1
    assert f"Device: {WEEK_41}" in page.text
    assert f"<option value='{WEEK_41}' selected>" in page.text
    assert "life-dashboard" in page.text
    for path in PAGES:
        text = client.get(path).text
        assert "<nav class='pages'>" in text
        assert "<a href='/preview'>" in text or path == "/preview"
        assert "<a href='/pixoo'>" in text or path == "/pixoo"
        assert "<a href='/'>" in text or path == "/"
    assert "<script" not in client.get("/preview").text


def test_rotation_json_lists_the_sequence_with_valid_frames(client, settings):
    body = client.get(f"/pixoo/rotation.json?fixture={WEEK_41}").json()
    view, now = load(WEEK_41, settings)
    sequence = rotation_sequence(view, now, settings.device.screen_seconds)
    clips = {name: clip for name, clip, _ in sequence}
    holds = {name: hold for name, _, hold in sequence}
    names = [s["name"] for s in body["screens"]]
    assert names == ["today", "week", "month", "books", "win-workout", "win-sleep"]
    assert body["dwell_ms"] == settings.device.screen_seconds * 1000 == 6000
    assert body["source"] == {"fixture": WEEK_41}
    assert body["day_local"] == view.day_local and body["as_of_local"] == "2026-09-30 18:10 EDT"
    for screen in body["screens"]:
        clip = clips[screen["name"]]
        assert len(screen["frames"]) == len(clip.frames)
        assert [f["ms"] for f in screen["frames"]] == list(clip.durations_ms)
        assert all(f["ms"] > 0 for f in screen["frames"])
        assert screen["total_ms"] == clip.total_ms == sum(f["ms"] for f in screen["frames"])
        assert screen["hold_ms"] == holds[screen["name"]]
        for index, frame in enumerate(screen["frames"]):
            assert "gamma" not in frame["url"] and "brightness" not in frame["url"]
            image = _png(client.get(frame["url"]))
            assert image.size == (64, 64)
            assert ImageChops.difference(image, clip.frames[index]).getbbox() is None
    frames = {s["name"]: s["frames"] for s in body["screens"]}
    assert len(frames["books"]) > 1, "Books pages are separate frames"
    assert [f["ms"] for f in frames["month"]] == [6000, 5000], "the plate, then its note"
    assert holds["month"] == 11000, "one pass through both pages"
    assert holds["week"] == holds["today"] == 6000
    assert holds["books"] == clips["books"].total_ms > 6000, "the whole message pages through"
    assert holds["win-workout"] == holds["win-sleep"] == 4200
    expected = {c.name: c for c in celebrations_for(view)}
    assert [c["name"] for c in body["celebrations"]] == ["sparkle", "party"]
    for celebration in body["celebrations"]:
        clip = expected[celebration["name"]].clip
        assert celebration["earned"] == expected[celebration["name"]].earned
        assert len(celebration["frames"]) == len(clip.frames)
        assert celebration["total_ms"] == clip.total_ms
        last = _png(client.get(celebration["frames"][-1]["url"]))
        assert ImageChops.difference(last, clip.frames[-1]).getbbox() is None
    assert body["celebrations"][0]["earned"] and not body["celebrations"][1]["earned"]
    complete = client.get(f"/pixoo/rotation.json?fixture={WEEK_COMPLETE}").json()
    assert complete["celebrations"][1]["earned"]
    done, done_now = load(WEEK_COMPLETE, settings)
    assert [s["name"] for s in complete["screens"]] == list(sequence_names(done))
    assert [s["name"] for s in complete["screens"]][3:] == [
        "books",
        "win-workout",
        "win-sleep",
        "party",
    ]
    party = complete["screens"][-1]
    clip = render_screen("party", done, done_now)
    assert (len(party["frames"]), party["hold_ms"]) == (56, clip.total_ms), "one whole play"
    assert party["frames"][0]["url"].startswith("/pixoo/frame/party/0.png?")
    assert party["frames"] == complete["celebrations"][1]["frames"], "one party clip, not two"
    shown = _png(client.get(party["frames"][30]["url"]))
    assert ImageChops.difference(shown, clip.frames[30]).getbbox() is None
    assert len(frames["month"]) == 2 and len(complete["screens"][2]["frames"]) == 1


def test_shipped_luts_are_the_gamma_emulator_with_brightness_before_the_curve(client, settings):
    body = client.get(f"/pixoo/rotation.json?fixture={WEEK_41}").json()
    assert body["brightness_steps"] == list(BRIGHTNESS_STEPS) == list(range(10, 101, 10))
    luts = body["luts"]
    assert set(luts) == {"gamma", "raw"}
    for step in BRIGHTNESS_STEPS:
        assert luts["gamma"][str(step)] == led_lut(brightness=step / 100)
        assert luts["raw"][str(step)] == [int(v * step / 100) for v in range(256)]
        assert len(luts["gamma"][str(step)]) == 256
    raw = _png(client.get(f"/pixoo/frame/week/0.png?fixture={WEEK_41}"))
    emulated = _png(client.get(f"/pixoo/frame/week/0.png?fixture={WEEK_41}&gamma=1"))
    assert ImageChops.difference(emulated, led_gamma(raw)).getbbox() is None
    full = luts["gamma"]["100"]
    assert ImageChops.difference(raw.point(full * 3), emulated).getbbox() is None
    half = luts["gamma"]["50"]
    after_curve = [round(v * 0.5) for v in full]
    assert half != after_curve, "dimming must scale the PWM level before the panel curve"
    assert half[1] == 0 and full[1] > 0, "below one PWM step the LED is dark"
    assert (
        ImageChops.difference(raw.point(half * 3), led_gamma(raw, brightness=0.5)).getbbox() is None
    )


def test_hold_is_the_dwell_or_the_clip_length_if_longer(settings):
    short = Clip((new_frame(),) * 4, (1000,) * 4)
    long = Clip((new_frame(),) * 5, (5000,) * 5)
    still = Clip((new_frame(),), (8000,))
    assert hold_ms("week", still, settings.device.screen_seconds) == 6000, "a still: the dwell"
    assert hold_ms("week", short, settings.device.screen_seconds) == 8000, "two whole plays"
    assert hold_ms("books", long, settings.device.screen_seconds) == 25000, "one whole play"
    assert hold_ms("books", long, 30) == 25000, "never a second pass"
    assert hold_ms("books", short, 30) == 4000, "a short summary is not repeated either"
    sparkle = sparkle_clip("book")
    assert sparkle.total_ms == 1400
    assert hold_ms("win-book", sparkle, settings.device.screen_seconds) == 4200
    assert hold_ms("win-book", sparkle, 45) == 4200, "a sparkle never waits out the dwell"
    paged = Clip((new_frame(),) * 2, (6000, 5000))
    assert hold_ms("month", paged, 6) == hold_ms("month", paged, 45) == 11000, "one pass"
    assert hold_ms("month", still, 45) == 45000, "the plate alone, or the calendar: the dwell"
    party = render_screen("party", DayView(day_local="2026-10-02", week_dots=3), NOW)
    assert hold_ms("party", party, 6) == hold_ms("party", party, 45) == party.total_ms == 3360


def test_rotation_json_dwell_follows_device_config(settings):
    slow = replace(settings, device=replace(settings.device, screen_seconds=45))
    with TestClient(create_app(slow)) as client:
        body = client.get(f"/pixoo/rotation.json?fixture={WEEK_41}").json()
    assert body["dwell_ms"] == 45000
    holds = {s["name"]: (s["hold_ms"], s["total_ms"], len(s["frames"])) for s in body["screens"]}
    assert holds["week"][0] == holds["today"][0] == 45000
    books_hold, books_total, pages = holds["books"]
    assert pages > 1 and books_hold == books_total < 45000
    assert holds["win-workout"][0] == 4200


def test_date_and_fixture_selection(client, settings):
    today = local_day(datetime.now(UTC), settings.home_tz)
    default = client.get("/pixoo/rotation.json").json()
    assert default["source"] == {"date": today} and default["day_local"] == today
    page = client.get("/pixoo?placeholder=0")
    assert page.status_code == 200 and f"value='{today}'" in page.text
    assert "Device: " + today in page.text
    by_date = client.get("/pixoo/rotation.json?date=2026-10-02").json()
    assert by_date["source"] == {"date": "2026-10-02"} and by_date["as_of_local"] is None
    assert all("date=2026-10-02" in f["url"] for s in by_date["screens"] for f in s["frames"])
    for screen in by_date["screens"]:
        image = _png(client.get(screen["frames"][0]["url"]))
        assert image.size == (64, 64) and image.getbbox() is not None, "never blank"
    assert client.get("/pixoo?date=2026-10-02").status_code == 200
    page = client.get(f"/pixoo?fixture={WEEK_COMPLETE}")
    assert f"<option value='{WEEK_COMPLETE}' selected>" in page.text
    assert f"<option value='{WEEK_41}'>" in page.text


def test_bad_input(client):
    assert client.get("/pixoo?fixture=nope").status_code == 404
    assert client.get("/pixoo?fixture=../../config").status_code == 404
    assert client.get("/pixoo?date=yesterday").status_code == 422
    assert client.get("/pixoo/rotation.json?fixture=nope").status_code == 404
    assert client.get("/pixoo/rotation.json?date=2026-13-01").status_code == 422
    assert client.get(f"/pixoo/frame/load/0.png?fixture={WEEK_41}").status_code == 404
    assert client.get(f"/pixoo/frame/week/1.png?fixture={WEEK_41}").status_code == 404
    assert client.get(f"/pixoo/frame/week/-1.png?fixture={WEEK_41}").status_code == 404
    assert client.get(f"/pixoo/frame/week/x.png?fixture={WEEK_41}").status_code == 422
    assert client.get(f"/pixoo/frame/week/0.png?fixture={WEEK_41}&gamma=2").status_code == 422


def test_month_and_party_have_frames_and_deep_links(client, settings):
    view, now = load(WEEK_41, settings)
    month = render_screen("month", view, now)
    for index in (0, 1):
        image = _png(client.get(f"/pixoo/frame/month/{index}.png?fixture={WEEK_41}"))
        assert ImageChops.difference(image, month.frames[index]).getbbox() is None
    assert client.get(f"/pixoo/frame/month/2.png?fixture={WEEK_41}").status_code == 404
    calendar = _png(client.get("/pixoo/frame/month/0.png?date=2026-10-02"))
    assert calendar.getbbox() is not None, "no feature stored: the calendar, never blank"
    assert client.get("/pixoo/frame/month/1.png?date=2026-10-02").status_code == 404
    sample = _png(client.get(f"/pixoo/frame/party/0.png?fixture={WEEK_41}"))
    assert sample.getbbox() is not None, "an unearned party is still there to be looked at"
    page = client.get(f"/pixoo?fixture={WEEK_41}").text
    assert "/screen=([a-z-]+)/" in page, "#screen=month, #screen=win-sleep, #screen=party"
    assert "if (wanted !== null && !inRotation) { playCelebration(wanted); }" in page
    assert "c.earned && c.name === 'party'" not in page, "the earned party is in the rotation"


def test_brightness_and_gamma_are_client_side(client):
    page = client.get(f"/pixoo?fixture={WEEK_41}").text
    assert re.search(r"<input type='range' id='brightness' min='10' max='100' step='10'", page)
    assert "<input type='checkbox' id='gamma' checked>" in page
    plain = client.get(f"/pixoo/frame/today/0.png?fixture={WEEK_41}")
    ignored = client.get(f"/pixoo/frame/today/0.png?fixture={WEEK_41}&brightness=50")
    assert plain.content == ignored.content
    assert (
        client.get(f"/pixoo/frame/today/0.png?fixture={WEEK_41}&gamma=0").content == plain.content
    )
