"""The one-line navigation shared by the status, preview and device pages. Relative links only."""

from __future__ import annotations

from html import escape

PAGES = (("/", "status"), ("/preview", "frame preview"), ("/pixoo", "device"))

NAV_STYLE = (
    "nav.pages{font-size:.8rem;margin:0 0 1rem;opacity:.8}nav.pages a{margin-right:.75rem}"
    "nav.pages b{margin-right:.75rem}"
)


def nav_html(current: str) -> str:
    """`current` is the path of the page being rendered; it is shown as text, not a link."""
    parts = []
    for path, label in PAGES:
        if path == current:
            parts.append(f"<b>{escape(label)}</b>")
        else:
            parts.append(f"<a href='{escape(path)}'>{escape(label)}</a>")
    return "<nav class='pages'>" + "".join(parts) + "</nav>"
