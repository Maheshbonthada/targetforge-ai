"""FastAPI application factory and entrypoint (`uvicorn targetforge.main:app`)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from targetforge.api.routers import health, runs
from targetforge.config import get_settings
from targetforge.core.logging import configure_logging
from targetforge.core.telemetry import configure_telemetry
from targetforge.db.models import PipelineRun, RunStatus
from targetforge.db.session import get_session_factory

logger = structlog.get_logger(__name__)


async def _reconcile_orphaned_runs() -> None:
    """Any row still `RUNNING` when this process starts belongs to a
    background task that died with a previous process — nothing is coming
    back to finish it (see `Settings.orphaned_run_reconciliation_enabled`'s
    docstring, and docs/scaling-to-real-genomes.md §5 for the underlying
    execution-model limitation this papers over, not fixes). Without this,
    such a run stays "running" forever, indistinguishable from one that's
    genuinely in flight, and On-call has no signal to act on.
    """
    session_factory = get_session_factory()
    async with session_factory() as db:
        result = await db.execute(select(PipelineRun).where(PipelineRun.status == RunStatus.RUNNING))
        orphaned = list(result.scalars().all())
        if not orphaned:
            return
        for run in orphaned:
            run.status = RunStatus.FAILED
            run.error_message = (
                "Orphaned at process startup — the background task executing "
                "this run did not survive a restart/redeploy."
            )
            run.completed_at = datetime.now(UTC)
        await db.commit()
        logger.warning("pipeline_runs.orphaned_reconciled", count=len(orphaned))


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_telemetry(settings.applicationinsights_connection_string)
    logger.info("app.startup", environment=settings.environment, ai_provider=settings.ai_provider)
    if settings.orphaned_run_reconciliation_enabled:
        try:
            await _reconcile_orphaned_runs()
        except Exception as exc:  # noqa: BLE001 — a reconciliation failure must never block startup
            logger.error("pipeline_runs.orphaned_reconciliation_failed", error=str(exc))
    yield
    logger.info("app.shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="TargetForge API",
        description="Sequencing-to-insight genomics target discovery pipeline for biotech/pharma R&D teams.",
        version="0.1.0",
        root_path=settings.api_root_path,
        lifespan=lifespan,
    )

    # Locked down deliberately, same rationale as TrialReady: this API is
    # called by TargetForge's own frontend and nothing else. Widen only for
    # an explicit, reviewed integration partner.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        structlog.contextvars.bind_contextvars(request_id=request_id)
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        structlog.contextvars.clear_contextvars()
        return response

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error("request.unhandled_exception", path=request.url.path, error=str(exc))
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    app.include_router(health.router)
    app.include_router(runs.router)

    return app


app = create_app()
