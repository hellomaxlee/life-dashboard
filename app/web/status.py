"""GET / status page and GET /healthz. Plain HTML, no scripts, no external assets."""

from __future__ import annotations

import sqlite3
from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.config import Settings
from app.db import table_names
from app.timeutil import utc_iso_to_local_display
from app.web.nav import NAV_STYLE, nav_html

router = APIRouter()

_STYLE = (
    "body{font-family:system-ui,sans-serif;max-width:60rem;margin:2rem auto;padding:0 1rem;"
    "color:#222;background:#fafafa}table{border-collapse:collapse;margin:0 0 1.5rem}"
    "td,th{border:1px solid #ccc;padding:.25rem .6rem;text-align:left;font-size:.9rem}"
    "h2{margin-top:2rem}.flag{color:#a33}"
)


def _table(headers: list[str], rows: list[list[object]]) -> str:
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join(
        "<tr>"
        + "".join(f"<td>{escape(str(c)) if c is not None else '—'}</td>" for c in r)
        + "</tr>"
        for r in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render_status(conn: sqlite3.Connection, settings: Settings) -> str:
    last = conn.execute(
        "SELECT source, MAX(received_at_utc) AS at FROM raw_archive GROUP BY source ORDER BY source"
    ).fetchall()
    if not last:
        last_line = "<p>No push received yet.</p>"
    else:
        last_line = "".join(
            f"<p>Last {escape(r['source'])} push: <b>{escape(r['at'])}</b> "
            f"({escape(utc_iso_to_local_display(r['at'], settings.home_tz))})</p>"
            for r in last
        )

    counts = [
        [name, conn.execute(f'SELECT COUNT(*) AS n FROM "{name}"').fetchone()["n"]]
        for name in table_names(conn)
    ]
    recent = conn.execute(
        "SELECT id, source, received_at_utc, byte_len, parsed_ok, error, path FROM raw_archive "
        "ORDER BY received_at_utc DESC, id DESC LIMIT 5"
    ).fetchall()
    recent_rows = [
        [
            r["id"],
            r["source"],
            r["received_at_utc"],
            r["byte_len"],
            "yes" if r["parsed_ok"] else "no",
            r["error"],
            r["path"].rsplit("/", 1)[-1],
        ]
        for r in recent
    ]
    flagged = conn.execute(
        "SELECT id, type, start_utc, duration_s, hr_sample_count, hr_span_s FROM activities "
        "WHERE hr_incomplete = 1 ORDER BY start_utc DESC LIMIT 50"
    ).fetchall()
    flagged_rows = [
        [a["id"], a["type"], a["start_utc"], a["duration_s"], a["hr_sample_count"], a["hr_span_s"]]
        for a in flagged
    ]
    flagged_html = (
        _table(
            ["activity", "type", "start (UTC)", "duration s", "HR samples", "HR span s"],
            flagged_rows,
        )
        if flagged_rows
        else "<p>None.</p>"
    )
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>life-dashboard status</title><style>{_STYLE}{NAV_STYLE}</style></head><body>"
        + nav_html("/")
        + "<h1>life-dashboard</h1>"
        f"{last_line}"
        "<h2>Rows per table</h2>"
        + _table(["table", "rows"], counts)
        + "<h2>Last 5 raw payloads</h2>"
        + _table(
            ["id", "source", "received (UTC)", "bytes", "parsed", "error", "file"], recent_rows
        )
        + "<h2 class='flag'>Activities with incomplete HR</h2>"
        + flagged_html
        + "</body></html>"
    )


@router.get("/", response_class=HTMLResponse)
def status_page(request: Request) -> HTMLResponse:
    conn = request.app.state.open_conn()
    try:
        return HTMLResponse(render_status(conn, request.app.state.settings))
    finally:
        conn.close()


@router.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}
