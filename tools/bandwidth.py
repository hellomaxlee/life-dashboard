"""Read the two bandwidth scores side by side: the lamp's, and the fuller shadow score.

python -m tools.bandwidth                 the last 14 days, one line a day
python -m tools.bandwidth --days 60       further back
python -m tools.bandwidth --date DATE     that day's trace, term by term

Reads what the engine stored in `daily_metrics`; computes nothing and writes nothing. The
band named beside each score is the colour the Books lamp shows, or would show, for it.
Uses the live db without migrating it (exit 2 if the schema is behind: run tools.migrate).
See workflows/bandwidth.md.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, timedelta

from app.config import load_settings
from app.db import SchemaMismatch, connect_live
from app.metrics.bandwidth_full import DOMAINS, FULL_GREEN, FULL_RED, WEIGHTS

LAMP_STOPS = (100 / 3, 200 / 3)
FULL_STOPS = (FULL_RED, FULL_GREEN)


def band(score: float | None, low: bool, stops: tuple[float, float]) -> str:
    """A score's colour, named: red at the cue, gold round 50, green from the upper stop,
    orange and lime for the halves of the two gradients nearer their ends."""
    if score is None:
        return "none"
    red, green = stops
    if low or score <= red:
        return "red"
    if score >= green - 0.005:
        return "green"
    if score < 50 - (50 - red) * 0.3:
        return "orange"
    if score <= 50 + (green - 50) * 0.3:
        return "gold"
    return "lime"


def _row(conn: sqlite3.Connection, day: str) -> dict:
    row = conn.execute(
        "SELECT metrics_json FROM daily_metrics WHERE day_local = ?", (day,)
    ).fetchone()
    return json.loads(row["metrics_json"]) if row else {}


def _fmt(score: float | None) -> str:
    return "    -" if score is None else f"{score:5.1f}"


def day_line(day: str, row: dict) -> str:
    lamp, full = row.get("bandwidth"), row.get("bandwidth_full")
    trace = row.get("bandwidth_full_trace") or {}
    present = {name: term for name, term in trace.items() if name in WEIGHTS and term}
    movers = sorted(present.items(), key=lambda item: -abs(item[1]["points"]))[:3]
    told = ", ".join(f"{name} {term['points']:+.1f}" for name, term in movers)
    return (
        f"{day}  lamp {_fmt(lamp)} {band(lamp, row.get('bandwidth_low') is True, LAMP_STOPS):6}"
        f"  full {_fmt(full)} {band(full, row.get('bandwidth_full_low') is True, FULL_STOPS):6}"
        f"  {trace.get('weight_present') or 0:3}/100  {told}"
    )


def trace_lines(day: str, row: dict) -> list[str]:
    trace = row.get("bandwidth_full_trace") or {}
    out = [day_line(day, row), ""]
    for domain, names in DOMAINS.items():
        out.append(domain)
        for name in names:
            term = trace.get(name)
            if not term:
                out.append(f"  {name:14} weight {WEIGHTS[name]:2}  absent")
                continue
            against = (
                ""
                if term["reference"] is None
                else f" against {term['reference']} (scale {term['scale']})"
            )
            out.append(
                f"  {name:14} weight {term['weight']:2}  z {term['z']:+.2f}"
                f"  points {term['points']:+6.2f}  x {term['x']}{against}"
            )
    total = 50 + sum(trace[name]["points"] for name in WEIGHTS if trace.get(name))
    out += [
        "",
        f"50 + the points = {total:.2f}; stored {row.get('bandwidth_full')}",
        f"cue at or under {FULL_RED:.2f} with {trace.get('weight_present')} of 100 present; "
        f"green from {FULL_GREEN:.2f}",
    ]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools.bandwidth", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--date", metavar="DATE")
    args = parser.parse_args(argv)
    settings = load_settings()
    try:
        conn = connect_live(settings.storage.db_path)
    except SchemaMismatch as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    try:
        if args.date:
            day = date.fromisoformat(args.date).isoformat()
            print("\n".join(trace_lines(day, _row(conn, day))))
            return 0
        last = conn.execute("SELECT MAX(day_local) AS d FROM daily_metrics").fetchone()["d"]
        if last is None:
            print("no days stored")
            return 0
        end = date.fromisoformat(last)
        for back in range(max(args.days, 1) - 1, -1, -1):
            day = (end - timedelta(days=back)).isoformat()
            print(day_line(day, _row(conn, day)))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
