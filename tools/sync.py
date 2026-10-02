"""Sync a source now.

python -m tools.sync --source claude_usage   read the status-line hook's file into daily_metrics
python -m tools.sync --source goodreads      fetch the `read` shelf feed into books
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

import httpx

from app.config import Settings, load_settings
from app.db import SchemaMismatch, connect_live
from app.ingest.claude_usage import read_usage_file
from app.ingest.goodreads import FeedError, poll_goodreads

SOURCES = ("claude_usage", "goodreads")
UNCONFIGURED = "goodreads unconfigured: GOODREADS_RSS_URL is not set in .env; nothing was fetched"


def sync_claude_usage(conn: sqlite3.Connection, settings: Settings) -> int:
    result = read_usage_file(conn, settings)
    line = f"claude_usage {result.status}"
    if result.reading is not None:
        reading = result.reading
        line += (
            f" used_pct={reading.used_pct} resets_at={reading.resets_at_utc}"
            f" captured_at={reading.captured_at_utc}"
        )
    print(line)
    return 1 if result.status == "malformed" else 0


def sync_goodreads(conn: sqlite3.Connection, settings: Settings) -> int:
    try:
        result = poll_goodreads(conn, settings)
    except (FeedError, httpx.HTTPError) as exc:
        print(f"goodreads fetch failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    line = f"goodreads {result.status}"
    if result.raw_archive_id is not None:
        line += f" raw_archive_id={result.raw_archive_id}"
    if result.status == "ok":
        line += f" books={result.books}"
    print(line)
    return 1 if result.status == "malformed" else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools.sync", description=__doc__)
    parser.add_argument("--source", required=True, choices=SOURCES)
    args = parser.parse_args(argv)
    settings = load_settings()
    if args.source == "goodreads" and not settings.goodreads_rss_url:
        print(UNCONFIGURED, file=sys.stderr)
        return 2
    try:
        conn = connect_live(settings.storage.db_path)
    except SchemaMismatch as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    try:
        if args.source == "goodreads":
            return sync_goodreads(conn, settings)
        return sync_claude_usage(conn, settings)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
