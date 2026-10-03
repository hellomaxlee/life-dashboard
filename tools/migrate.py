"""Bring the live db up to this code's schema. The only tool that migrates the live db.

python -m tools.migrate     apply pending migrations, then normalise stored activities

Run it with the service stopped, after a backup (workflows/run-service.md, Upgrading). The
service also migrates when it starts; this is the explicit, visible way to do it first.
Safe to repeat: a second run applies nothing and changes nothing.
"""

from __future__ import annotations

import argparse
import sys

from app.config import load_settings
from app.db import connect, migrate, stored_schema_version
from app.ingest.store import recluster_all


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools.migrate", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.parse_args(argv)
    settings = load_settings()
    conn = connect(settings.storage.db_path)
    try:
        before = stored_schema_version(conn)
        applied = migrate(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            changed = recluster_all(conn, settings)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        after = stored_schema_version(conn)
    finally:
        conn.close()
    done = f"applied {', '.join(str(v) for v in applied)}" if applied else "nothing to apply"
    print(f"{settings.storage.db_path}: schema {before} -> {after} ({done})")
    print(f"normalised {changed} activity record(s) to the clustering rule")
    return 0


if __name__ == "__main__":
    sys.exit(main())
