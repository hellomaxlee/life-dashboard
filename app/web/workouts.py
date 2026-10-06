"""GET /workouts and POST /workouts: credit a day with a quality workout by hand, or take
the credit away. Plain HTML form, no scripts, no external assets, single-user LAN page.
Each post recomputes the metrics rows before redirecting back, so the dashboard shows the
change at once. The form body is parsed by hand (no multipart dependency)."""

from __future__ import annotations

import sqlite3
from html import escape
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.config import Settings
from app.metrics import manual
from app.metrics.engine import recompute
from app.timeutil import local_day, now_utc, utc_iso_to_local_display
from app.web.nav import NAV_STYLE, nav_html

router = APIRouter()
PATH = "/workouts"

_STYLE = (
    "body{font-family:system-ui,sans-serif;max-width:40rem;margin:2rem auto;padding:0 1rem;"
    "color:#222;background:#fafafa}table{border-collapse:collapse;margin:0 0 1.5rem}"
    "td,th{border:1px solid #ccc;padding:.25rem .6rem;text-align:left;font-size:.9rem}"
    "form.add{margin:0 0 1.5rem}form.add label{display:block;margin:.4rem 0}"
    "input[type=text]{width:100%;max-width:30rem}button{font:inherit}"
    ".note{font-size:.85rem;opacity:.8}.error{color:#a33}"
)


def render_workouts(conn: sqlite3.Connection, settings: Settings, error: str | None = None) -> str:
    today = local_day(now_utc(), settings.home_tz)
    rows = manual.list_overrides(conn)
    if rows:
        body = "".join(
            "<tr>"
            f"<td>{escape(row.day_local)}</td>"
            f"<td>{escape(row.note) or '—'}</td>"
            f"<td>{escape(utc_iso_to_local_display(row.created_at_utc, settings.home_tz))}</td>"
            "<td><form method='post' action='/workouts'>"
            f"<input type='hidden' name='date' value='{escape(row.day_local, quote=True)}'>"
            "<input type='hidden' name='action' value='remove'>"
            "<button type='submit'>remove</button></form></td>"
            "</tr>"
            for row in rows
        )
        table = (
            "<table><thead><tr><th>day</th><th>note</th><th>recorded</th><th></th></tr></thead>"
            f"<tbody>{body}</tbody></table>"
        )
    else:
        table = "<p>No overrides.</p>"
    error_html = f"<p class='error'>{escape(error)}</p>" if error else ""
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>life-dashboard workouts</title><style>{_STYLE}{NAV_STYLE}</style></head><body>"
        + nav_html(PATH)
        + "<h1>Manual workouts</h1>"
        "<p class='note'>Credit a day with a quality workout when the feed cannot. One per "
        "day; adding a day again replaces its note. No heart rate is involved: the dot is "
        "your word, marked manual.</p>"
        + error_html
        + "<form class='add' method='post' action='/workouts'>"
        "<input type='hidden' name='action' value='add'>"
        f"<label>Day <input type='date' name='date' value='{escape(today, quote=True)}' "
        f"max='{escape(today, quote=True)}' required></label>"
        f"<label>Note <input type='text' name='note' maxlength='{manual.NOTE_MAX}' "
        "placeholder='4 mile run'></label>"
        "<button type='submit'>Credit this day</button></form>"
        "<h2>Overrides</h2>" + table + "</body></html>"
    )


def _form(body: bytes) -> dict[str, str]:
    parsed = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items() if values}


@router.get(PATH, response_class=HTMLResponse)
def workouts_page(request: Request) -> HTMLResponse:
    conn = request.app.state.open_conn()
    try:
        return HTMLResponse(render_workouts(conn, request.app.state.settings))
    finally:
        conn.close()


@router.post(PATH)
async def workouts_post(request: Request) -> Response:
    settings: Settings = request.app.state.settings
    form = _form(await request.body())
    conn = request.app.state.open_conn()
    try:
        try:
            day = manual.parse_day(form.get("date", ""), settings)
        except ValueError as exc:
            return HTMLResponse(render_workouts(conn, settings, str(exc)), status_code=400)
        if form.get("action") == "remove":
            manual.remove_override(conn, day)
        else:
            manual.add_override(conn, day, form.get("note", ""))
        recompute(conn, settings)
    finally:
        conn.close()
    return RedirectResponse(PATH, status_code=303)
