"""POST /runs triggers a pipeline run; GET /runs/{id} polls it. A run is
long-running (multiple live external API calls + several LLM calls), so this
follows the standard async-job HTTP shape: 202 Accepted with a row the client
polls, not a blocking request. The DB row is the source of truth for status —
the background task and the read path never share application state.

Only `run_from_candidate_genes` is exposed here (the fully real, fully
tested entry point — see orchestration/supervisor.py). `run_full_from_fastq`
needs raw sequencing files and real alignment/variant-calling compute this
API does not yet accept uploads for; wiring that entry point up is tracked in
docs/scaling-to-real-genomes.md, not silently half-exposed here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from targetforge.db.models import PipelineRun, RunStatus
from targetforge.db.session import get_db_session, get_session_factory
from targetforge.deps import get_supervisor
from targetforge.orchestration.supervisor import Supervisor
from targetforge.schemas.api import RunCreate, RunOut

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


async def _execute_run(
    run_id: uuid.UUID,
    candidate_genes: list[str],
    top_n_for_experiments: int,
    supervisor: Supervisor,
) -> None:
    """Runs outside the request/response cycle, so it opens its own DB
    session rather than reusing the request-scoped one FastAPI would tear
    down as soon as the response is sent.
    """
    session_factory = get_session_factory()
    async with session_factory() as db:
        run = await db.get(PipelineRun, run_id)
        if run is None:
            logger.error("pipeline_run.missing_at_execute", run_id=str(run_id))
            return

        try:
            report, state = await supervisor.run_from_candidate_genes(
                candidate_genes, top_n_for_experiments=top_n_for_experiments, run_id=str(run_id)
            )
            run.status = RunStatus.SUCCEEDED
            run.report_json = report.model_dump(mode="json")
            run.stage_status_json = {stage.value: st.value for stage, st in state.stage_status.items()}
        except Exception as exc:  # noqa: BLE001 — a failed run is a valid, recorded outcome, not a 500
            logger.error("pipeline_run.failed", run_id=str(run_id), error=str(exc))
            run.status = RunStatus.FAILED
            run.error_message = str(exc)[:2000]
        finally:
            run.completed_at = datetime.now(UTC)
            await db.commit()


@router.post("", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    payload: RunCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
    supervisor: Supervisor = Depends(get_supervisor),
) -> PipelineRun:
    run = PipelineRun(
        candidate_genes=payload.candidate_genes,
        status=RunStatus.RUNNING,
        stage_status_json={},
        triggered_by="api-local-dev",
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    background_tasks.add_task(
        _execute_run, run.id, payload.candidate_genes, payload.top_n_for_experiments, supervisor
    )
    return run


@router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: uuid.UUID, db: AsyncSession = Depends(get_db_session)) -> PipelineRun:
    run = await db.get(PipelineRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return run


@router.get("", response_model=list[RunOut])
async def list_runs(db: AsyncSession = Depends(get_db_session), limit: int = 20) -> list[PipelineRun]:
    result = await db.execute(select(PipelineRun).order_by(PipelineRun.created_at.desc()).limit(min(limit, 100)))
    return list(result.scalars().all())
