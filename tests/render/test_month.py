"""The Month screen: the feature's plate and note, the calendar fallback, and where it sits
in the rotation. The feature used is the hand-made sample under fixtures/month."""

from __future__ import annotations

import calendar
import json
import sqlite3
from dataclasses import replace
from datetime import date

import pytest

from app.month import store
from app.month.spec import ART_SIZE, DayPlate, MonthFeature, parse_feature
from app.render import month as month_screen
from app.render.font import BODY, SMALL, text_width
from app.render.frame import SIZE
from app.render.gamma import led_gamma, led_lut
from app.render.month import (
    CELL,
    NOTE_BOTTOM,
    NOTE_MS,
    NOTE_TOP,
    PLATE_MS,
    PLATE_SCALE,
    PLATE_X,
    PLATE_Y,
    calendar_cells,
    calendar_layout,
    legible,
    note_layout,
    rail_pips,
    render_month,
)
from app.render.palette import TRACK, WHITE
from app.render.rotation import device_parts, hold_ms, rotation_sequence, sequence_names
from app.render.view import DayView, fixture_feature
from app.render.view_db import view_from_db
from app.timeutil import from_utc_iso
from tests.render import DAYS, MONTHS, SAMPLE_FEATURE, STALE, WEEK_41, load, record_text

NOW = from_utc_iso("2026-10-02T16:00:00Z")
BLACK = (0, 0, 0)


def sample_raw() -> dict:
    return json.loads((MONTHS / f"{SAMPLE_FEATURE}.json").read_text())


def sample(month: str = "2026-10") -> MonthFeature:
    return fixture_feature(DAYS / "any.json", SAMPLE_FEATURE, month)


def view_on(day: str, feature: MonthFeature | None = None) -> DayView:
    return DayView(day_local=day, month_feature=feature if feature else sample(day[:7]))


def extent(recorded: list, text: str) -> tuple[int, int]:
    drawn, x, _, font_name, _ = next(r for r in recorded if r[0] == text)
    return x, x + text_width(drawn, SMALL if font_name == SMALL.name else BODY) - 1


# the sample itself


def test_the_sample_is_a_whole_valid_feature_for_its_own_month():
    raw = sample_raw()
    feature = parse_feature(raw, "2026-10")
    assert len(feature.days) == 31 and len({plate.art for plate in feature.days}) == 31
    assert len({plate.caption for plate in feature.days}) >= 12, "the caption changes by day"
    assert 8 <= sum(1 for plate in feature.days if plate.note) <= 16, "some days carry a note"
    for text in (feature.title, *(plate.caption for plate in feature.days)):
        assert text_width(text.upper(), SMALL) <= month_screen.TEXT_WIDTH, text
    for plate in feature.days:
        if plate.note:
            font, _, lines = note_layout(plate.note)
            assert font is BODY and " ".join(lines) == plate.note, "whole, in the body face"


def test_a_fixture_sample_is_re_dated_to_the_fixture_month_and_bad_names_are_refused(settings):
    assert [len(sample(m).days) for m in ("2027-02", "2028-02", "2026-09")] == [28, 29, 30]
    assert sample("2026-09").month == "2026-09"
    view, _ = load(WEEK_41, settings)
    assert view.month_feature == sample("2026-09")
    assert load(STALE, settings)[0].month_feature is None
    for name in ("../days/x", "nope", "", 7):
        with pytest.raises(ValueError, match="month_feature"):
            fixture_feature(DAYS / "any.json", name, "2026-10")


# the plate page


def test_title_and_caption_are_drawn_whole_inside_the_frame(monkeypatch):
    feature = sample()
    recorded = record_text(monkeypatch)
    clip = render_month(view_on("2026-10-26"), NOW)
    plate = feature.plate("2026-10-26")
    assert plate.caption == "FULL MOON"
    title = next(r for r in recorded if r[0] == "MOON OVER WATER")
    caption = next(r for r in recorded if r[0] == "FULL MOON")
    assert (title[2], title[3]) == (2, SMALL.name) and (caption[2], caption[3]) == (58, SMALL.name)
    for text in ("MOON OVER WATER", "FULL MOON"):
        left, right = extent(recorded, text)
        assert 0 <= left and right <= SIZE - 1
        assert abs(left - (SIZE - 1 - right)) <= 1, "centred"
    first = clip.frames[0]
    assert first.getpixel((title[1], 2)) == feature.palette[0], "the title in the first colour"
    caption_colours = {first.getpixel((x, y)) for x in range(SIZE) for y in range(58, 63)}
    assert caption_colours == {BLACK, feature.palette[1]}, "the caption in the second"
    assert all(first.getpixel((x, y)) == BLACK for x in range(SIZE) for y in (0, 1, 7, 56, 57, 63))


@pytest.mark.parametrize("day", range(1, 32))
def test_the_plate_is_exact_x3_blocks_of_the_requested_days_art(day):
    feature = sample()
    day_local = f"2026-10-{day:02d}"
    frame = render_month(view_on(day_local), NOW).frames[0]
    plate = feature.plate(day_local)
    assert (PLATE_SCALE, PLATE_X, PLATE_Y) == (3, 8, 8)
    for row in range(ART_SIZE):
        for column in range(ART_SIZE):
            cell = plate.art[row][column]
            want = BLACK if cell == "." else feature.palette[int(cell) - 1]
            block = {
                frame.getpixel((PLATE_X + column * 3 + dx, PLATE_Y + row * 3 + dy))
                for dx in range(3)
                for dy in range(3)
            }
            assert block == {want}, (row, column)
    other = feature.plate(f"2026-10-{day % 31 + 1:02d}")
    assert other.art != plate.art


def test_every_colour_of_every_plate_survives_led_gamma_as_its_own_colour():
    feature = sample()
    lut = led_lut()
    shown = [tuple(lut[c] for c in colour) for colour in feature.palette]
    assert BLACK not in shown
    for i, a in enumerate(shown):
        for b in shown[i + 1 :]:
            assert max(abs(x - y) for x, y in zip(a, b, strict=True)) >= 24, (a, b)
    used = set()
    for plate in feature.days:
        frame = led_gamma(render_month(view_on(f"2026-10-{plate.day:02d}"), NOW).frames[0])
        for row in range(ART_SIZE):
            for column in range(ART_SIZE):
                cell = plate.art[row][column]
                pixel = frame.getpixel((PLATE_X + column * 3 + 1, PLATE_Y + row * 3 + 1))
                assert pixel == (BLACK if cell == "." else shown[int(cell) - 1])
                used.add(cell)
    assert used == {".", *"1234567"}, "the sample uses its whole palette"
    night = led_gamma(render_month(view_on("2026-10-26"), NOW).frames[0], brightness=0.1)
    seen = {night.getpixel((x, y)) for x in range(8, 56) for y in range(8, 56)} - {BLACK}
    assert len(seen) >= 4, "at night brightness the plate is still a picture"


def test_the_rails_count_the_days_gone_today_and_the_days_to_come():
    feature = sample()
    frame = render_month(view_on("2026-10-18"), NOW).frames[0]
    pips = rail_pips(31)
    assert len(pips) == 31 and {x for _, x, _ in pips} == {3, 59}
    assert all(8 <= y <= 54 for _, _, y in pips)
    colours = {day: frame.getpixel((x, y)) for day, x, y in pips}
    assert colours[18] == WHITE
    assert {colours[day] for day in range(1, 18)} == {month_screen.dim(feature.palette[1], 0.45)}
    assert {colours[day] for day in range(19, 32)} == {TRACK}
    lut = led_lut()
    seen = {tuple(lut[c] for c in colours[day]) for day in (1, 18, 31)}
    assert len(seen) == 3 and BLACK not in seen


# the note page


def test_page_two_exists_only_when_the_day_has_a_note(monkeypatch):
    feature = sample()
    noted, plain = "2026-10-10", "2026-10-11"
    assert feature.plate(noted).note and not feature.plate(plain).note
    still = render_month(view_on(plain), NOW)
    assert len(still.frames) == 1 and not still.animated
    assert device_parts(still) == [still]

    recorded = record_text(monkeypatch)
    clip = render_month(view_on(noted), NOW)
    assert clip.durations_ms == (PLATE_MS, NOTE_MS) == (6000, 5000)
    parts = device_parts(clip)
    assert [len(part.frames) for part in parts] == [1, 1], "two stills, never a loop"
    assert [part.durations_ms for part in parts] == [(6000,), (5000,)]
    assert hold_ms("month", clip, 6) == 11000
    note = clip.frames[1]
    lines = [r for r in recorded if r[3] == BODY.name]
    assert " ".join(r[0] for r in lines) == feature.plate(noted).note
    assert [r[0] for r in recorded if r[0] == "MOON OVER WATER"] == ["MOON OVER WATER"] * 2
    top, bottom = lines[0][2], lines[-1][2] + BODY.height - 1
    assert NOTE_TOP <= top and bottom <= NOTE_BOTTOM
    assert abs((top - NOTE_TOP) - (NOTE_BOTTOM - bottom)) <= 1, "centred under the title"
    for _, x, _, _, _ in lines:
        assert x >= 2
    box = note.crop((0, NOTE_TOP, SIZE, SIZE)).getbbox()
    assert box is not None and box[0] >= 2 and box[2] <= 62
    colours = {note.getpixel((x, y)) for x in range(SIZE) for y in range(NOTE_TOP, SIZE)}
    assert colours == {BLACK, feature.palette[0]}, "the note in the palette's brightest colour"
    assert note.crop((0, 0, SIZE, 7)).tobytes() == clip.frames[0].crop((0, 0, SIZE, 7)).tobytes()

    blank = replace(feature.plate(noted), note="   ")
    days = tuple(blank if p.day == 10 else p for p in feature.days)
    assert len(render_month(view_on(noted, replace(feature, days=days)), NOW).frames) == 1


def test_the_longest_text_the_spec_allows_never_leaves_the_frame(monkeypatch):
    raw = sample_raw()
    raw["title"] = "M" * 15
    raw["days"][0]["caption"] = "W" * 15
    raw["days"][0]["note"] = "Wwwwww mmmmmm Wwwwww mmmmmm Wwwwww mmmmmm Wwwwww mmmmmm Wwww"
    raw["days"][1]["note"] = "W" * 60
    raw["palette"][0], raw["palette"][1] = "#0000ff", "#8c0000"
    feature = parse_feature(raw, "2026-10")
    assert len(feature.days[0].note) == 60
    for day in ("2026-10-01", "2026-10-02"):
        recorded = record_text(monkeypatch)
        clip = render_month(view_on(day, feature), NOW)
        assert len(clip.frames) == 2
        for text, x, y, font_name, _ in recorded:
            font = SMALL if font_name == SMALL.name else BODY
            assert 0 <= x and x + text_width(text, font) <= SIZE, text
            assert 0 <= y and y + font.height <= SIZE, text
        for frame in clip.frames:
            assert frame.size == (SIZE, SIZE) and frame.getbbox() is not None
    drawn = [r[0] for r in recorded]
    assert "M" * 10 in drawn and "M" * 11 not in drawn, "whole glyphs only, never half a letter"
    font, pitch, lines = note_layout(feature.days[0].note)
    assert font is SMALL and (len(lines) - 1) * pitch + font.height <= NOTE_BOTTOM - NOTE_TOP + 1
    lut = led_lut()
    for colour in ((0, 0, 255), (140, 0, 0)):
        lifted = legible(colour)
        assert lifted != colour and max(lifted) == max(max(colour), max(lifted))
        assert sum(lut[c] for c in lifted) > sum(lut[c] for c in colour)
    assert legible((255, 240, 180)) == (255, 240, 180)


# the calendar


@pytest.mark.parametrize(
    ("day", "days", "first_column", "rows"),
    [
        ("2027-02-10", 28, 0, 4),
        ("2028-02-29", 29, 1, 5),
        ("2026-09-30", 30, 1, 5),
        ("2026-10-04", 31, 3, 5),
        ("2026-06-17", 30, 0, 5),
        ("2026-08-31", 31, 5, 6),
        ("2026-11-01", 30, 6, 6),
    ],
)
def test_the_calendar_has_one_cell_per_day_laid_out_monday_first(day, days, first_column, rows):
    cells = calendar_cells(day)
    assert len(cells) == days == calendar.monthrange(int(day[:4]), int(day[5:7]))[1]
    assert cells[0] == (1, first_column, 0)
    assert cells[-1][2] == rows - 1
    for n, column, _ in cells:
        assert column == date(int(day[:4]), int(day[5:7]), n).weekday(), "Monday is column 0"
    frame = render_month(DayView(day_local=day), NOW).poster
    header_y, boxes = calendar_layout(day)
    assert len(boxes) == days
    grid_top = header_y + SMALL.height + 1
    lit = sum(
        1 for y in range(grid_top, SIZE) for x in range(SIZE) if frame.getpixel((x, y)) != BLACK
    )
    assert lit == days * CELL * CELL, "exactly one 6x6 cell per day and nothing else"
    today = int(day[8:])
    for x, y in boxes.values():
        assert 0 <= x and x + CELL <= SIZE and grid_top <= y and y + CELL <= SIZE
        block = {frame.getpixel((x + dx, y + dy)) for dx in range(CELL) for dy in range(CELL)}
        assert len(block) == 1 and block != {BLACK}
    colour = {n: frame.getpixel(box) for n, box in boxes.items()}
    past = {colour[n] for n in range(1, today)}
    future = {colour[n] for n in range(today + 1, days + 1)}
    assert colour[today] == WHITE and len(past) <= 1 and len(future) <= 1
    assert colour[today] not in past | future and not past & future
    lut = led_lut()
    shown = {tuple(lut[c] for c in value) for value in {colour[today]} | past | future}
    assert len(shown) == 1 + len(past) + len(future), "all three states differ on LEDs too"


def test_known_dates_sit_in_their_weekday_column():
    assert calendar_cells("2026-10-04")[3] == (4, 6, 0), "Sunday 4 October: the last column"
    assert calendar_cells("2026-10-04")[4] == (5, 0, 1), "Monday 5 October: first column, row two"
    _, boxes = calendar_layout("2026-10-04")
    assert boxes[5][0] == month_screen.GRID_X == 5 and boxes[4][0] == 5 + 6 * 8
    assert boxes[5][1] == boxes[4][1] + 8


def test_the_calendar_says_the_month_and_year_and_uses_nothing_personal(monkeypatch, settings):
    recorded = record_text(monkeypatch)
    rich, now = load(STALE, settings)
    assert rich.month_feature is None and rich.books_ytd == 11
    frame = render_month(rich, now)
    texts = [r[0] for r in recorded]
    assert texts == ["DECEMBER", "2026", *"MTWTFSS"]
    bare = render_month(DayView(day_local=rich.day_local), NOW)
    assert frame.poster.tobytes() == bare.poster.tobytes(), "the date and nothing else"
    assert len(frame.frames) == 1
    names = {
        render_month(DayView(day_local=f"2026-{m:02d}-15")).poster.tobytes() for m in range(1, 13)
    }
    assert len(names) == 12
    colours = {month_screen.month_color(m) for m in range(1, 13)}
    assert len(colours) == 12, "each month has its own colour"
    for m in range(1, 13):
        recorded.clear()
        render_month(DayView(day_local=f"2026-{m:02d}-15"))
        name, year = recorded[0], recorded[1]
        assert name[0] == month_screen.MONTH_NAMES[m - 1] and year[0] == "2026"
        assert 0 <= name[1] and year[1] + text_width("2026") <= SIZE


# which month, and what happens without one


def put_daily(db, day: str, metrics: dict) -> None:
    db.execute(
        "INSERT INTO daily_metrics (day_local, metrics_json) VALUES (?, ?)",
        (day, json.dumps(metrics)),
    )


def test_the_month_is_the_requested_days_even_when_today_falls_back(db, settings, monkeypatch):
    put_daily(db, "2026-10-31", {"sleep_hours": 7.5, "steps": 9000, "quality_workout": True})
    october = sample_raw()
    november = {**sample_raw(), "month": "2026-11", "title": "FIRST FROST"}
    november["days"] = november["days"][:30]
    store.save_feature(db, "2026-10", october, source="test", model=None, raw_reply=None)

    view = view_from_db(db, settings, "2026-11-01")
    assert view.day_shown == "2026-10-31" and view.day_local == "2026-11-01"
    assert view.month_feature is None, "October's feature is not November's"
    recorded = record_text(monkeypatch)
    clip = render_month(view, NOW)
    assert [r[0] for r in recorded][:2] == ["NOVEMBER", "2026"]
    assert len(clip.frames) == 1 and clip.poster.getbbox() is not None

    store.save_feature(db, "2026-11", november, source="test", model=None, raw_reply=None)
    view = view_from_db(db, settings, "2026-11-01")
    assert view.day_shown == "2026-10-31"
    assert view.month_feature.month == "2026-11" and view.month_feature.title == "FIRST FROST"
    recorded.clear()
    clip = render_month(view, NOW)
    texts = [r[0] for r in recorded]
    assert "FIRST FROST" in texts and "MOON OVER WATER" not in texts
    first_of_month = view.month_feature.plate("2026-11-01")
    assert first_of_month.caption.upper() in texts and len(clip.frames) == 2
    last_of_october = parse_feature(october, "2026-10").plate("2026-10-31")
    assert last_of_october.art != first_of_month.art
    for row in range(ART_SIZE):
        for column in range(ART_SIZE):
            cell = first_of_month.art[row][column]
            want = BLACK if cell == "." else view.month_feature.palette[int(cell) - 1]
            assert clip.poster.getpixel((PLATE_X + column * 3, PLATE_Y + row * 3)) == want

    halloween = view_from_db(db, settings, "2026-10-31")
    assert halloween.day_shown is None and halloween.month_feature.title == "MOON OVER WATER"


def test_no_stored_feature_or_a_broken_one_is_the_calendar_never_an_error(
    db, settings, monkeypatch
):
    calendar_frame = render_month(DayView(day_local="2026-10-02"), NOW).poster.tobytes()
    view = view_from_db(db, settings, "2026-10-02")
    assert view.month_feature is None
    assert render_month(view, NOW).poster.tobytes() == calendar_frame

    db.execute(
        "INSERT INTO month_features (month_local, feature_json, source, created_at_utc) "
        "VALUES ('2026-10', '{\"month\": \"2026-10\"}', 'test', '2026-10-01T00:00:00Z')"
    )
    assert view_from_db(db, settings, "2026-10-02").month_feature is None, "unparseable row"

    def broken(conn, month):
        raise sqlite3.OperationalError("no such table: month_features")

    monkeypatch.setattr(store, "load_feature", broken)
    view = view_from_db(db, settings, "2026-10-02")
    assert view.month_feature is None and view.day_local == "2026-10-02"
    names = [name for name, _, _ in rotation_sequence(view, NOW, 6)]
    assert names == ["today", "city", "week", "month", "books"]

    wrong_month = DayView(day_local="2026-10-02", month_feature=sample("2026-09"))
    assert render_month(wrong_month, NOW).poster.tobytes() == calendar_frame
    feature = sample()
    short = replace(feature, days=feature.days[:1])
    assert render_month(view_on("2026-10-02", short), NOW).poster.tobytes() == calendar_frame
    odd = DayPlate(2, "ODD", "", ("9" * 16,) * 16)
    days = (feature.days[0], odd, *feature.days[2:])
    drawn = render_month(view_on("2026-10-02", replace(feature, days=days)), NOW).poster
    assert drawn.getbbox() is not None, "an index past the palette is skipped, not a crash"
    assert drawn.crop((8, 8, 56, 56)).getbbox() is None


# the rotation order


def test_the_sequence_is_day_week_month_year_then_the_wins_then_the_party():
    plain = DayView(day_local="2026-10-02")
    base = ("today", "city", "week", "month", "books")
    assert sequence_names(plain) == base
    wins = replace(plain, today_dot=True, sleep_hours=7.5, book_finished=True, week_dots=2)
    assert sequence_names(wins) == (*base, "win-workout", "win-sleep", "win-book")
    done = replace(plain, week_dots=3, sleep_hours=7.5)
    assert sequence_names(done) == (*base, "win-workout", "win-sleep", "party")
    slots = rotation_sequence(replace(done, month_feature=sample()), NOW, 6)
    assert [name for name, _, _ in slots] == list(sequence_names(done))
    holds = {name: hold for name, _, hold in slots}
    assert holds == {
        "today": 6000,
        "city": 6000,
        "week": 6000,
        "month": 6000,
        "books": 6000,
        "win-workout": 4000,
        "win-sleep": 4000,
        "party": 6000,
    }
    party = slots[-1][1]
    assert len(party.frames) == 6 and len(device_parts(party)) == 6, "six stills, one pass"
