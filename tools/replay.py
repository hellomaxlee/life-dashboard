"""Replay archived raw payloads (health, claude_usage, goodreads) through the product's own
parsers.

python -m tools.replay --verify             replay into the scratch db and diff it against LIVE;
                                            exit 1 on any difference (the idempotency proof)
python -m tools.replay --since YYYY-MM-DD   re-parse data/raw into data/replay/scratch.db
python -m tools.replay --snapshot PATH      dump the live data tables to JSON
python -m tools.replay --diff PATH          compare live tables to a snapshot; exit 1 on difference
python -m tools.replay --rebuild-live [--drop-ids 1,2,3]
                                            replace the LIVE data tables with a from-scratch
                                            replay of the archive (stop the service and back up
                                            first; see workflows/run-service.md section 16)

--rebuild-live is for the day the parsers changed and live still holds what the old ones
stored. --drop-ids names raw_archive ids to leave out for good (test pushes): their rows are
removed and their files moved to data/raw/_dropped/, never deleted. The summary's keys are
kept; the metrics engine's rows are recomputed under the clock.

--snapshot and --diff compare live with live across a code change; they never look at the
scratch db. --verify is the one that tests the parsers against what is stored.

The metrics engine's keys in daily_metrics / weekly_metrics and the load_bar_history table
are derived, not ingested. --verify recomputes them in the scratch db under the clock of
live's last recompute (`metrics_state`) and compares them too; when live holds a payload
newer than that recompute, or the engine never ran, they are left out and a note says so.
--snapshot and --diff always include them.

The summary's keys (`summary_device_line`, `summary_web_line`, `summary_source`) are authored
output, not a function of the raw archive (the model's words differ run to run): --verify
never compares them. --snapshot and --diff, which compare live with live, keep them.

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
from app.db import SchemaMismatch, connect_live, open_db
from app.ingest import claude_usage, goodreads, health
from app.ingest.health import archive_raw, raw_suffix, received_at_from_filename
from app.metrics.engine import last_run, recompute
from app.metrics.keys import DAILY_KEYS, WEEKLY_KEYS
from app.summary.run import SUMMARY_KEYS
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
DERIVED_TABLES = ("load_bar_history",)
DERIVED_KEYS = {"daily_metrics": DAILY_KEYS, "weekly_metrics": WEEKLY_KEYS}
AUTHORED_KEYS = {"daily_metrics": SUMMARY_KEYS}
PROVENANCE_COLUMNS = {"activity_sources": {"raw_archive_id"}}
SCRATCH_DB = REPO_ROOT / "data" / "replay" / "scratch.db"
INGESTERS = {
    health.SOURCE: health.ingest_archived,
    claude_usage.SOURCE: claude_usage.ingest_archived,
    goodreads.SOURCE: goodreads.ingest_archived,
}


def _primary_key(conn: sqlite3.Connection, table: str) -> list[str]:
    cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    keyed = sorted((c["pk"], c["name"]) for c in cols if c["pk"])
    return [name for _, name in keyed] or [c["name"] for c in cols]


def _without_derived(row: dict[str, object], keys: frozenset[str]) -> dict[str, object] | None:
    """The row with the engine's keys removed; None when nothing else is in it."""
    loaded = json.loads(str(row["metrics_json"]))
    kept = (
        {k: v for k, v in loaded.items() if k not in keys} if isinstance(loaded, dict) else loaded
    )
    if not kept:
        return None
    return {**row, "metrics_json": json.dumps(kept, sort_keys=True)}


def dump_table(
    conn: sqlite3.Connection, table: str, derived: bool = True, authored: bool = True
) -> list[dict[str, object]]:
    order = ", ".join(f'"{c}"' for c in _primary_key(conn, table))
    rows = conn.execute(f'SELECT * FROM "{table}" ORDER BY {order}').fetchall()
    skip = PROVENANCE_COLUMNS.get(table, set())
    dumped = [{k: v for k, v in dict(r).items() if k not in skip} for r in rows]
    strip: frozenset[str] = frozenset()
    if not derived:
        strip |= DERIVED_KEYS.get(table, frozenset())
    if not authored:
        strip |= AUTHORED_KEYS.get(table, frozenset())
    if not strip:
        return dumped
    stripped = (_without_derived(row, strip) for row in dumped)
    return [row for row in stripped if row is not None]


def snapshot(
    conn: sqlite3.Connection, derived: bool = False, authored: bool = True
) -> dict[str, list[dict[str, object]]]:
    """The data tables. With `derived` the metrics engine's keys and tables are included;
    without `authored` the summary's keys are left out."""
    tables = DATA_TABLES + DERIVED_TABLES if derived else DATA_TABLES
    return {table: dump_table(conn, table, derived, authored) for table in tables}


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
    altered: list[str] = field(default_factory=list)

    def refused(self) -> list[str]:
        """Parsed payloads that cannot be replayed; each is a difference."""
        return [f"missing raw file: {rel}" for rel in self.missing] + [
            f"altered raw file: {rel} (does not match its recorded sha256)" for rel in self.altered
        ]

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

    With raw_archive rows: parsed rows in id order, each file checked against its
    recorded sha256 (a missing or altered file is refused, not replayed). Without:
    every file, filename order.
    """
    on_disk = {
        f"{source}/{path.name}": path
        for source in INGESTERS
        for path in sorted((raw_dir / source).glob(f"*{raw_suffix(source)}"))
    }
    rows = []
    if recorded is not None:
        rows = recorded.execute(
            "SELECT source, path, received_at_utc, parsed_ok, sha256 FROM raw_archive ORDER BY id"
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
    for source, recorded_path, received_at_utc, parsed_ok, sha256 in rows:
        rel = f"{source}/{Path(recorded_path).name}"
        known.add(rel)
        if source not in INGESTERS:
            continue
        if not parsed_ok:
            plan.unparsed.append(rel)
        elif rel not in on_disk:
            plan.missing.append(rel)
        elif hashlib.sha256(on_disk[rel].read_bytes()).hexdigest() != sha256:
            plan.altered.append(rel)
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
    live = connect_live(settings.storage.db_path)
    try:
        plan = replay_plan(settings.storage.raw_dir, live)
        clock, why_not = metrics_clock(live)
        scratch = replay(settings.storage.raw_dir, scratch_db, settings, plan=plan)
        try:
            if clock is not None:
                recompute(scratch, settings, clock[0], from_utc_iso(clock[1]))
            lines = diff(
                snapshot(live, derived=clock is not None, authored=False),
                snapshot(scratch, derived=clock is not None, authored=False),
            )
        finally:
            scratch.close()
    finally:
        live.close()
    lines.extend(plan.refused())
    notes = plan.notes()
    if why_not:
        notes.append(f"metrics: derived rows not compared: {why_not}")
    return lines, notes


def _copy_rows(
    scratch: sqlite3.Connection, live: sqlite3.Connection, table: str, archive_ids: dict[int, int]
) -> int:
    rows = [dict(row) for row in scratch.execute(f'SELECT * FROM "{table}"')]
    for row in rows:
        if "raw_archive_id" in row and row["raw_archive_id"] is not None:
            row["raw_archive_id"] = archive_ids.get(row["raw_archive_id"])
        columns = ", ".join(f'"{name}"' for name in row)
        marks = ", ".join("?" for _ in row)
        live.execute(f'INSERT INTO "{table}" ({columns}) VALUES ({marks})', list(row.values()))
    return len(rows)


def _authored(live: sqlite3.Connection) -> dict[str, dict[str, object]]:
    """Each day's summary keys: authored output a replay cannot reproduce."""
    kept: dict[str, dict[str, object]] = {}
    for row in live.execute("SELECT day_local, metrics_json FROM daily_metrics"):
        try:
            loaded = json.loads(row["metrics_json"])
        except (TypeError, ValueError):
            continue
        if isinstance(loaded, dict):
            keys = {k: v for k, v in loaded.items() if k in SUMMARY_KEYS}
            if keys:
                kept[row["day_local"]] = keys
    return kept


def _restore_authored(live: sqlite3.Connection, authored: dict[str, dict[str, object]]) -> None:
    for day, keys in authored.items():
        row = live.execute(
            "SELECT metrics_json FROM daily_metrics WHERE day_local = ?", (day,)
        ).fetchone()
        metrics = json.loads(row["metrics_json"]) if row is not None else {}
        metrics.update(keys)
        live.execute(
            "INSERT INTO daily_metrics (day_local, metrics_json) VALUES (?, ?) "
            "ON CONFLICT (day_local) DO UPDATE SET metrics_json = excluded.metrics_json",
            (day, json.dumps(metrics, sort_keys=True)),
        )


def rebuild_live(
    settings: Settings, scratch_db: Path, drop_ids: tuple[int, ...] = ()
) -> tuple[dict[str, int], list[str]]:
    """Replace live's data tables with a from-scratch replay of its own archive.

    Returns (rows written per table, files moved to data/raw/_dropped). ValueError, with
    live untouched, when a drop id is unknown or a parsed payload cannot be replayed.
    """
    raw_dir = settings.storage.raw_dir
    live = connect_live(settings.storage.db_path)
    try:
        plan = replay_plan(raw_dir, live)
        if plan.refused():
            raise ValueError("; ".join(plan.refused()))
        dropped: dict[str, Path] = {}
        for raw_id in drop_ids:
            row = live.execute(
                "SELECT source, path FROM raw_archive WHERE id = ?", (raw_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"no raw_archive row with id {raw_id}")
            dropped[f"{row['source']}/{Path(row['path']).name}"] = (
                raw_dir / row["source"] / Path(row["path"]).name
            )
        gone = set(dropped.values())
        plan.entries = [entry for entry in plan.entries if entry[1] not in gone]
        scratch = replay(raw_dir, scratch_db, settings, plan=plan)
        try:
            by_sha = {row["sha256"]: row["id"] for row in live.execute("SELECT * FROM raw_archive")}
            archive_ids = {
                row["id"]: by_sha[row["sha256"]]
                for row in scratch.execute("SELECT id, sha256 FROM raw_archive")
            }
            authored = _authored(live)
            written: dict[str, int] = {}
            live.execute("BEGIN IMMEDIATE")
            try:
                for table in reversed(DATA_TABLES + DERIVED_TABLES):
                    live.execute(f'DELETE FROM "{table}"')
                live.execute("DELETE FROM metrics_state")
                for raw_id in drop_ids:
                    live.execute("DELETE FROM ingest_log WHERE raw_archive_id = ?", (raw_id,))
                    live.execute("DELETE FROM raw_archive WHERE id = ?", (raw_id,))
                for table in DATA_TABLES:
                    written[table] = _copy_rows(scratch, live, table, archive_ids)
                _restore_authored(live, authored)
                live.execute("COMMIT")
            except BaseException:
                live.execute("ROLLBACK")
                raise
        finally:
            scratch.close()
        moved = []
        for rel, path in dropped.items():
            target = raw_dir / "_dropped" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if path.is_file():
                path.rename(target)
                moved.append(rel)
        recompute(live, settings)
        return written, moved
    finally:
        live.close()


def metrics_clock(live: sqlite3.Connection) -> tuple[tuple[str, str] | None, str | None]:
    """The (today, now) to recompute the replay under, or why the derived rows cannot be
    compared: the engine never ran, or live holds a payload newer than its last run."""
    clock = last_run(live)
    if clock is None:
        return None, "the metrics engine has not run on the live db"
    newest = live.execute(
        "SELECT MAX(received_at_utc) FROM raw_archive WHERE parsed_ok = 1"
    ).fetchone()[0]
    if newest is not None and newest > clock[1]:
        return None, (
            f"live holds a payload received {newest}, after its last recompute at {clock[1]}; "
            "run `uv run python -m tools.metrics --recompute` and verify again"
        )
    return clock, None


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
    group.add_argument("--rebuild-live", action="store_true")
    parser.add_argument("--drop-ids", default="", help="with --rebuild-live: 1,2,3")
    parser.add_argument("--scratch", default=str(SCRATCH_DB))
    args = parser.parse_args(argv)
    settings = load_settings()
    try:
        return _run(args, settings)
    except SchemaMismatch as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2


def _run(args: argparse.Namespace, settings: Settings) -> int:
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

    if args.rebuild_live:
        if not live_exists:
            print(f"no live database at {settings.storage.db_path}", file=sys.stderr)
            return 2
        try:
            drop = tuple(int(part) for part in args.drop_ids.split(",") if part.strip())
            written, moved = rebuild_live(settings, Path(args.scratch), drop)
        except ValueError as exc:
            print(f"refusing: {exc}", file=sys.stderr)
            return 2
        for table, count in written.items():
            print(f"{table:24s} {count}")
        for rel in moved:
            print(f"dropped: {rel} moved to data/raw/_dropped/")
        print("rebuilt live from the archive; now run: uv run python -m tools.replay --verify")
        return 0

    if args.since:
        recorded = connect_live(settings.storage.db_path) if live_exists else None
        try:
            plan = replay_plan(settings.storage.raw_dir, recorded, args.since)
        finally:
            if recorded is not None:
                recorded.close()
        conn = replay(settings.storage.raw_dir, Path(args.scratch), settings, plan=plan)
        for note in [*plan.notes(), *plan.refused()]:
            print(f"note: {note}", file=sys.stderr)
        print(report(conn))
        conn.close()
        return 0

    live = connect_live(settings.storage.db_path)
    try:
        if args.snapshot:
            target = Path(args.snapshot)
            target.parent.mkdir(parents=True, exist_ok=True)
            snap = snapshot(live, derived=True)
            target.write_text(json.dumps(snap, indent=1, sort_keys=True))
            print(f"snapshot {target} checksum {checksum(snap)}")
            return 0
        saved = json.loads(Path(args.diff).read_text())
        lines = diff(snapshot(live, derived=True), saved)
        for line in lines:
            print(line)
        print(f"{len(lines)} difference(s)")
        return 1 if lines else 0
    finally:
        live.close()


if __name__ == "__main__":
    sys.exit(main())
