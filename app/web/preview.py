"""GET /preview: every screen (Today, Week, Month, Books) and both celebrations, at 1x and 8x,
raw and through LED gamma.

/preview?date=YYYY-MM-DD reads the database for that home-timezone day (default: today).
/preview?fixture=<combo> shows a fixtures/days file by its name, with the fixture's own "now".
With neither, a database that has no Health data for today or yesterday shows the placeholder
fixture under a banner that says so; real data replaces it as soon as a push lands, and
/preview?placeholder=0 always shows the database. Only these pages do this; the device never
shows placeholder numbers.
Images are PNG or GIF bytes from Pillow at /preview/image/<screen>; no scripts, no canvas, no
external assets. Judge legibility on the 1x gamma column, never on the 8x one.
"""

from __future__ import annotations

from datetime import date, datetime
from html import escape
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response

from app.config import REPO_ROOT
from app.render.adapters.file import gif_bytes, png_bytes
from app.render.celebrate import CELEBRATION_ORDER, celebrations_for
from app.render.frame import Clip
from app.render.gamma import led_gamma
from app.render.rotation import ROTATION_ORDER, rotation_clips
from app.render.view import DayView, has_health_data, load_fixture
from app.render.view_db import view_from_db
from app.timeutil import local_day, now_utc
from app.web.nav import NAV_STYLE, nav_html

router = APIRouter()

FIXTURES_DIR = REPO_ROOT / "fixtures" / "days"
NAMES = ROTATION_ORDER + CELEBRATION_ORDER
SCALES = (1, 8)
PLACEHOLDER = "train__all-sources__alive__base"
PLACEHOLDER_BANNER = (
    "<p class='placeholder'><b>Placeholder data.</b> No Health data has arrived for today or "
    "yesterday, so this is the sample day <code>{stem}</code>, not yours. It switches to the "
    "database on its own once a push lands. <a href='{path}?placeholder=0'>Show the database "
    "anyway</a>.</p>"
)
PLACEHOLDER_STYLE = (
    ".placeholder{border:1px solid #c80;background:#2a2008;color:#fd9;padding:.5rem .75rem;"
    "border-radius:4px;font-size:.85rem;max-width:44rem}"
)

_STYLE = (
    "body{font-family:system-ui,sans-serif;margin:2rem auto;max-width:76rem;padding:0 1rem;"
    "color:#ddd;background:#111}a{color:#8bf}table{border-collapse:collapse}"
    "td,th{padding:.5rem .75rem;text-align:left;vertical-align:top;font-size:.85rem}"
    "img{image-rendering:pixelated;display:block;background:#000}"
)


def fixture_paths() -> dict[str, Path]:
    return {path.stem: path for path in sorted(FIXTURES_DIR.glob("*.json"))}


def _resolve(request: Request, day: str | None, fixture: str | None) -> tuple[DayView, datetime]:
    settings = request.app.state.settings
    if fixture is not None:
        path = fixture_paths().get(fixture)
        if path is None:
            raise HTTPException(status_code=404, detail="unknown fixture")
        return load_fixture(path, settings)
    now = now_utc()
    day_local = day or local_day(now, settings.home_tz)
    try:
        day_local = date.fromisoformat(day_local).isoformat()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD") from exc
    conn = request.app.state.open_conn()
    try:
        return view_from_db(conn, settings, day_local), now
    finally:
        conn.close()


def resolve_page(
    request: Request, day: str | None, fixture: str | None, placeholder: bool
) -> tuple[DayView, datetime, str | None, bool]:
    """(view, now, fixture shown, is placeholder) for a page. The placeholder stands in only
    when nothing was asked for by name and the database has no Health data to show."""
    view, now = _resolve(request, day, fixture)
    asked = day is not None or fixture is not None
    if asked or not placeholder or has_health_data(view) or PLACEHOLDER not in fixture_paths():
        return view, now, fixture, False
    view, now = _resolve(request, None, PLACEHOLDER)
    return view, now, PLACEHOLDER, True


def _clips(view: DayView, now: datetime) -> dict[str, tuple[Clip, bool]]:
    """name -> (clip, sample). A sample is a celebration the day's data did not earn."""
    clips = {name: (clip, False) for name, clip in rotation_clips(view, now)}
    clips.update({c.name: (c.clip, not c.earned) for c in celebrations_for(view)})
    return clips


@router.get("/preview/image/{name}")
def preview_image(
    request: Request,
    name: str,
    day: str | None = Query(default=None, alias="date"),
    fixture: str | None = None,
    scale: int = 1,
    gamma: int = 0,
) -> Response:
    if name not in NAMES:
        raise HTTPException(status_code=404, detail="unknown screen")
    if scale not in SCALES:
        raise HTTPException(status_code=422, detail="scale must be 1 or 8")
    view, now = _resolve(request, day, fixture)
    clip, _ = _clips(view, now)[name]
    transform = led_gamma if gamma else None
    if clip.animated:
        return Response(gif_bytes(clip, scale, transform), media_type="image/gif")
    frame = transform(clip.poster) if transform else clip.poster
    return Response(png_bytes(frame, scale), media_type="image/png")


@router.get("/preview", response_class=HTMLResponse)
def preview_page(
    request: Request,
    day: str | None = Query(default=None, alias="date"),
    fixture: str | None = None,
    placeholder: bool = True,
) -> HTMLResponse:
    view, now, fixture, is_placeholder = resolve_page(request, day, fixture, placeholder)
    source = {"fixture": fixture} if fixture is not None else {"date": view.day_local}
    banner = (
        PLACEHOLDER_BANNER.format(stem=escape(PLACEHOLDER), path="/preview")
        if is_placeholder
        else ""
    )
    clips = _clips(view, now)
    rows = []
    for name in NAMES:
        clip, sample = clips[name]
        shown = f"{name} (sample)" if sample else name
        cells = [
            f"<th>{escape(shown)}<br><small>{len(clip.frames)} frame"
            f"{'' if len(clip.frames) == 1 else 's'}, {clip.total_ms} ms</small></th>"
        ]
        for gamma in (1, 0):
            for scale in SCALES:
                query = urlencode({**source, "scale": scale, "gamma": gamma})
                size = 64 * scale
                cells.append(
                    f"<td><img src='/preview/image/{name}?{escape(query)}' "
                    f"width='{size}' height='{size}' alt='{escape(name)} {scale}x'></td>"
                )
        rows.append("<tr>" + "".join(cells) + "</tr>")
    links = " · ".join(
        f"<a href='/preview?{escape(urlencode({'fixture': stem}))}'>{escape(stem)}</a>"
        for stem in fixture_paths()
    )
    title = fixture if fixture is not None else view.day_local
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>life-dashboard preview</title><style>{_STYLE}{NAV_STYLE}{PLACEHOLDER_STYLE}"
        "</style></head><body>"
        + nav_html("/preview")
        + f"<h1>Preview: {escape(title)}</h1>"
        + banner
        + "<p>Judge legibility on the <b>1x LED gamma</b> column at native size. "
        "The 8x columns are for inspecting pixels only. A celebration marked (sample) was not "
        "earned by this day's data and is shown only so it can be looked at.</p>"
        "<table><thead><tr><th>screen</th><th>LED gamma 1x</th><th>LED gamma 8x</th>"
        "<th>raw 1x</th><th>raw 8x</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        f"<p><a href='/preview'>today</a> · fixtures: {links}</p>"
        "</body></html>"
    )
