"""Replay archived raw payloads (health, claude_usage) through the product's own parsers.

python -m tools.replay --verify             replay into the scratch db and diff it against LIVE;
                                            exit 1 on any difference (the idempotency proof)
python -m tools.replay --since YYYY-MM-DD   re-parse data/raw into data/replay/scratch.db
python -m tools.replay --snapshot PATH      dump the live data tables to JSON
python -m tools.replay --diff PATH          compare live tables to a snapshot; exit 1 on difference

--snapshot and --diff compare live with live across a code change; they never look at the
scratch db. --verify is the one that tests the parsers against what is stored.

Replay order is raw_archive.id order (the order payloads were applied) whenever a db with
raw_archive rows is available. Only payloads that were parsed are replayed. Files on disk
with no row are reported as unrecorded and never applied. With no db at all (rebuilding from
the archive alone) every file is replayed in filename order, which is approximate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.config import REPO_ROOT, Settings, load_settings
from app.db import open_db
from app.ingest import claude_usage, health
from app.ingest.health import archive_raw, received_at_from_filename
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
INGESTERS = {
    health.SOURCE: health.ingest_archived,
    claude_usage.SOURCE: claude_usage.ingest_archived,
}


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


@dataclass
class ReplayPlan:
    """What a replay will apply, in order, and what it deliberately leaves out."""

    entries: list[tuple[str, Path, datetime]]
    ordered_by: str
    unrecorded: list[str] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def notes(self) -> list[str]:
        lines: list[str] = []
        if self.unrecorded:
            lines.append(
                f"unrecorded: {len(self.unrecorded)} unrecorded file(s) on disk with no "
                f"raw_archive row, not replayed: {', '.join(self.unrecorded)}"
            )
        if self.unparsed:
            lines.append(
                f"unparsed: {len(self.unparsed)} recorded payload(s) never parsed, "
                f"not replayed: {', '.join(self.unparsed)}"
            )
        return lines


def _on_or_after(moment: datetime, since: str | None) -> bool:
    return since is None or moment.date() >= datetime.strptime(since, "%Y-%m-%d").date()


def replay_plan(
    raw_dir: Path, recorded: sqlite3.Connection | None, since: str | None = None
) -> ReplayPlan:
    """The one definition of the replay set, shared by tools.replay and tools.backup.

    With raw_archive rows: parsed rows in id order. Without: every file, filename order.
    """
    on_disk = {
        f"{source}/{path.name}": path
        for source in INGESTERS
        for path in sorted((raw_dir / source).glob("*.json"))
    }
    rows = []
    if recorded is not None:
        rows = recorded.execute(
            "SELECT source, path, received_at_utc, parsed_ok FROM raw_archive ORDER BY id"
        ).fetchall()
    if not rows:
        ordered = sorted(on_disk.items(), key=lambda item: (item[1].name, item[0]))
        entries = [
            (rel.split("/", 1)[0], path, from_utc_iso(received_at_from_filename(path.name)))
            for rel, path in ordered
        ]
        return ReplayPlan([e for e in entries if _on_or_after(e[2], since)], "filename")
    plan = ReplayPlan([], "raw_archive.id")
    known: set[str] = set()
    for source, recorded_path, received_at_utc, parsed_ok in rows:
        rel = f"{source}/{Path(recorded_path).name}"
        known.add(rel)
        if source not in INGESTERS:
            continue
        if not parsed_ok:
            plan.unparsed.append(rel)
        elif rel not in on_disk:
            plan.missing.append(rel)
        elif _on_or_after(from_utc_iso(received_at_utc), since):
            plan.entries.append((source, on_disk[rel], from_utc_iso(received_at_utc)))
    plan.unrecorded = sorted(set(on_disk) - known)
    return plan


def replay(
    raw_dir: Path,
    scratch_db: Path,
    settings: Settings,
    since: str | None = None,
    recorded: sqlite3.Connection | None = None,
    plan: ReplayPlan | None = None,
) -> sqlite3.Connection:
    """Re-ingest the replay set into a fresh scratch database.

    `recorded` is a connection to the db whose raw_archive says what was applied and in
    what order (the live db, or a restored backup). Without it the order is by filename.
    """
    for suffix in ("", "-wal", "-shm"):
        Path(str(scratch_db) + suffix).unlink(missing_ok=True)
    plan = plan or replay_plan(raw_dir, recorded, since)
    if plan.ordered_by == "filename" and plan.entries:
        print(
            "warning: no raw_archive rows to order by; replaying every file in filename order, "
            "which is approximate (same-second arrivals and clock changes can reorder)",
            file=sys.stderr,
        )
    conn = open_db(scratch_db)
    for source, path, received in plan.entries:
        body = path.read_bytes()
        archived = archive_raw(
            conn, raw_dir, body, source=source, received_at=received, write_file=False, path=path
        )
        if archived.duplicate:
            continue
        try:
            INGESTERS[source](conn, body, archived.raw_archive_id, settings, raw_dir)
        except Exception as exc:
            print(f"skip {path.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
    return conn


def verify(settings: Settings, scratch_db: Path) -> tuple[list[str], list[str]]:
    """Replay the archive from scratch and compare it with the live data tables.

    Returns (differences, notes). `+` rows are in live only, `-` rows in the replay only.
    """
    live = open_db(settings.storage.db_path)
    try:
        plan = replay_plan(settings.storage.raw_dir, live)
        scratch = replay(settings.storage.raw_dir, scratch_db, settings, plan=plan)
        try:
            lines = diff(snapshot(live), snapshot(scratch))
        finally:
            scratch.close()
    finally:
        live.close()
    lines.extend(f"missing raw file: {rel}" for rel in plan.missing)
    return lines, plan.notes()


def report(conn: sqlite3.Connection) -> str:
    snap = snapshot(conn)
    lines = [f"{table:20s} {len(rows):6d}" for table, rows in snap.items()]
    lines.append(f"checksum {checksum(snap)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools.replay", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--verify", action="store_true")
    group.add_argument("--since", metavar="YYYY-MM-DD")
    group.add_argument("--snapshot", metavar="PATH")
    group.add_argument("--diff", metavar="PATH")
    parser.add_argument("--scratch", default=str(SCRATCH_DB))
    args = parser.parse_args(argv)
    settings = load_settings()
    live_exists = settings.storage.db_path.is_file()

    if args.verify:
        if not live_exists:
            print(f"no live database at {settings.storage.db_path}", file=sys.stderr)
            return 2
        lines, notes = verify(settings, Path(args.scratch))
        for line in lines:
            print(line)
        for note in notes:
            print(f"note: {note}")
        print(f"{len(lines)} difference(s)")
        return 1 if lines else 0

    if args.since:
        recorded = open_db(settings.storage.db_path) if live_exists else None
        try:
            plan = replay_plan(settings.storage.raw_dir, recorded, args.since)
        finally:
            if recorded is not None:
                recorded.close()
        conn = replay(settings.storage.raw_dir, Path(args.scratch), settings, plan=plan)
        for note in [*plan.notes(), *(f"missing raw file: {rel}" for rel in plan.missing)]:
            print(f"note: {note}", file=sys.stderr)
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
