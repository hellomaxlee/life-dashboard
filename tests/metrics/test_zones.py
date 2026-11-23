"""Zone bounds, sample spacing, gaps and the no-samples rule."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.metrics.zones import Sample, edwards_load, zone_floors, zone_of, zone_seconds
from app.timeutil import to_utc_iso

T0 = datetime(2026, 10, 1, 11, 0, tzinfo=UTC)


def at(minutes: float) -> str:
    return to_utc_iso(T0 + timedelta(minutes=minutes))


def test_zone_floors_are_the_goal_model_table():
    assert zone_floors(189, (0.5, 0.6, 0.7, 0.8, 0.9)) == (95, 113, 132, 151, 170)


@pytest.mark.parametrize(
    ("bpm", "zone"),
    [
        (94, 0),
        (94.9, 0),
        (95, 1),
        (112, 1),
        (113, 2),
        (131, 2),
        (132, 3),
        (150, 3),
        (151, 4),
        (169, 4),
        (170, 5),
        (200, 5),
    ],
)
def test_zone_of_uses_whole_bpm_floors(bpm, zone):
    assert zone_of(bpm, (95, 113, 132, 151, 170)) == zone


def test_each_sample_covers_until_the_next_and_the_last_until_the_end():
    floors = (95, 113, 132, 151, 170)
    samples = [Sample(at(0), 100), Sample(at(2), 120), Sample(at(5), 140)]
    seconds = zone_seconds(samples, at(10), floors, max_gap_s=600)
    assert seconds == [120.0, 180.0, 300.0, 0.0, 0.0]
    assert edwards_load(samples, at(10), floors, 600) == 2 * 1 + 3 * 2 + 5 * 3


def test_a_gap_longer_than_the_cap_scores_only_the_cap():
    floors = (95, 113, 132, 151, 170)
    samples = [Sample(at(0), 140), Sample(at(20), 140)]
    assert zone_seconds(samples, at(21), floors, max_gap_s=300) == [0.0, 0.0, 360.0, 0.0, 0.0]


def test_time_before_the_first_sample_and_unreadable_samples_score_nothing():
    floors = (95, 113, 132, 151, 170)
    samples = [Sample(at(5), None), Sample(at(6), 140)]
    assert edwards_load(samples, at(7), floors, 300) == 3.0


def test_unsorted_samples_are_ordered_by_time():
    floors = (95, 113, 132, 151, 170)
    samples = [Sample(at(1), 170), Sample(at(0), 100)]
    assert edwards_load(samples, at(2), floors, 300) == 1 * 1 + 1 * 5


def test_no_samples_is_no_load_not_zero():
    assert edwards_load([], at(40), (95, 113, 132, 151, 170), 300) is None


def test_below_zone_one_scores_zero():
    assert edwards_load([Sample(at(0), 90)], at(30), (95, 113, 132, 151, 170), 3600) == 0.0


def test_load_is_rounded_half_up_to_one_decimal():
    floors = (95, 113, 132, 151, 170)
    assert edwards_load([Sample(at(0), 100)], at(0.25), floors, 300) == 0.3
