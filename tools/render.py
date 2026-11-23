"""Render every screen and both celebration clips to files, for the eye test.

python -m tools.render --fixture fixtures/days/<combo>.json --out data/preview/<combo>
python -m tools.render --date YYYY-MM-DD --scale 8

One folder per screen under --out (today, city, week, month, books, sparkle, party), each holding
frame_1x.png, frame_<scale>x.png, frame_gamma_1x.png, frame_gamma_<scale>x.png and, for an
animated clip, clip_1x.gif, clip_<scale>x.gif, clip_gamma_1x.gif, clip_gamma_<scale>x.gif; a
paged screen (city, month, books) also gets page_<n>_1x.png and page_<n>_gamma_1x.png.
A celebration the day's data did not earn is printed as "(sample)".
See workflows/render.md.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from app.config import REPO_ROOT, Settings, load_settings
from app.db import SchemaMismatch, connect_live, open_db
from app.render.adapters.file import gif_bytes, png_bytes
from app.render.celebrate import celebrations_for
from app.render.frame import Clip
from app.render.gamma import led_gamma
from app.render.rotation import device_parts, rotation_clips
from app.render.view import DayView, load_fixture
from app.render.view_db import view_from_db
from app.timeutil import from_utc_iso, now_utc


def render_all(view: DayView, now: datetime) -> list[tuple[str, Clip, bool]]:
    """(name, clip, sample) for the rotation in order, then the two celebrations.

    `sample` is True for a celebration the day's data did not earn.
    """
    rotation = [(name, clip, False) for name, clip in rotation_clips(view, now)]
    return rotation + [(c.name, c.clip, not c.earned) for c in celebrations_for(view)]


def write_clip(clip: Clip, directory: Path, scale: int) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    files: dict[str, bytes] = {
        "frame_1x.png": png_bytes(clip.poster),
        f"frame_{scale}x.png": png_bytes(clip.poster, scale),
        "frame_gamma_1x.png": png_bytes(led_gamma(clip.poster)),
        f"frame_gamma_{scale}x.png": png_bytes(led_gamma(clip.poster), scale),
    }
    if clip.animated and len(device_parts(clip)) > 1:
        for number, page in enumerate(clip.frames, start=1):
            files[f"page_{number}_1x.png"] = png_bytes(page)
            files[f"page_{number}_gamma_1x.png"] = png_bytes(led_gamma(page))
    if clip.animated:
        files["clip_1x.gif"] = gif_bytes(clip)
        files[f"clip_{scale}x.gif"] = gif_bytes(clip, scale)
        files["clip_gamma_1x.gif"] = gif_bytes(clip, transform=led_gamma)
        files[f"clip_gamma_{scale}x.gif"] = gif_bytes(clip, scale, led_gamma)
    written = []
    for name, body in files.items():
        path = directory / name
        path.write_bytes(body)
        written.append(path)
    return written


def _view_for(args: argparse.Namespace, settings: Settings) -> tuple[DayView, datetime, str]:
    """ValueError or OSError with a message fit to print if the fixture or the date is bad."""
    if args.fixture:
        view, now = load_fixture(Path(args.fixture), settings)
        return view, now, Path(args.fixture).stem
    try:
        day_local = date.fromisoformat(args.date).isoformat()
    except ValueError:
        raise ValueError(f"--date {args.date} is not a valid date (YYYY-MM-DD)") from None
    db_path = settings.storage.db_path
    try:
        conn = connect_live(db_path) if db_path.is_file() else open_db(db_path)
    except SchemaMismatch as exc:
        raise ValueError(str(exc)) from None
    try:
        view = view_from_db(conn, settings, day_local)
    finally:
        conn.close()
    return view, now_utc(), day_local


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools.render", description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fixture", help="a fixtures/days/<combo>.json file")
    source.add_argument("--date", help="a home-timezone day to read from the database")
    parser.add_argument("--out", help="output folder (default data/preview/<combo or date>)")
    parser.add_argument("--scale", type=int, default=8, help="the enlarged size (default 8)")
    parser.add_argument("--now", help="UTC ISO 'now' for the usage bar; overrides the default")
    args = parser.parse_args(argv)
    if args.scale < 2:
        parser.error("--scale must be 2 or more; the 1x files are always written")

    settings = load_settings()
    try:
        view, now, label = _view_for(args, settings)
        if args.now:
            now = from_utc_iso(args.now)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    out = Path(args.out) if args.out else REPO_ROOT / "data" / "preview" / label
    for name, clip, sample in render_all(view, now):
        written = write_clip(clip, out / name, args.scale)
        frames = len(clip.frames)
        shown = f"{name} (sample)" if sample else name
        print(f"{shown}: {frames} frame{'s' if frames != 1 else ''}, {clip.total_ms} ms")
        for path in written:
            print(f"  {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
