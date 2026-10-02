from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import Settings, load_settings
from app.db import SchemaMismatch, connect_live, open_db
from app.ingest.health import router as health_router
from app.jobs.scheduler import start_scheduler, stop_scheduler
from app.web.preview import router as preview_router
from app.web.status import router as status_router


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or load_settings()

    def open_conn() -> sqlite3.Connection:
        return connect_live(resolved.storage.db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        enabled = resolved.scheduler.enabled
        app.state.scheduler = start_scheduler(resolved, open_conn) if enabled else None
        try:
            yield
        finally:
            if app.state.scheduler is not None:
                stop_scheduler(app.state.scheduler)
                app.state.scheduler = None

    app = FastAPI(
        title="life-dashboard", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.state.settings = resolved
    app.state.scheduler = None
    app.state.open_conn = open_conn
    open_db(resolved.storage.db_path).close()

    @app.exception_handler(SchemaMismatch)
    async def schema_mismatch(request: Request, exc: SchemaMismatch) -> JSONResponse:
        return JSONResponse({"status": "schema_mismatch", "error": str(exc)}, status_code=503)

    app.include_router(health_router)
    app.include_router(status_router)
    app.include_router(preview_router)
    return app


app = create_app()
