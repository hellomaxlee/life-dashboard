from __future__ import annotations

import io
from datetime import UTC, datetime

from PIL import Image, ImageChops

from app.render.gamma import led_gamma
from app.timeutil import local_day
from app.web.preview import PLACEHOLDER
from tests.render import STALE, WEEK_41, load, rotation
from tools.render import main as render_main

NAMES = ("today", "city", "week", "month", "books", "sparkle", "party")


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
    assert page.text.count("<img ") == 28
    order = [page.text.index(f"/preview/image/{name}?") for name in NAMES]
    assert order == sorted(order), "rows follow the rotation: today, city, week, month, books"


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
    for name in ("month", "books", "sparkle", "party"):
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


def pages(count: int) -> set[str]:
    names = (f"page_{n}_{kind}1x.png" for n in range(1, count + 1) for kind in ("", "gamma_"))
    return set(names)


def test_render_tool_writes_the_documented_files(tmp_path, settings, capsys):
    out = tmp_path / "preview"
    fixture = f"fixtures/days/{STALE}.json"
    assert render_main(["--fixture", fixture, "--out", str(out), "--scale", "8"]) == 0
    stills = {"frame_1x.png", "frame_8x.png", "frame_gamma_1x.png", "frame_gamma_8x.png"}
    clips = {"clip_1x.gif", "clip_8x.gif", "clip_gamma_1x.gif", "clip_gamma_8x.gif"}
    assert {p.name for p in out.iterdir()} == set(NAMES)
    assert {p.name for p in (out / "today").iterdir()} == stills
    assert {p.name for p in (out / "month").iterdir()} == stills, "no feature: the calendar"
    assert {p.name for p in (out / "week").iterdir()} == stills | clips
    view, now = load(STALE, settings)
    for name, count in (("sparkle", 4), ("party", 6)):
        assert {p.name for p in (out / name).iterdir()} == stills | clips | pages(count), name
    for name in ("city", "books"):
        count = len(rotation(view, now)[name].frames)
        assert count > 1
        assert {p.name for p in (out / name).iterdir()} == stills | clips | pages(count), name
    for number in (1, 2):
        page = Image.open(out / "city" / f"page_{number}_1x.png").convert("RGB")
        assert page.tobytes() == rotation(view, now)["city"].frames[number - 1].tobytes()
    assert Image.open(out / "today" / "frame_1x.png").size == (64, 64)
    assert Image.open(out / "today" / "frame_gamma_8x.png").size == (512, 512)
    printed = capsys.readouterr().out
    assert "week: 16 frames, 4000 ms" in printed and "month: 1 frame, 8000 ms" in printed
    names = [line.split(":")[0] for line in printed.splitlines() if not line.startswith(" ")]
    assert names == [
        "today",
        "city",
        "week",
        "month",
        "books",
        "sparkle (sample)",
        "party (sample)",
    ]
    with_note = tmp_path / "with-note"
    fixture = f"fixtures/days/{WEEK_41}.json"
    assert render_main(["--fixture", fixture, "--out", str(with_note)]) == 0
    assert {p.name for p in (with_note / "month").iterdir()} == stills | clips | pages(2)
    assert "month: 2 frames, 11000 ms" in capsys.readouterr().out


def test_render_tool_by_date_reads_the_database(tmp_path, settings):
    out = tmp_path / "by-date"
    assert render_main(["--date", "2026-10-02", "--out", str(out), "--scale", "4"]) == 0
    assert Image.open(out / "week" / "frame_4x.png").size == (256, 256)
    assert Image.open(out / "week" / "frame_gamma_1x.png").getbbox() is not None


def test_an_empty_database_shows_the_labelled_placeholder_until_data_lands(client, db, settings):
    today = local_day(datetime.now(UTC), settings.home_tz)
    for path in ("/preview", "/pixoo"):
        page = client.get(path)
        assert page.status_code == 200
        assert "Placeholder data." in page.text and PLACEHOLDER in page.text
        assert f"{path}?placeholder=0" in page.text
        plain = client.get(f"{path}?placeholder=0")
        assert "Placeholder data." not in plain.text and today in plain.text
        assert "Placeholder data." not in client.get(f"{path}?date={today}").text
    assert f"/preview/image/week?fixture={PLACEHOLDER}" in client.get("/preview").text

    db.execute(
        "INSERT INTO daily_metrics (day_local, metrics_json) VALUES (?, ?)",
        (today, '{"steps": 4200, "quality_workout": false, "workout_count": 0}'),
    )
    for path in ("/preview", "/pixoo"):
        page = client.get(path)
        assert "Placeholder data." not in page.text and today in page.text
    # the frames a device would get never carry placeholder numbers
    assert client.get("/pixoo/rotation.json").json()["source"] == {"date": today}
