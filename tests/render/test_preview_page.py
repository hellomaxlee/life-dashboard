from __future__ import annotations

import io

from PIL import Image, ImageChops

from app.render.gamma import led_gamma
from tests.render import STALE, WEEK_41, load, rotation
from tools.render import main as render_main

NAMES = ("week", "today", "books", "sparkle", "party")


def test_preview_page_shows_every_screen_four_ways(client):
    page = client.get(f"/preview?fixture={WEEK_41}")
    assert page.status_code == 200
    assert "<script" not in page.text and "<canvas" not in page.text
    assert "http://" not in page.text and "https://" not in page.text
    for name in NAMES:
        for scale in (1, 8):
            for gamma in (0, 1):
                src = f"/preview/image/{name}?fixture={WEEK_41}&amp;scale={scale}&amp;gamma={gamma}"
                assert src in page.text, src
    assert page.text.count("<img ") == 20


def test_preview_images_are_pillow_png_or_gif_bytes(client, settings):
    view, now = load(WEEK_41, settings)
    week = rotation(view, now)["week"].poster
    for scale in (1, 8):
        for gamma in (0, 1):
            response = client.get(
                f"/preview/image/week?fixture={WEEK_41}&scale={scale}&gamma={gamma}"
            )
            assert response.status_code == 200
            assert response.headers["content-type"] == "image/png"
            image = Image.open(io.BytesIO(response.content)).convert("RGB")
            assert image.size == (64 * scale, 64 * scale)
            if scale == 1:
                expected = led_gamma(week) if gamma else week
                assert ImageChops.difference(image, expected).getbbox() is None
    for name in ("books", "sparkle", "party"):
        response = client.get(f"/preview/image/{name}?fixture={WEEK_41}&scale=8&gamma=1")
        assert response.headers["content-type"] == "image/gif"
        gif = Image.open(io.BytesIO(response.content))
        assert gif.size == (512, 512) and gif.n_frames > 1
    stale = client.get(f"/preview/image/week?fixture={STALE}")
    assert stale.headers["content-type"] == "image/gif"


def test_preview_by_date_on_an_empty_database_still_renders(client):
    page = client.get("/preview?date=2026-10-02")
    assert page.status_code == 200
    assert "Preview: 2026-10-02" in page.text
    assert "/preview/image/week?date=2026-10-02&amp;scale=1&amp;gamma=1" in page.text
    for name in NAMES:
        response = client.get(f"/preview/image/{name}?date=2026-10-02")
        assert response.status_code == 200
        image = Image.open(io.BytesIO(response.content)).convert("RGB")
        assert image.size == (64, 64) and image.getbbox() is not None
    assert client.get("/preview").status_code == 200


def test_preview_rejects_bad_input(client):
    assert client.get("/preview?fixture=nope").status_code == 404
    assert client.get("/preview?fixture=../../config").status_code == 404
    assert client.get("/preview?date=yesterday").status_code == 422
    assert client.get(f"/preview/image/load?fixture={WEEK_41}").status_code == 404
    assert client.get(f"/preview/image/week?fixture={WEEK_41}&scale=3").status_code == 422


def test_render_tool_writes_the_documented_files(tmp_path, settings, capsys):
    out = tmp_path / "preview"
    fixture = f"fixtures/days/{STALE}.json"
    assert render_main(["--fixture", fixture, "--out", str(out), "--scale", "8"]) == 0
    stills = {"frame_1x.png", "frame_8x.png", "frame_gamma_1x.png", "frame_gamma_8x.png"}
    clips = {"clip_1x.gif", "clip_8x.gif", "clip_gamma_1x.gif", "clip_gamma_8x.gif"}
    assert {p.name for p in out.iterdir()} == set(NAMES)
    assert {p.name for p in (out / "today").iterdir()} == stills
    for name in ("week", "books", "sparkle", "party"):
        assert {p.name for p in (out / name).iterdir()} == stills | clips
    assert Image.open(out / "today" / "frame_1x.png").size == (64, 64)
    assert Image.open(out / "today" / "frame_gamma_8x.png").size == (512, 512)
    assert "week: 16 frames, 4000 ms" in capsys.readouterr().out


def test_render_tool_by_date_reads_the_database(tmp_path, settings):
    out = tmp_path / "by-date"
    assert render_main(["--date", "2026-10-02", "--out", str(out), "--scale", "4"]) == 0
    assert Image.open(out / "week" / "frame_4x.png").size == (256, 256)
    assert Image.open(out / "week" / "frame_gamma_1x.png").getbbox() is not None
