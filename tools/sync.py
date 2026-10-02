"""Sync a source now.

python -m tools.sync --source claude_usage   read the status-line hook's file into daily_metrics
"""

from __future__ import annotations

import argparse
import sys

from app.config import load_settings
from app.db import SchemaMismatch, connect_live
from app.ingest.claude_usage import read_usage_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools.sync", description=__doc__)
    parser.add_argument("--source", required=True, choices=["claude_usage"])
    parser.parse_args(argv)
    settings = load_settings()
    try:
        conn = connect_live(settings.storage.db_path)
    except SchemaMismatch as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    try:
        result = read_usage_file(conn, settings)
    finally:
        conn.close()
    line = f"claude_usage {result.status}"
    if result.reading is not None:
        reading = result.reading
        line += (
            f" used_pct={reading.used_pct} resets_at={reading.resets_at_utc}"
            f" captured_at={reading.captured_at_utc}"
        )
    print(line)
    return 1 if result.status == "malformed" else 0


if __name__ == "__main__":
    sys.exit(main())
