from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import Settings, load_settings
from app.db import open_db
from app.ingest.health import router as health_router
from app.jobs.scheduler import start_scheduler
from app.web.status import router as status_router


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or load_settings()

    def open_conn() -> sqlite3.Connection:
        return open_db(resolved.storage.db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        enabled = resolved.scheduler.enabled
        app.state.scheduler = start_scheduler(resolved, open_conn) if enabled else None
        try:
            yield
        finally:
            if app.state.scheduler is not None:
                app.state.scheduler.shutdown(wait=True)
                app.state.scheduler = None

    app = FastAPI(
        title="life-dashboard", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.state.settings = resolved
    app.state.scheduler = None
    app.state.open_conn = open_conn
    open_conn().close()
    app.include_router(health_router)
    app.include_router(status_router)
    return app


app = create_app()
