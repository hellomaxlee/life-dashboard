"""spec.parse_feature: each rule rejects with a SpecError that names it; valid features for
28-, 30- and 31-day months parse."""

from __future__ import annotations

import copy

import pytest

from app.month.spec import SpecError, days_in, parse_feature
from tests.month.conftest import feature_object, plate_art

MONTH = "2026-10"


def broken(change) -> dict:
    raw = copy.deepcopy(feature_object(MONTH))
    change(raw)
    return raw


def drop_last_day(raw):
    raw["days"].pop()


def narrow_art(raw):
    raw["days"][4]["art"] = [row[:15] for row in raw["days"][4]["art"]]


def short_art(raw):
    raw["days"][4]["art"] = raw["days"][4]["art"][:15]


def digit_in_caption(raw):
    raw["days"][2]["caption"] = "Door 3"


def digit_in_note(raw):
    raw["days"][2]["note"] = "The 3rd door is ajar."


def dark_colour(raw):
    raw["palette"][1] = "#402010"


def unknown_index(raw):
    raw["days"][0]["art"][0] = "4" + raw["days"][0]["art"][0][1:]


def undrawable(raw):
    raw["days"][6]["caption"] = "Moon & tide"


def too_few_lit(raw):
    raw["days"][9]["art"] = ["1" * 11 + "." * 5] + ["." * 16] * 15


def mostly_duplicates(raw):
    for entry in raw["days"]:
        entry["art"] = plate_art(1 + entry["day"] % 5)


def long_caption(raw):
    raw["days"][0]["caption"] = "Sixteen letters!"


def long_note(raw):
    raw["days"][0]["note"] = "n" * 61


def one_colour(raw):
    raw["palette"] = ["#ff4020"]


def out_of_order(raw):
    raw["days"][3]["day"] = 5


def wrong_month(raw):
    raw["month"] = "2026-11"


@pytest.mark.parametrize(
    ("change", "names"),
    [
        (drop_last_day, "days: wants 31 entries"),
        (narrow_art, "day 5 art row 1: wants 16 cells"),
        (short_art, "day 5 art: wants 16 rows"),
        (digit_in_caption, "day 3 caption: digits are not allowed"),
        (digit_in_note, "day 3 note: digits are not allowed"),
        (dark_colour, "palette 2: #402010 is too dark for LEDs"),
        (unknown_index, "day 1 art row 1: a cell is not one of '.123'"),
        (undrawable, "day 7 caption: the panel cannot draw '&'"),
        (too_few_lit, "day 10 art: fewer than 12 lit cells"),
        (mostly_duplicates, "days: more than half the plates repeat another day's art"),
        (long_caption, "day 1 caption: 16 characters, wants 1 to 15"),
        (long_note, "day 1 note: 61 characters, wants 0 to 60"),
        (one_colour, "palette: wants 2 to 8 colours"),
        (out_of_order, "day 4: missing or out of order"),
        (wrong_month, "month: got '2026-11', wants '2026-10'"),
    ],
)
def test_each_rule_rejects_and_names_itself(change, names):
    with pytest.raises(SpecError) as raised:
        parse_feature(broken(change), MONTH)
    assert str(raised.value) == names


@pytest.mark.parametrize(("month", "count"), [("2027-02", 28), ("2026-11", 30), ("2026-10", 31)])
def test_a_valid_feature_parses_for_every_month_length(month, count):
    assert days_in(month) == count
    feature = parse_feature(feature_object(month), month)
    assert len(feature.days) == count
    assert feature.title == "Small Woods"
    assert feature.palette[0] == (255, 64, 32)
    assert feature.plate(f"{month}-{count:02d}").day == count
    assert feature.plate("2025-01-01") is None
    assert all(len(plate.caption) <= 15 and len(plate.note) <= 60 for plate in feature.days)


def test_a_non_object_and_a_bad_month_are_rejected():
    with pytest.raises(SpecError, match="not a JSON object"):
        parse_feature([feature_object(MONTH)], MONTH)
    with pytest.raises(SpecError, match="not YYYY-MM"):
        parse_feature(feature_object(MONTH), "2026-13")
