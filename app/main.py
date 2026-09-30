from __future__ import annotations

import sqlite3

from fastapi import FastAPI

from app.config import Settings, load_settings
from app.db import open_db
from app.ingest.health import router as health_router
from app.web.status import router as status_router


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or load_settings()
    app = FastAPI(title="life-dashboard", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = resolved

    def open_conn() -> sqlite3.Connection:
        return open_db(resolved.storage.db_path)

    app.state.open_conn = open_conn
    open_conn().close()
    app.include_router(health_router)
    app.include_router(status_router)
    return app


app = create_app()
