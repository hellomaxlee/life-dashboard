"""Write, inspect, or price the daily summary.

python -m tools.summary --date YYYY-MM-DD            write the line shown on that day (it
                                                     describes the day before; no call if stored)
python -m tools.summary --date YYYY-MM-DD --force    regenerate, calling the model again
python -m tools.summary --date YYYY-MM-DD --dry-run  print the exact request body and the cap
                                                     check; nothing is called or written
python -m tools.summary --spend                      month-to-date usd and the cap
"""

from __future__ import annotations

import argparse
import json
import sys

from app.config import load_settings
from app.db import SchemaMismatch, connect_live
from app.summary import memory, spend
from app.summary.payload import build_payload
from app.summary.run import build_request, write_summary
from app.timeutil import local_day, now_utc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools.summary", description=__doc__)
    parser.add_argument("--date", help="home-timezone day, YYYY-MM-DD; default today")
    parser.add_argument("--force", action="store_true", help="regenerate a stored line")
    parser.add_argument("--dry-run", action="store_true", help="print the request, call nothing")
    parser.add_argument("--spend", action="store_true", help="print month-to-date spend")
    args = parser.parse_args(argv)
    settings = load_settings()
    try:
        conn = connect_live(settings.storage.db_path)
    except SchemaMismatch as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    try:
        if args.spend:
            print(spend.readback(conn, settings))
            return 0
        day = args.date or local_day(now_utc(), settings.home_tz)
        if args.dry_run:
            return dry_run(conn, settings, day)
        result = write_summary(conn, settings, day, force=args.force)
    finally:
        conn.close()
    print(f"{result.day_local} [{result.source}] {result.line}")
    if result.web_line:
        print(f"  web: {result.web_line}")
    print(
        f"  gate: {result.gate_result}; model called: {result.called}; usd {result.spend_usd:.4f}"
    )
    for attempt in result.attempts:
        print(f"  attempt {attempt.get('source')}: {attempt.get('result')}")
    return 0


def dry_run(conn, settings, day: str) -> int:
    payload = build_payload(conn, settings, day)
    recent = memory.recent_before(conn, day)
    request = build_request(payload, recent, settings, None)
    print(json.dumps(request, indent=1, sort_keys=True))
    print(spend.cap_check(conn, settings, request).describe())
    key = "present" if settings.anthropic_api_key else "absent (model unavailable, fallback runs)"
    print(
        f"api key: {key}; shown on {day}, describes {payload.data['describes']}; "
        f"cell: {payload.cell.name}; lens: {payload.lens}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
