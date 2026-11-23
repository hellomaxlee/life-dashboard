"""SQLite connections and hand-written migrations.

Migrations are applied in two places only: at service start (`create_app` calls `open_db`)
and by explicit tools (`tools.migrate`, the restore boot check, tests). The list of
migrations is read once, when this module is imported, so a migration file that lands in
the checkout while a process is running is never applied by that process. Request-time and
job-time connections come from `connect_live`, which changes nothing and refuses a db whose
schema version is not the one this code was started with.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_MIGRATION_NAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


class SchemaMismatch(RuntimeError):
    pass


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def list_migrations(migrations_dir: Path = MIGRATIONS_DIR) -> list[tuple[int, Path]]:
    found: list[tuple[int, Path]] = []
    for path in sorted(migrations_dir.glob("*.sql")):
        match = _MIGRATION_NAME.match(path.name)
        if match is None:
            raise ValueError(f"migration file name not NNN_name.sql: {path.name}")
        found.append((int(match.group(1)), path))
    return found


def _load(migrations_dir: Path) -> tuple[tuple[int, str, str], ...]:
    return tuple((v, path.stem, path.read_text()) for v, path in list_migrations(migrations_dir))


KNOWN_MIGRATIONS = _load(MIGRATIONS_DIR)
CODE_SCHEMA_VERSION = KNOWN_MIGRATIONS[-1][0] if KNOWN_MIGRATIONS else 0


def schema_version(conn: sqlite3.Connection) -> int:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version "
        "(version INTEGER PRIMARY KEY, applied_at_utc TEXT NOT NULL, name TEXT NOT NULL)"
    )
    row = conn.execute("SELECT COALESCE(MAX(version), 0) AS v FROM schema_version").fetchone()
    return int(row["v"])


def stored_schema_version(conn: sqlite3.Connection) -> int:
    """The db's schema version, read without creating or changing anything."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
    ).fetchone()
    if exists is None:
        return 0
    return int(conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0])


def migrate(conn: sqlite3.Connection, migrations_dir: Path | None = None) -> list[int]:
    """Apply every migration above the current version, in order. Returns versions applied.

    Without `migrations_dir` the migrations are the ones this process loaded at import.
    """
    known = KNOWN_MIGRATIONS if migrations_dir is None else _load(migrations_dir)
    current = schema_version(conn)
    applied: list[int] = []
    for version, name, sql in known:
        if version <= current:
            continue
        record = (
            "INSERT INTO schema_version (version, applied_at_utc, name) "
            f"VALUES ({version}, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), '{name}');"
        )
        try:
            conn.executescript(f"BEGIN;\n{sql}\n{record}\nCOMMIT;")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        applied.append(version)
    return applied


def open_db(db_path: Path) -> sqlite3.Connection:
    """Connect and migrate. For service start, tools.migrate, scratch databases and tests."""
    conn = connect(db_path)
    migrate(conn)
    return conn


def connect_live(db_path: Path) -> sqlite3.Connection:
    """Connect to an existing db without migrating it; refuse one this code does not match."""
    if not db_path.is_file():
        raise SchemaMismatch(
            f"no database at {db_path}; start the service or run `uv run python -m tools.migrate`"
        )
    conn = connect(db_path)
    try:
        stored = stored_schema_version(conn)
    except Exception:
        conn.close()
        raise
    if stored != CODE_SCHEMA_VERSION:
        conn.close()
        raise SchemaMismatch(
            f"{db_path} is at schema version {stored} and this code expects "
            f"{CODE_SCHEMA_VERSION}; back up, stop the service, run "
            "`uv run python -m tools.migrate`, start it again"
        )
    return conn


def table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    ).fetchall()
    return [r["name"] for r in rows]
