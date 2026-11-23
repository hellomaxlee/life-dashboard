"""Back up, restore, and verify the SQLite db and the raw archive. Local paths only.

PATH may be the word `latest`: the newest complete backup under backup.dir.
Exit codes: 0 done / identical, 1 --verify found a difference, 2 refused or not a usable
backup (missing, truncated, or unreadable).

python -m tools.backup                          nightly: <backup.dir>/life-<utc stamp>, then prune
python -m tools.backup --out PATH               one backup at exactly PATH, nothing pruned
python -m tools.backup --restore PATH --to DIR  write DIR/life.db and DIR/raw/ from a backup
python -m tools.backup --verify PATH            restore to a temp dir and diff against the live db

A backup is a directory: life.db (sqlite3 backup API, safe while the service writes), raw/
(the archive, hard-linked against the previous backup when the file is unchanged), and
manifest.json. The db is copied before the archive, so every raw_archive row in a backup has
its file; the archive may hold files newer than the db copy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings, load_settings
from app.db import SchemaMismatch, connect_live, open_db
from app.timeutil import UTC_ISO, now_utc
from tools import replay
from tools.replay import checksum, diff, snapshot

DB_NAME = "life.db"
RAW_NAME = "raw"
MANIFEST_NAME = "manifest.json"
PARTIAL_SUFFIX = ".partial"
STAMP = "%Y%m%dT%H%M%SZ"
_BACKUP_NAME = re.compile(r"^life-\d{8}T\d{6}Z(-\d+)?$")
PARTIAL_STALE_S = 3600


class BackupError(Exception):
    pass


@dataclass(frozen=True)
class BackupResult:
    path: Path
    data_checksum: str
    raw_files: int
    raw_bytes: int
    raw_linked: int
    raw_missing: list[str] = field(default_factory=list)


def backup_name(moment: datetime) -> str:
    return f"life-{moment.strftime(STAMP)}"


def backup_created_at(path: Path) -> datetime:
    return datetime.strptime(path.name.removeprefix("life-")[:16], STAMP).replace(tzinfo=UTC)


def list_backups(backup_dir: Path) -> list[Path]:
    """Complete nightly backups under a directory, oldest first."""
    if not backup_dir.is_dir():
        return []
    return sorted(
        p
        for p in backup_dir.iterdir()
        if _BACKUP_NAME.match(p.name) and (p / MANIFEST_NAME).is_file()
    )


def _open_plain(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise BackupError(f"no database at {db_path}")
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _read[T](db_path: Path, query: Callable[[sqlite3.Connection], T]) -> T:
    """Run one read against a db file. A damaged or truncated file is a BackupError."""
    conn = _open_plain(db_path)
    try:
        return query(conn)
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"cannot read {db_path}: {exc}") from exc
    finally:
        conn.close()


def integrity(db_path: Path) -> str:
    return _read(db_path, lambda c: str(c.execute("PRAGMA integrity_check").fetchone()[0]))


def data_snapshot(db_path: Path) -> dict[str, list[dict[str, object]]]:
    return _read(db_path, snapshot)


def data_checksum(db_path: Path) -> str:
    return checksum(data_snapshot(db_path))


def _fsync_file(path: Path) -> None:
    with path.open("rb") as fh:
        os.fsync(fh.fileno())


def _copy_db(db_path: Path, target: Path) -> None:
    if not db_path.is_file():
        raise BackupError(f"no database at {db_path}")
    source = sqlite3.connect(db_path)
    source.execute("PRAGMA busy_timeout=5000")
    dest = sqlite3.connect(target)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()
    _fsync_file(target)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def raw_rel_path(source: str, recorded_path: str) -> Path:
    return Path(source) / Path(recorded_path).name


def _raw_rows(db_path: Path) -> list[sqlite3.Row]:
    query = "SELECT id, source, path, sha256, parsed_ok FROM raw_archive ORDER BY id"
    return _read(db_path, lambda c: c.execute(query).fetchall())


def recorded_raw(db_path: Path) -> dict[Path, str]:
    """Archive-relative path to recorded sha256, for every raw_archive row."""
    return {raw_rel_path(r["source"], r["path"]): r["sha256"] for r in _raw_rows(db_path)}


def _copy_raw(
    raw_dir: Path, target: Path, link_from: Path | None, recorded: dict[Path, str]
) -> tuple[int, int, int, list[dict[str, object]]]:
    """Copy the archive. A file is hard-linked to the previous backup's copy only when that
    copy's sha256 is the one raw_archive recorded; anything else is copied from live.
    Returns counts and a listing (name, size, sha256) of what the backup now holds."""
    target.mkdir(parents=True, exist_ok=True)
    files = total = linked = 0
    listing: list[dict[str, object]] = []
    if not raw_dir.is_dir():
        return files, total, linked, listing
    for src in sorted(p for p in raw_dir.rglob("*") if p.is_file()):
        rel = src.relative_to(raw_dir)
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        earlier = link_from / RAW_NAME / rel if link_from is not None else None
        done = False
        if earlier is not None and earlier.is_file() and _sha256(earlier) == recorded.get(rel):
            try:
                os.link(earlier, dest)
                done = True
                linked += 1
            except OSError:
                done = False
        if not done:
            shutil.copy2(src, dest)
        size = dest.stat().st_size
        sha = recorded[rel] if done else _sha256(dest)
        listing.append({"name": rel.as_posix(), "size": size, "sha256": sha})
        files += 1
        total += size
    return files, total, linked, listing


def missing_raw_files(db_path: Path, raw_dir: Path, check_sha: bool = False) -> list[str]:
    """raw_archive rows whose file is absent (or, with check_sha, altered) under raw_dir."""
    problems: list[str] = []
    for row in _raw_rows(db_path):
        path = raw_dir / raw_rel_path(row["source"], row["path"])
        if not path.is_file():
            problems.append(f"raw_archive {row['id']}: file missing: {path.name}")
        elif check_sha and _sha256(path) != row["sha256"]:
            problems.append(f"raw_archive {row['id']}: sha256 mismatch: {path.name}")
    return problems


def create_backup(
    settings: Settings, out: Path, now: datetime | None = None, link_from: Path | None = None
) -> BackupResult:
    """Write one complete backup directory at `out`. It appears only when it is whole.

    A raw file that raw_archive records but the live archive no longer has does not stop the
    backup: the db copy still lands and the gap is listed in the manifest as `raw_missing`.
    """
    if out.exists():
        raise BackupError(f"backup target already exists: {out}")
    moment = now or now_utc()
    if link_from is None:
        earlier = [p for p in list_backups(out.parent) if p != out]
        link_from = earlier[-1] if earlier else None
    scratch = out.with_name(out.name + PARTIAL_SUFFIX)
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True)
    try:
        db_copy = scratch / DB_NAME
        _copy_db(settings.storage.db_path, db_copy)
        state = integrity(db_copy)
        if state != "ok":
            raise BackupError(f"backup copy failed integrity_check: {state}")
        recorded = recorded_raw(db_copy)
        files, total, linked, listing = _copy_raw(
            settings.storage.raw_dir, scratch / RAW_NAME, link_from, recorded
        )
        missing = sorted(
            rel.as_posix() for rel in recorded if not (scratch / RAW_NAME / rel).is_file()
        )
        digest = data_checksum(db_copy)
        manifest = {
            "created_at_utc": moment.strftime(UTC_ISO),
            "db_file": DB_NAME,
            "db_bytes": db_copy.stat().st_size,
            "data_checksum": digest,
            "raw_files": files,
            "raw_bytes": total,
            "raw_linked": linked,
            "raw_missing": missing,
            "raw_listing": listing,
            "db_sha256": _sha256(db_copy),
        }
        (scratch / MANIFEST_NAME).write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
        _fsync_file(scratch / MANIFEST_NAME)
        os.replace(scratch, out)
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        raise
    return BackupResult(out, digest, files, total, linked, missing)


def _last_touched(partial: Path) -> float:
    watched = (partial, *partial.glob(RAW_NAME), *partial.glob(f"{RAW_NAME}/*"))
    return max(p.stat().st_mtime for p in watched)


def prune(backup_dir: Path, keep: int, protect: Path | None = None) -> list[Path]:
    """Delete all but the newest `keep` nightly backups (newest by stamp), and partial ones
    nobody has written to for an hour. A younger partial may be another process's backup in
    progress. `protect` is never deleted: the backup just written always survives its own
    retention pass, even when a wrongly future-stamped backup outranks it."""
    removed: list[Path] = []
    backups = list_backups(backup_dir)
    for path in backups[: max(len(backups) - max(keep, 1), 0)]:
        if path == protect:
            continue
        shutil.rmtree(path)
        removed.append(path)
    if backup_dir.is_dir():
        for path in sorted(backup_dir.iterdir()):
            stem = path.name.removesuffix(PARTIAL_SUFFIX)
            if not (path.name.endswith(PARTIAL_SUFFIX) and _BACKUP_NAME.match(stem)):
                continue
            if time.time() - _last_touched(path) > PARTIAL_STALE_S:
                shutil.rmtree(path)
                removed.append(path)
    return removed


def _free_name(backup_dir: Path, moment: datetime) -> Path:
    base = backup_name(moment)
    candidate, n = backup_dir / base, 1
    while candidate.exists() or candidate.with_name(candidate.name + PARTIAL_SUFFIX).exists():
        n += 1
        candidate = backup_dir / f"{base}-{n}"
    return candidate


def nightly(settings: Settings, now: datetime | None = None) -> BackupResult:
    """The scheduled backup: a stamped directory under backup.dir, then retention."""
    moment = now or now_utc()
    result = create_backup(settings, _free_name(settings.backup.dir, moment), moment)
    prune(settings.backup.dir, settings.backup.keep, protect=result.path)
    return result


def read_manifest(backup: Path) -> dict[str, object]:
    manifest = backup / MANIFEST_NAME
    if not manifest.is_file() or not (backup / DB_NAME).is_file():
        raise BackupError(f"not a backup (no {MANIFEST_NAME} or {DB_NAME}): {backup}")
    if (backup / DB_NAME).stat().st_size == 0:
        raise BackupError(f"backup db is empty: {backup / DB_NAME}")
    try:
        return json.loads(manifest.read_text())
    except ValueError as exc:
        raise BackupError(f"unreadable manifest in {backup}: {exc}") from exc


def restore(backup: Path, to: Path, overwrite: bool = False) -> Path:
    """Copy a backup's db and raw archive into `to`. Returns the restored db path.

    Without `overwrite` nothing existing is replaced: not the db, and not a raw file whose
    content differs from the backup's. The check runs before anything is written.
    """
    read_manifest(backup)
    target = to / DB_NAME
    if target.exists() and not overwrite:
        raise BackupError(f"{target} exists; refusing to overwrite it")
    source_raw = backup / RAW_NAME
    raw_files = sorted(p for p in source_raw.rglob("*") if p.is_file())
    if not overwrite:
        differing = [
            src.relative_to(source_raw).as_posix()
            for src in raw_files
            if (to / RAW_NAME / src.relative_to(source_raw)).is_file()
            and _sha256(to / RAW_NAME / src.relative_to(source_raw)) != _sha256(src)
        ]
        if differing:
            raise BackupError(
                f"raw file(s) under {to / RAW_NAME} differ from the backup; refusing to "
                f"overwrite: {', '.join(differing)}"
            )
    to.mkdir(parents=True, exist_ok=True)
    scratch = to / (DB_NAME + PARTIAL_SUFFIX)
    shutil.copy2(backup / DB_NAME, scratch)
    _fsync_file(scratch)
    try:
        state = integrity(scratch)
        if state != "ok":
            raise BackupError(f"backup db failed integrity_check: {state}")
        data_snapshot(scratch)
    except BackupError:
        for suffix in ("", "-wal", "-shm"):
            Path(str(scratch) + suffix).unlink(missing_ok=True)
        raise
    for suffix in ("-wal", "-shm"):
        Path(str(scratch) + suffix).unlink(missing_ok=True)
        Path(str(target) + suffix).unlink(missing_ok=True)
    os.replace(scratch, target)
    for src in raw_files:
        dest = to / RAW_NAME / src.relative_to(source_raw)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    (to / RAW_NAME).mkdir(parents=True, exist_ok=True)
    return target


def _raw_archive_state(db_path: Path) -> list[dict[str, object]]:
    return [
        {"id": r["id"], "sha256": r["sha256"], "parsed_ok": r["parsed_ok"]}
        for r in _raw_rows(db_path)
    ]


def _file_level_problems(backup: Path, manifest: dict[str, object]) -> list[str]:
    """Compare the backup's files with what the manifest recorded when it was written."""
    problems: list[str] = []
    recorded_sha = manifest.get("db_sha256")
    if recorded_sha is not None:
        actual = _sha256(backup / DB_NAME)
        if actual != recorded_sha:
            problems.append(f"manifest: db file sha256 {actual} is not the recorded {recorded_sha}")
    listing = manifest.get("raw_listing")
    if isinstance(listing, list):
        raw = backup / RAW_NAME
        actual_files = {p.relative_to(raw).as_posix(): p for p in raw.rglob("*") if p.is_file()}
        listed = {str(entry["name"]): entry for entry in listing}
        for name in sorted(set(actual_files) - set(listed)):
            problems.append(f"raw listing: not in the manifest: {name}")
        for name in sorted(set(listed) - set(actual_files)):
            problems.append(f"raw listing: missing from the backup: {name}")
        for name in sorted(set(listed) & set(actual_files)):
            path, entry = actual_files[name], listed[name]
            if path.stat().st_size != entry["size"] or _sha256(path) != entry["sha256"]:
                problems.append(f"raw listing: changed since the backup was written: {name}")
    return problems


def verify(
    backup: Path, settings: Settings, replay_check: bool = True, notes: list[str] | None = None
) -> list[str]:
    """Every way the backup fails to reproduce the live data. Empty means identical.

    `notes` collects things worth saying that are not differences (unrecorded raw files,
    payloads that were never parsed). A backup that cannot be read at all is a BackupError.
    """
    manifest = read_manifest(backup)
    if settings.storage.db_path.is_file():
        connect_live(settings.storage.db_path).close()
    notes = notes if notes is not None else []
    problems = _file_level_problems(backup, manifest)
    if "db_sha256" not in manifest:
        notes.append("manifest predates db_sha256 and raw_listing; file-level checks skipped")
    with tempfile.TemporaryDirectory(prefix="life-backup-verify-") as tmp:
        workdir = Path(tmp)
        restored_db = restore(backup, workdir / "restored")
        restored_raw = workdir / "restored" / RAW_NAME
        as_written = data_snapshot(restored_db)
        if checksum(as_written) != manifest.get("data_checksum"):
            problems.append(
                f"manifest: backup db checksum {checksum(as_written)} is not the recorded "
                f"{manifest.get('data_checksum')}"
            )
        problems.extend(
            f"manifest: raw file was already missing at backup time: {rel}"
            for rel in manifest.get("raw_missing", [])
        )
        problems.extend(missing_raw_files(restored_db, restored_raw, check_sha=True))

        try:
            open_db(restored_db).close()
        except Exception as exc:
            problems.append(f"boot: the service would not start on this backup: {exc}")
        restored = data_snapshot(restored_db)
        restored_rows = {"raw_archive": _raw_archive_state(restored_db)}

        if not settings.storage.db_path.is_file():
            problems.append(f"live: no database at {settings.storage.db_path}")
        else:
            live_conn = connect_live(settings.storage.db_path)
            try:
                live = snapshot(live_conn)
            finally:
                live_conn.close()
            live_rows = {"raw_archive": _raw_archive_state(settings.storage.db_path)}
            problems.extend(f"live vs backup: {line}" for line in diff(live, restored))
            problems.extend(f"live vs backup: {line}" for line in diff(live_rows, restored_rows))

        if replay_check:
            recorded = _open_plain(restored_db)
            try:
                plan = replay.replay_plan(restored_raw, recorded)
            finally:
                recorded.close()
            scratch = replay.replay(restored_raw, workdir / "replay.db", settings, plan=plan)
            try:
                replayed = snapshot(scratch, authored=False)
            finally:
                scratch.close()
            unauthored = _read(restored_db, lambda conn: snapshot(conn, authored=False))
            problems.extend(f"raw replay vs backup: {line}" for line in diff(replayed, unauthored))
            notes.extend(plan.notes())
    return problems


def _is_live(target: Path, settings: Settings) -> bool:
    live = settings.storage.db_path
    if target.exists() and live.exists():
        return os.path.samefile(target, live)
    return target.resolve() == live.resolve()


def _named(path_arg: str, settings: Settings) -> Path:
    if path_arg != "latest":
        return Path(path_arg)
    backups = list_backups(settings.backup.dir)
    if not backups:
        raise BackupError(f"no complete backup under {settings.backup.dir}")
    return backups[-1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools.backup", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--out", metavar="PATH")
    group.add_argument("--restore", metavar="PATH")
    group.add_argument("--verify", metavar="PATH")
    parser.add_argument("--to", metavar="DIR", help="restore target directory")
    parser.add_argument(
        "--overwrite", action="store_true", help="replace DIR/life.db and differing raw files"
    )
    parser.add_argument(
        "--overwrite-live", action="store_true", help="replace the live db (stop the service)"
    )
    parser.add_argument("--no-replay", action="store_true", help="verify: skip the raw replay")
    args = parser.parse_args(argv)
    settings = load_settings()

    try:
        if args.restore:
            if not args.to:
                parser.error("--restore needs --to DIR")
            target = Path(args.to) / DB_NAME
            live = _is_live(target, settings)
            if target.exists() and live and not args.overwrite_live:
                print(f"refusing: {target} is the live db; pass --overwrite-live", file=sys.stderr)
                return 2
            if target.exists() and not live and not args.overwrite:
                print(f"refusing: {target} exists; pass --overwrite", file=sys.stderr)
                return 2
            restored = restore(
                _named(args.restore, settings),
                Path(args.to),
                overwrite=args.overwrite or args.overwrite_live,
            )
            print(f"restored {restored} checksum {data_checksum(restored)}")
            return 0

        if args.verify:
            notes: list[str] = []
            target = _named(args.verify, settings)
            problems = verify(target, settings, replay_check=not args.no_replay, notes=notes)
            for line in problems:
                print(line)
            for note in notes:
                print(f"note: {note}")
            print(f"{len(problems)} difference(s)")
            return 1 if problems else 0

        result = create_backup(settings, Path(args.out)) if args.out else nightly(settings)
    except BackupError as exc:
        print(f"backup error: {exc}", file=sys.stderr)
        return 2
    except SchemaMismatch as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    print(
        f"backup {result.path} checksum {result.data_checksum} "
        f"raw_files={result.raw_files} raw_bytes={result.raw_bytes} linked={result.raw_linked} "
        f"raw_missing={len(result.raw_missing)}"
    )
    for rel in result.raw_missing:
        print(f"missing from the live archive, not in this backup: {rel}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
