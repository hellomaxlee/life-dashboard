"""Run the metrics engine by hand and look at what it wrote.

python -m tools.metrics --recompute                 judge, then recompute every row through today
python -m tools.metrics --recompute --today DATE    ... as of the end of that home-timezone day
python -m tools.metrics --show DATE                 print the day's and its week's rows as JSON
python -m tools.metrics --history                   print the load-bar history

--recompute goes through app.metrics.job.run_recompute, the scheduler's own tick: the
judged workout runs first, only when `[judge] model` is set and a key is present, and the
line printed says how many days were judged, failed and skipped (unchanged hash).

Uses the live db without migrating it (exit 2 if the schema is behind: run tools.migrate).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from app.config import load_settings
from app.db import SchemaMismatch, connect_live
from app.metrics.calendar import week_start
from app.metrics.job import run_recompute


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools.metrics", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--recompute", action="store_true")
    group.add_argument("--show", metavar="DATE")
    group.add_argument("--history", action="store_true")
    parser.add_argument("--today", metavar="DATE", help="with --recompute: the day to compute to")
    args = parser.parse_args(argv)
    settings = load_settings()
    try:
        conn = connect_live(settings.storage.db_path)
    except SchemaMismatch as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    try:
        if args.recompute:
            conn.close()
            result = run_recompute(
                settings, lambda: connect_live(settings.storage.db_path), today_local=args.today
            )
            print(f"judge: {result.judge.describe()}")
            print(
                f"recomputed {result.first_day}..{result.today_local} as of {result.now_utc}: "
                f"{result.days_written} day row(s), {result.weeks_written} week row(s), "
                f"{result.calibrations_added} calibration(s) written"
            )
            return 0
        if args.history:
            rows = conn.execute("SELECT * FROM load_bar_history ORDER BY effective_from_week")
            for row in rows:
                print(json.dumps(dict(row), sort_keys=True))
            print(f"placeholder {settings.workout.load_bar}")
            return 0
        day = date.fromisoformat(args.show).isoformat()
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
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
