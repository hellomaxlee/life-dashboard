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
    """Readers see the old file or the new one, never part of either."""
    target.parent.mkdir(parents=True, exist_ok=True)
    scratch = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    try:
        scratch.write_text(json.dumps(record, indent=1, allow_nan=False) + "\n")
        os.replace(scratch, target)
    finally:
        scratch.unlink(missing_ok=True)


def _reject_constant(name: str) -> float:
    raise ValueError(name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools.claude_usage_hook", add_help=False)
    parser.add_argument("--out", metavar="PATH")
    try:
        args, _ = parser.parse_known_args(argv)
        stream = getattr(sys.stdin, "buffer", sys.stdin)
        feed = json.loads(stream.read(), parse_constant=_reject_constant)
        record = usage_record(feed, datetime.now(UTC))
        if record is not None:
            write_atomic(Path(args.out) if args.out else configured_path(), record)
    except (Exception, SystemExit):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
