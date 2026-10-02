"""Pixel-exact snapshots of every screen and both clips.

The poster frame of each clip is a committed PNG; every frame and duration of each clip is
covered by a digest in snapshots/digests.json. UPDATE_SNAPSHOTS=1 rewrites them; a changed
snapshot is reviewed pixel by pixel (frame-preview skill, step 3), never regenerated blindly.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from app.render.celebrate import party_clip, sparkle_clip
from app.render.frame import Clip
from app.render.screens import SCREEN_ORDER, render_rotation
from app.render.view import DayView
from app.timeutil import from_utc_iso
from tests.render import COMBOS, SNAPSHOTS, load

UPDATE = os.environ.get("UPDATE_SNAPSHOTS", "") == "1"
DIGESTS = SNAPSHOTS / "digests.json"
EMPTY_NOW = "2026-10-02T16:00:00Z"


def clip_digest(clip: Clip) -> str:
    digest = hashlib.sha256()
    for frame, ms in zip(clip.frames, clip.durations_ms, strict=True):
        digest.update(frame.tobytes())
        digest.update(str(ms).encode())
    return digest.hexdigest()


def check(clip: Clip, key: str) -> None:
    path = SNAPSHOTS / f"{key}.png"
    digests = json.loads(DIGESTS.read_text()) if DIGESTS.exists() else {}
    if UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        clip.poster.save(path)
        digests[key] = {"frames": len(clip.frames), "sha256": clip_digest(clip)}
        DIGESTS.write_text(json.dumps(digests, indent=2, sort_keys=True) + "\n")
        return
    assert path.exists(), f"no snapshot for {key}; review a render, then UPDATE_SNAPSHOTS=1"
    golden = Image.open(path).convert("RGB")
    diff = ImageChops.difference(clip.poster, golden).getbbox()
    assert diff is None, f"{key}: poster differs from snapshot inside box {diff}"
    assert digests[key] == {"frames": len(clip.frames), "sha256": clip_digest(clip)}, key


@pytest.mark.parametrize("combo", COMBOS)
@pytest.mark.parametrize("screen", SCREEN_ORDER)
def test_screen_matches_snapshot(combo, screen, settings):
    view, now = load(combo, settings)
    check(render_rotation(view, now)[screen], f"{combo}/{screen}")


@pytest.mark.parametrize("screen", SCREEN_ORDER)
def test_empty_view_matches_snapshot(screen):
    clips = render_rotation(DayView(day_local="2026-10-02"), from_utc_iso(EMPTY_NOW))
    check(clips[screen], f"empty/{screen}")


@pytest.mark.parametrize("win", ["sleep", "workout", "book"])
def test_sparkle_matches_snapshot(win):
    check(sparkle_clip(win), f"celebrations/sparkle_{win}")


def test_party_matches_snapshot():
    check(party_clip(3), "celebrations/party")


def test_no_orphan_snapshots():
    expected = {f"{c}/{s}" for c in COMBOS for s in SCREEN_ORDER}
    expected |= {f"empty/{s}" for s in SCREEN_ORDER}
    expected |= {f"celebrations/sparkle_{w}" for w in ("sleep", "workout", "book")}
    expected |= {"celebrations/party"}
    on_disk = {
        str(Path(p).relative_to(SNAPSHOTS).with_suffix("")) for p in SNAPSHOTS.rglob("*.png")
    }
    assert on_disk == expected
    assert set(json.loads(DIGESTS.read_text())) == expected
