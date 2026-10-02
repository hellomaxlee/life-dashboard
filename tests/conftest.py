from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_SESSION_DIR = Path(tempfile.mkdtemp(prefix="life-dashboard-import-"))
os.environ.setdefault("LIFE_DB_PATH", str(_SESSION_DIR / "life.db"))
os.environ.setdefault("LIFE_RAW_DIR", str(_SESSION_DIR / "raw"))
os.environ["HEALTH_EXPORT_TOKEN"] = ""
os.environ["LIFE_SCHEDULER_ENABLED"] = "0"
os.environ.setdefault("LIFE_BACKUP_DIR", str(_SESSION_DIR / "backups"))

from app.config import Settings, load_settings  # noqa: E402
from app.db import open_db  # noqa: E402
from app.main import create_app  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "health"


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("LIFE_DB_PATH", str(tmp_path / "life.db"))
    monkeypatch.setenv("LIFE_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("HEALTH_EXPORT_TOKEN", "")
    return load_settings()


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def db(settings: Settings) -> Iterator[sqlite3.Connection]:
    conn = open_db(settings.storage.db_path)
    yield conn
    conn.close()


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def fixture_json(name: str) -> dict:
    return json.loads(fixture_bytes(name))


def post_fixture(client: TestClient, name: str, **headers: str):
    return client.post(
        "/ingest/health",
        content=fixture_bytes(name),
        headers={"content-type": "application/json", **headers},
    )


def count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
