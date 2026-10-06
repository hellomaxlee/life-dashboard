"""The metrics goldens were hand-computed for the goal model's original parameters: a
placeholder bar of 100 with run-based calibration on. Max lowered the shipped bar to 60 and
switched calibration off on 2026-10-05; the engine's arithmetic is what these tests pin, so
they keep the parameters they were written for. `test_shipped_bar.py` pins the shipped ones."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.config import Settings


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return replace(settings, workout=replace(settings.workout, load_bar=100.0, calibrate=True))
