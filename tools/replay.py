"""Replay archived raw payloads (health, claude_usage) through the product's own parsers.

python -m tools.replay --since YYYY-MM-DD   re-parse data/raw into data/replay/scratch.db
python -m tools.replay --snapshot PATH      dump the live data tables to JSON
python -m tools.replay --diff PATH          compare live tables to a snapshot; exit 1 on difference
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from app.config import REPO_ROOT, Settings, load_settings
from app.db import open_db
from app.ingest import claude_usage, health
from app.ingest.health import SOURCE, archive_raw, received_at_from_filename
from app.timeutil import from_utc_iso

DATA_TABLES = (
    "activities",
    "activity_sources",
    "workout_hr_samples",
    "sleep_sessions",
    "steps_daily",
    "wellness_daily",
    "daily_metrics",
    "weekly_metrics",
    "books",
)
PROVENANCE_COLUMNS = {"activity_sources": {"raw_archive_id"}}
SCRATCH_DB = REPO_ROOT / "data" / "replay" / "scratch.db"
INGESTERS = {SOURCE: health.ingest_archived, claude_usage.SOURCE: claude_usage.ingest_archived}


def _primary_key(conn: sqlite3.Connection, table: str) -> list[str]:
    cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    keyed = sorted((c["pk"], c["name"]) for c in cols if c["pk"])
    return [name for _, name in keyed] or [c["name"] for c in cols]


def dump_table(conn: sqlite3.Connection, table: str) -> list[dict[str, object]]:
    order = ", ".join(f'"{c}"' for c in _primary_key(conn, table))
    rows = conn.execute(f'SELECT * FROM "{table}" ORDER BY {order}').fetchall()
    skip = PROVENANCE_COLUMNS.get(table, set())
    return [{k: v for k, v in dict(r).items() if k not in skip} for r in rows]


def snapshot(conn: sqlite3.Connection) -> dict[str, list[dict[str, object]]]:
    return {table: dump_table(conn, table) for table in DATA_TABLES}


def checksum(snap: dict[str, list[dict[str, object]]]) -> str:
    encoded = json.dumps(snap, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def diff(
    live: dict[str, list[dict[str, object]]], saved: dict[str, list[dict[str, object]]]
) -> list[str]:
    lines: list[str] = []
    for table in sorted(set(live) | set(saved)):
        a, b = live.get(table, []), saved.get(table, [])
        if a == b:
            continue
        a_set = {json.dumps(r, sort_keys=True) for r in a}
        b_set = {json.dumps(r, sort_keys=True) for r in b}
        for row in sorted(b_set - a_set):
            lines.append(f"{table}: - {row}")
        for row in sorted(a_set - b_set):
            lines.append(f"{table}: + {row}")
    return lines


def archived_files(raw_dir: Path, since: str | None, source: str = SOURCE) -> list[Path]:
    files = sorted((raw_dir / source).glob("*.json"))
    if since is None:
        return files
    floor = datetime.strptime(since, "%Y-%m-%d").date()
    return [f for f in files if from_utc_iso(received_at_from_filename(f.name)).date() >= floor]


def replay(
    raw_dir: Path, scratch_db: Path, settings: Settings, since: str | None = None
) -> sqlite3.Connection:
    """Re-ingest archived files, in filename order, into a fresh scratch database."""
    for suffix in ("", "-wal", "-shm"):
        Path(str(scratch_db) + suffix).unlink(missing_ok=True)
    conn = open_db(scratch_db)
    for source, ingest in INGESTERS.items():
        for path in archived_files(raw_dir, since, source):
            body = path.read_bytes()
            received = from_utc_iso(received_at_from_filename(path.name))
            archived = archive_raw(
                conn,
                raw_dir,
                body,
                source=source,
                received_at=received,
                write_file=False,
                path=path,
            )
            if archived.duplicate:
                continue
            try:
                ingest(conn, body, archived.raw_archive_id, settings)
            except Exception as exc:
                print(f"skip {path.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
    return conn


def report(conn: sqlite3.Connection) -> str:
    snap = snapshot(conn)
    lines = [f"{table:20s} {len(rows):6d}" for table, rows in snap.items()]
    lines.append(f"checksum {checksum(snap)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools.replay", description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--since", metavar="YYYY-MM-DD")
    group.add_argument("--snapshot", metavar="PATH")
    group.add_argument("--diff", metavar="PATH")
    parser.add_argument("--scratch", default=str(SCRATCH_DB))
    args = parser.parse_args(argv)
    settings = load_settings()

    if args.since:
        conn = replay(settings.storage.raw_dir, Path(args.scratch), settings, args.since)
        print(report(conn))
        conn.close()
        return 0

    live = open_db(settings.storage.db_path)
    try:
        if args.snapshot:
            target = Path(args.snapshot)
            target.parent.mkdir(parents=True, exist_ok=True)
            snap = snapshot(live)
            target.write_text(json.dumps(snap, indent=1, sort_keys=True))
            print(f"snapshot {target} checksum {checksum(snap)}")
            return 0
        saved = json.loads(Path(args.diff).read_text())
        lines = diff(snapshot(live), saved)
        for line in lines:
            print(line)
        print(f"{len(lines)} difference(s)")
        return 1 if lines else 0
    finally:
        live.close()


if __name__ == "__main__":
    sys.exit(main())
