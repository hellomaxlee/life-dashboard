"""POST /ingest/health: archive the raw bytes first, then parse and store."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.config import Settings
from app.ingest.parse import parse_payload
from app.ingest.store import IngestStats, store_payload
from app.timeutil import UTC_ISO, now_utc

SOURCE = "health"
log = logging.getLogger(__name__)
router = APIRouter()


@dataclass(frozen=True)
class ArchiveResult:
    raw_archive_id: int
    path: Path
    sha256: str
    duplicate: bool


def raw_filename(received_at: datetime, sha256: str) -> str:
    return f"{received_at.strftime('%Y%m%dT%H%M%SZ')}_{sha256[:8]}.json"


def received_at_from_filename(name: str) -> str:
    stamp = name.split("_", 1)[0]
    return datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").strftime(UTC_ISO)


def _write_durably(target: Path, body: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as fh:
        fh.write(body)
        fh.flush()
        os.fsync(fh.fileno())


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
    their existing row with duplicate False, so the caller parses them again.
    """
    digest = hashlib.sha256(body).hexdigest()
    existing = conn.execute(
        "SELECT id, path, parsed_ok FROM raw_archive WHERE sha256 = ?", (digest,)
    ).fetchone()
    if existing is not None:
        recorded = Path(existing["path"])
        if write_file and not existing["parsed_ok"] and not recorded.is_file():
            _write_durably(recorded, body)
        return ArchiveResult(int(existing["id"]), recorded, digest, bool(existing["parsed_ok"]))
    moment = received_at or now_utc()
    target = path or raw_dir / source / raw_filename(moment, digest)
    if write_file:
        _write_durably(target, body)
    cursor = conn.execute(
        "INSERT INTO raw_archive (source, received_at_utc, sha256, path, byte_len, parsed_ok) "
        "VALUES (?, ?, ?, ?, ?, 0)",
        (source, moment.strftime(UTC_ISO), digest, str(target), len(body)),
    )
    return ArchiveResult(int(cursor.lastrowid), target, digest, False)


def mark_parsed(conn: sqlite3.Connection, raw_archive_id: int, ok: bool, error: str | None) -> None:
    conn.execute(
        "UPDATE raw_archive SET parsed_ok = ?, error = ? WHERE id = ?",
        (int(ok), error, raw_archive_id),
    )


def ingest_archived(
    conn: sqlite3.Connection, body: bytes, raw_archive_id: int, settings: Settings
) -> IngestStats:
    """Parse and store an already-archived payload inside one transaction."""
    try:
        payload = json.loads(body)
    except ValueError as exc:
        mark_parsed(conn, raw_archive_id, False, f"malformed json: {exc}")
        raise
    conn.execute("BEGIN")
    try:
        parsed = parse_payload(payload, settings.home_tz)
        stats = store_payload(conn, parsed, raw_archive_id, settings)
        mark_parsed(conn, raw_archive_id, True, None)
        conn.execute("COMMIT")
    except Exception as exc:
        conn.execute("ROLLBACK")
        mark_parsed(conn, raw_archive_id, False, f"{type(exc).__name__}: {exc}")
        raise
    if stats.unknown_metrics:
        log.warning("ignored unknown metrics: %s", ", ".join(stats.unknown_metrics))
    return stats


def authorized(request: Request, token: str) -> bool:
    if not token:
        return True
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer ") and header[7:].strip() == token:
        return True
    return request.headers.get("x-api-key", "").strip() == token


@router.post("/ingest/health")
async def ingest_health(request: Request) -> Response:
    settings: Settings = request.app.state.settings
    if not authorized(request, settings.health_export_token):
        return JSONResponse({"status": "unauthorized"}, status_code=401)
    body = await request.body()
    conn: sqlite3.Connection = request.app.state.open_conn()
    try:
        archived = archive_raw(conn, settings.storage.raw_dir, body)
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
        return JSONResponse(
            {
                "status": "ok",
                "raw_archive_id": archived.raw_archive_id,
                "file": archived.path.name,
                "workouts_seen": stats.workouts_seen,
                "workouts_merged": stats.workouts_merged,
                "metrics_rows": stats.metrics_rows,
                "unknown_metrics": stats.unknown_metrics,
            }
        )
    finally:
        conn.close()
