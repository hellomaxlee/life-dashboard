"""POST /ingest/health: archive the raw bytes first, then parse and store.

The stored truth is "every parsed payload applied in raw_archive.id order". `run_ingest`
keeps that true when a payload is parsed late (after a kill or a failed first attempt):
it applies the late payload and then applies every later parsed payload of the same source
again from its raw file, in one write transaction.
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.config import Settings
from app.db import SchemaMismatch
from app.ingest.parse import parse_payload
from app.ingest.store import IngestStats, store_payload
from app.timeutil import UTC_ISO, now_utc

SOURCE = "health"
MAX_BODY_BYTES = 16 * 1024 * 1024  # the largest real push is ~4 MB; a 16 MB body is not a push
RAW_SUFFIXES = {"goodreads": ".xml"}
log = logging.getLogger(__name__)
router = APIRouter()

Apply = Callable[[sqlite3.Connection, bytes, int, Settings, bool], object]


class RecoveryError(Exception):
    """A late payload could not be applied because a later one could not be re-applied."""


@dataclass(frozen=True)
class ArchiveResult:
    raw_archive_id: int
    path: Path
    sha256: str
    duplicate: bool


def raw_suffix(source: str) -> str:
    """The archive file extension of a source's payloads: `.json` unless listed."""
    return RAW_SUFFIXES.get(source, ".json")


def raw_filename(received_at: datetime, sha256: str, source: str = SOURCE) -> str:
    return f"{received_at.strftime('%Y%m%dT%H%M%SZ')}_{sha256[:8]}{raw_suffix(source)}"


def received_at_from_filename(name: str) -> str:
    stamp = name.split("_", 1)[0]
    return datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").strftime(UTC_ISO)


def raw_file(raw_dir: Path, source: str, recorded_path: str) -> Path:
    """Where a recorded payload lives now: always `raw_dir/source/name`.

    Rows written before paths were stored relative hold an absolute path; only its file name
    is used, so a restored or moved copy never reads or writes the original location.
    """
    return raw_dir / source / Path(recorded_path).name


def _write_durably(target: Path, body: bytes) -> None:
    """The bytes and the directory entry both reach the platter before returning. On
    darwin, fsync only pushes to the drive's cache; F_FULLFSYNC asks the drive to flush."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as fh:
        fh.write(body)
        fh.flush()
        _fsync(fh.fileno())
    dir_fd = os.open(target.parent, os.O_RDONLY)
    try:
        _fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _fsync(fd: int) -> None:
    os.fsync(fd)
    if sys.platform == "darwin":
        fcntl.fcntl(fd, fcntl.F_FULLFSYNC)


def _unrecorded_copy(
    conn: sqlite3.Connection, raw_dir: Path, source: str, body: bytes
) -> Path | None:
    """A file already on disk with these exact bytes and no raw_archive row, or None.

    Such a file is a push that died (or met a locked db) after its bytes were written and
    before its row was. The retry adopts it instead of writing a second copy.
    """
    digest = hashlib.sha256(body).hexdigest()
    for candidate in sorted((raw_dir / source).glob(f"*_{digest[:8]}{raw_suffix(source)}")):
        recorded = conn.execute(
            "SELECT 1 FROM raw_archive WHERE source = ? AND path IN (?, ?)",
            (source, f"{source}/{candidate.name}", str(candidate)),
        ).fetchone()
        if recorded is None and candidate.read_bytes() == body:
            return candidate
    return None


def archive_raw(
    conn: sqlite3.Connection,
    raw_dir: Path,
    body: bytes,
    source: str = SOURCE,
    received_at: datetime | None = None,
    write_file: bool = True,
    path: Path | None = None,
) -> ArchiveResult:
    """Write the exact request bytes to disk and record them before anything reads them.

    `duplicate` is True only when these bytes were archived and parsed before. Bytes whose
    earlier parse never finished (a kill or an error left parsed_ok = 0) come back under
    their existing row with duplicate False, so the caller parses them again. The row's
    `path` is stored relative to the archive (`source/name`).
    """
    digest = hashlib.sha256(body).hexdigest()

    def existing_row() -> ArchiveResult | None:
        row = conn.execute(
            "SELECT id, source, path, parsed_ok FROM raw_archive WHERE sha256 = ?", (digest,)
        ).fetchone()
        if row is None:
            return None
        recorded = raw_file(raw_dir, row["source"], row["path"])
        return ArchiveResult(int(row["id"]), recorded, digest, bool(row["parsed_ok"]))

    existing = existing_row()
    if existing is not None:
        if write_file and not existing.duplicate and not existing.path.is_file():
            _write_durably(existing.path, body)
        return existing
    moment = received_at or now_utc()
    target = path
    wrote = False
    if target is None:
        target = _unrecorded_copy(conn, raw_dir, source, body) if write_file else None
        if target is None:
            target = raw_dir / source / raw_filename(moment, digest, source)
            if write_file:
                _write_durably(target, body)
                wrote = True
    try:
        cursor = conn.execute(
            "INSERT INTO raw_archive (source, received_at_utc, sha256, path, byte_len, parsed_ok) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (source, moment.strftime(UTC_ISO), digest, f"{source}/{target.name}", len(body)),
        )
    except sqlite3.IntegrityError:
        racing = existing_row()
        if racing is None:
            raise
        if wrote and racing.path != target:
            target.unlink(missing_ok=True)
        return racing
    return ArchiveResult(int(cursor.lastrowid), target, digest, False)


def mark_parsed(conn: sqlite3.Connection, raw_archive_id: int, ok: bool, error: str | None) -> None:
    conn.execute(
        "UPDATE raw_archive SET parsed_ok = ?, error = ? WHERE id = ?",
        (int(ok), error, raw_archive_id),
    )


def run_ingest(
    conn: sqlite3.Connection,
    body: bytes,
    raw_archive_id: int,
    settings: Settings,
    apply: Apply,
    raw_dir: Path | None = None,
) -> tuple[object, int]:
    """Apply one archived payload, then re-apply every later parsed payload of its source.

    One write transaction (the lock is taken up front): either the payload and the whole
    catch-up land, or nothing does and the row keeps parsed_ok = 0 with the error. Returns
    what `apply` returned for this payload and the number of later payloads re-applied.
    """
    archive_dir = raw_dir or settings.storage.raw_dir
    conn.execute("BEGIN IMMEDIATE")
    try:
        result = apply(conn, body, raw_archive_id, settings, True)
        source = conn.execute(
            "SELECT source FROM raw_archive WHERE id = ?", (raw_archive_id,)
        ).fetchone()["source"]
        later = conn.execute(
            "SELECT id, path, sha256 FROM raw_archive WHERE source = ? AND parsed_ok = 1 "
            "AND id > ? ORDER BY id",
            (source, raw_archive_id),
        ).fetchall()
        for row in later:
            file = raw_file(archive_dir, source, row["path"])
            where = f"cannot re-apply raw_archive {row['id']} after {raw_archive_id}"
            if not file.is_file():
                raise RecoveryError(f"{where}: file missing: {file.name}")
            later_body = file.read_bytes()
            if hashlib.sha256(later_body).hexdigest() != row["sha256"]:
                raise RecoveryError(
                    f"{where}: {file.name} no longer matches its recorded sha256 "
                    "(the file was changed on disk)"
                )
            try:
                apply(conn, later_body, int(row["id"]), settings, False)
            except Exception as exc:
                raise RecoveryError(f"{where}: {type(exc).__name__}: {exc}") from exc
        mark_parsed(conn, raw_archive_id, True, None)
        conn.execute("COMMIT")
    except Exception as exc:
        conn.execute("ROLLBACK")
        mark_parsed(conn, raw_archive_id, False, f"{type(exc).__name__}: {exc}")
        raise
    return result, len(later)


def apply_payload(
    conn: sqlite3.Connection, body: bytes, raw_archive_id: int, settings: Settings, first: bool
) -> IngestStats:
    """Parse and store one payload inside the caller's transaction. `first` is False when
    the payload was applied before and is being applied again to keep id order."""
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise ValueError(f"malformed json: {exc}") from exc
    parsed = parse_payload(payload, settings.home_tz, settings.ingest.sleep_gap_min)
    return store_payload(conn, parsed, raw_archive_id, settings, record_log=first)


def ingest_archived(
    conn: sqlite3.Connection,
    body: bytes,
    raw_archive_id: int,
    settings: Settings,
    raw_dir: Path | None = None,
) -> IngestStats:
    """Parse and store an already-archived payload, keeping raw_archive.id order."""
    stats, reapplied = run_ingest(conn, body, raw_archive_id, settings, apply_payload, raw_dir)
    assert isinstance(stats, IngestStats)
    stats.reapplied = reapplied
    if stats.unknown_metrics:
        log.warning("ignored unknown metrics: %s", ", ".join(stats.unknown_metrics))
    return stats


def authorized(request: Request, token: str) -> bool:
    if not token:
        return True
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer ") and hmac.compare_digest(header[7:].strip(), token):
        return True
    return hmac.compare_digest(request.headers.get("x-api-key", "").strip(), token)


def _busy(settings: Settings, body: bytes, exc: Exception, status: str = "busy") -> Response:
    """The db would not take the row. Keep the bytes on disk and say so; the phone retries."""
    digest = hashlib.sha256(body).hexdigest()
    folder = settings.storage.raw_dir / SOURCE
    kept = next(
        (f for f in sorted(folder.glob(f"*_{digest[:8]}.json")) if f.read_bytes() == body), None
    )
    if kept is None:
        kept = folder / raw_filename(now_utc(), digest)
        _write_durably(kept, body)
    log.error("db %s, payload kept unrecorded as %s: %s", status, kept.name, exc)
    return JSONResponse(
        {"status": status, "file": kept.name, "error": f"{type(exc).__name__}: {exc}"},
        status_code=503,
    )


@router.post("/ingest/health")
async def ingest_health(request: Request) -> Response:
    settings: Settings = request.app.state.settings
    if not authorized(request, settings.health_export_token):
        return JSONResponse({"status": "unauthorized"}, status_code=401)
    declared = request.headers.get("content-length", "0")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return JSONResponse({"status": "too_large", "max_bytes": MAX_BODY_BYTES}, status_code=413)
    body = await request.body()
    try:
        conn: sqlite3.Connection = request.app.state.open_conn()
    except sqlite3.OperationalError as exc:
        return _busy(settings, body, exc)
    except SchemaMismatch as exc:
        return _busy(settings, body, exc, status="schema_mismatch")
    try:
        try:
            archived = archive_raw(conn, settings.storage.raw_dir, body)
        except sqlite3.OperationalError as exc:
            return _busy(settings, body, exc)
        if archived.duplicate:
            return JSONResponse({"status": "duplicate", "raw_archive_id": archived.raw_archive_id})
        try:
            stats = ingest_archived(conn, body, archived.raw_archive_id, settings)
        except ValueError as exc:
            return JSONResponse(
                {
                    "status": "malformed",
                    "raw_archive_id": archived.raw_archive_id,
                    "error": str(exc),
                },
                status_code=422,
            )
        except Exception as exc:
            log.exception("ingest failed for raw_archive %s", archived.raw_archive_id)
            return JSONResponse(
                {"status": "error", "raw_archive_id": archived.raw_archive_id, "error": str(exc)},
                status_code=500,
            )
        from app.metrics.job import recompute_soon

        recompute_soon(getattr(request.app.state, "scheduler", None))
        return JSONResponse(
            {
                "status": "ok",
                "raw_archive_id": archived.raw_archive_id,
                "file": archived.path.name,
                "workouts_seen": stats.workouts_seen,
                "workouts_merged": stats.workouts_merged,
                "workouts_withdrawn": stats.workouts_withdrawn,
                "metrics_rows": stats.metrics_rows,
                "unknown_metrics": stats.unknown_metrics,
                "reapplied": stats.reapplied,
            }
        )
    finally:
        conn.close()
