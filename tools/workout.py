"""Credit a day with a quality workout by hand (notes.txt § Architecture assumptions,
Manual workout override).

python -m tools.workout --note "4 mile run"               credit today (home timezone)
python -m tools.workout --date YYYY-MM-DD --note "..."    credit that day; a second add replaces
                                                          the note
python -m tools.workout --date YYYY-MM-DD --remove        take the override away again
python -m tools.workout --list                            every override, then every judged
                                                          day with verdict and reason
python -m tools.workout --clean-reasons                   re-strip stray braces and quotes
                                                          from stored judged reasons

Every add or remove recomputes the metrics rows at once, the way the scheduler does, and
prints the day's and its week's rows. Uses the live db without migrating it (exit 2 if the
schema is behind: run tools.migrate).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from app.config import Settings, load_settings
from app.db import SchemaMismatch, connect_live
from app.metrics import judge, manual
from app.metrics.calendar import week_start
from app.metrics.engine import recompute
from app.timeutil import local_day, now_utc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools.workout", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--date", metavar="YYYY-MM-DD", help="home-timezone day; default today")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--note", default="", help="what the workout was; stored with the day")
    group.add_argument("--remove", action="store_true")
    group.add_argument("--list", action="store_true")
    group.add_argument("--clean-reasons", action="store_true")
    args = parser.parse_args(argv)
    settings = load_settings()
    try:
        conn = connect_live(settings.storage.db_path)
    except SchemaMismatch as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    try:
        if args.list:
            return list_overrides(conn)
        if args.clean_reasons:
            print(f"cleaned {judge.clean_stored_reasons(conn)} judged reason(s)")
            return 0
        try:
            day = manual.parse_day(args.date or local_day(now_utc(), settings.home_tz), settings)
        except ValueError as exc:
            print(f"refusing: {exc}", file=sys.stderr)
            return 2
        if args.remove:
            if not manual.remove_override(conn, day):
                print(f"{day}: no override to remove", file=sys.stderr)
                return 1
            print(f"removed the override for {day}")
        else:
            row = manual.add_override(conn, day, args.note)
            print(f"credited {day} with a quality workout by hand: {row.note or '(no note)'}")
        recompute(conn, settings)
        show(conn, settings, day)
        return 0
    finally:
        conn.close()


def list_overrides(conn) -> int:
    rows = manual.list_overrides(conn)
    if not rows:
        print("no manual workout overrides")
    for row in rows:
        print(f"{row.day_local}  {row.note or '(no note)'}  recorded {row.created_at_utc}")
    for row in judge.list_judgements(conn):
        print(
            f"{row.day_local}  judged {row.verdict} ({row.confidence:.2f})  "
            f"{row.reason or row.error}"
            f"  judged {row.created_at_utc}"
        )
    return 0


def show(conn, settings: Settings, day: str) -> None:
    monday = week_start(date.fromisoformat(day)).isoformat()
    for table, column, key in (
        ("daily_metrics", "day_local", day),
        ("weekly_metrics", "week_start_local", monday),
    ):
        row = conn.execute(
            f'SELECT metrics_json FROM "{table}" WHERE "{column}" = ?', (key,)
        ).fetchone()
        body = json.loads(row["metrics_json"]) if row else None
        print(f"{table} {key}: {json.dumps(body, sort_keys=True)}")


if __name__ == "__main__":
    sys.exit(main())
