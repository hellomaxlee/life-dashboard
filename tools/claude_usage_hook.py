"""Claude Code status-line hook: keep the subscription rate-limit windows for the usage bar.

Claude Code pipes its status-line JSON to stdin. This writes
{"captured_at_utc": ..., "rate_limits": {...}} to `claude_usage.path` from config.toml
(issue #2) and nothing else from the feed. A feed without `rate_limits` leaves the last
reading in place. Stdlib only, silent, and always exit 0: it runs on every status-line
refresh and must never break the line.

python tools/claude_usage_hook.py [--out PATH] < statusline.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
UTC_ISO = "%Y-%m-%dT%H:%M:%SZ"


def configured_path() -> Path:
    with (REPO_ROOT / "config.toml").open("rb") as fh:
        path = Path(tomllib.load(fh)["claude_usage"]["path"])
    return path if path.is_absolute() else REPO_ROOT / path


def usage_record(feed: object, captured_at: datetime) -> dict[str, object] | None:
    """The record to save for one status-line feed, or None when it carries no rate limits."""
    if not isinstance(feed, dict):
        return None
    rate_limits = feed.get("rate_limits")
    if not isinstance(rate_limits, dict) or not rate_limits:
        return None
    return {"captured_at_utc": captured_at.strftime(UTC_ISO), "rate_limits": rate_limits}


def write_atomic(target: Path, record: dict[str, object]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    scratch = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    scratch.write_text(json.dumps(record, indent=1) + "\n")
    os.replace(scratch, target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools.claude_usage_hook")
    parser.add_argument("--out", metavar="PATH")
    args = parser.parse_args(argv)
    try:
        record = usage_record(json.loads(sys.stdin.read()), datetime.now(UTC))
        if record is not None:
            write_atomic(Path(args.out) if args.out else configured_path(), record)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
