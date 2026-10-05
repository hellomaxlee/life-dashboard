"""Author, inspect, or price the month feature.

python -m tools.month                      this month's feature; one model run if none is stored
python -m tools.month --month YYYY-MM      the same for another month
python -m tools.month --force              author it again (skips the attempt guard, never the cap)
python -m tools.month --show               print the stored feature; nothing is called
python -m tools.month --dry-run            print the request's size, its worst-case cost and the
                                           cap check; nothing is called or written
"""

from __future__ import annotations

import argparse
import sys

from app.config import load_settings
from app.db import SchemaMismatch, connect_live
from app.month import store
from app.month.generate import MAX_CALLS, build_request, ensure_month_feature, guard_reason
from app.month.spec import MONTH, MonthFeature
from app.summary import spend
from app.timeutil import local_day, now_utc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools.month", description=__doc__)
    parser.add_argument("--month", help="home-timezone month, YYYY-MM; default this month")
    parser.add_argument("--force", action="store_true", help="author a stored month again")
    parser.add_argument("--show", action="store_true", help="print the stored feature only")
    parser.add_argument("--dry-run", action="store_true", help="price the request, call nothing")
    args = parser.parse_args(argv)
    settings = load_settings()
    today = local_day(now_utc(), settings.home_tz)
    month = args.month or today[:7]
    if not MONTH.match(month):
        print(f"refusing: --month {month!r} is not YYYY-MM", file=sys.stderr)
        return 2
    try:
        conn = connect_live(settings.storage.db_path)
    except SchemaMismatch as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    try:
        if args.show:
            return show(conn, month)
        if args.dry_run:
            return dry_run(conn, settings, month, today)
        result = ensure_month_feature(conn, settings, month, force=args.force)
        if result.feature is not None:
            source = store.feature_source(conn, month)
            print_header(result.feature, source)
        print(
            f"{month} [{result.status}] model calls: {result.calls}; usd {result.spend_usd:.4f}"
            + (f"; {result.reason}" if result.reason else "")
        )
        for rejection in result.rejections:
            print(f"  rejected: {rejection}")
        print(f"spend: {spend.readback(conn, settings)}")
        return 0 if result.ok else 1
    finally:
        conn.close()


def print_header(feature: MonthFeature, source: tuple[str, str | None] | None) -> None:
    print(f"{feature.month}  {feature.title.upper()}")
    print(f"  theme: {feature.theme}")
    if source is not None:
        print(f"  source: {source[0]} ({source[1] or 'no model'})")


def show(conn, month: str) -> int:
    feature = store.load_feature(conn, month)
    if feature is None:
        print(f"{month}: no stored feature (the panel shows its calendar screen)")
        for row in store.attempts(conn, month):
            print(f"  failed run {row['day_local']}: {row['calls']} call(s): {row['reason']}")
        return 1
    print_header(feature, store.feature_source(conn, month))
    colours = ("#{:02x}{:02x}{:02x}".format(*colour) for colour in feature.palette)
    print("  palette: " + " ".join(colours))
    for plate in feature.days:
        print(f"  {plate.day:2d}  {plate.caption.upper():<15}  {plate.note}")
    return 0


def dry_run(conn, settings, month: str, today: str) -> int:
    request = build_request(conn, settings, month)
    system_chars = sum(len(block["text"]) for block in request["system"])
    user_chars = sum(len(message["content"]) for message in request["messages"])
    worst = spend.worst_case_usd(settings, request)
    print(request["messages"][0]["content"])
    print(
        f"request: system {system_chars} chars, user {user_chars} chars, about "
        f"{spend.estimate_input_tokens(request)} input tokens (counted high); "
        f"max_tokens {request['max_tokens']}; model {request['model']}"
    )
    print(
        f"worst case: ${worst:.4f} a call, ${worst * MAX_CALLS:.4f} for a run of "
        f"{MAX_CALLS} calls (one retry after a rejection)"
    )
    print(spend.cap_check(conn, settings, request).describe())
    stored = "stored" if store.load_feature(conn, month) is not None else "not stored"
    guard = guard_reason(conn, month, today) or "an automatic run may call"
    key = "present" if settings.anthropic_api_key else "absent (no call is possible)"
    print(f"{month}: {stored}; guard: {guard}; api key: {key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
