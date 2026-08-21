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

Every route requires an authenticated caller (`get_current_user` — bypassed
only when `AUTH_DISABLED_FOR_LOCAL_DEV=true`) and every read/delete is scoped
to the caller's own runs. `core/security.py` existed in this codebase from
the start but was never actually wired into a router — a run submitted by
anyone was readable and deletable by anyone who could reach the API. There is
no roles/admin concept yet; this is strict per-caller isolation, matching
TrialReady's simplicity level, not a full authorization model.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from targetforge.config import Settings, get_settings
from targetforge.core.rate_limit import SlidingWindowRateLimiter
from targetforge.core.security import AuthenticatedUser, get_current_user
from targetforge.db.models import PipelineRun, RunStatus
from targetforge.db.session import get_db_session, get_session_factory
from targetforge.deps import get_run_rate_limiter, get_supervisor
from targetforge.orchestration.supervisor import Supervisor
from targetforge.schemas.api import RunCreate, RunOut

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


async def _execute_run(
    run_id: uuid.UUID,
    candidate_genes: list[str],
    top_n_for_experiments: int,
    supervisor: Supervisor,
    timeout_seconds: int,
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
            report, state = await asyncio.wait_for(
                supervisor.run_from_candidate_genes(
                    candidate_genes, top_n_for_experiments=top_n_for_experiments, run_id=str(run_id)
                ),
                timeout=timeout_seconds,
            )
            run.status = RunStatus.SUCCEEDED
            run.report_json = report.model_dump(mode="json")
            run.stage_status_json = {stage.value: st.value for stage, st in state.stage_status.items()}
        except TimeoutError:
            logger.error("pipeline_run.timed_out", run_id=str(run_id), timeout_seconds=timeout_seconds)
            run.status = RunStatus.FAILED
            run.error_message = f"Run exceeded the {timeout_seconds}s time budget and was aborted."
        except Exception as exc:  # noqa: BLE001 — a failed run is a valid, recorded outcome, not a 500
            logger.error("pipeline_run.failed", run_id=str(run_id), error=str(exc))
            run.status = RunStatus.FAILED
            # LlmParseError messages can carry a large raw-JSON fragment
            # (see core/llm.py) — truncate before persisting, same 2000-char
            # cap as the error_message column itself, so a pathological
            # error can't inflate the JSONB row further than the column
            # would already reject.
            run.error_message = str(exc)[:2000]
        finally:
            run.completed_at = datetime.now(UTC)
            await db.commit()


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")


async def _get_owned_run(run_id: uuid.UUID, user: AuthenticatedUser, db: AsyncSession) -> PipelineRun:
    """Scoped by `triggered_by == user.subject` — a run's UUID isn't
    guessable, but "not guessable" is not the same as "authorized," and
    genomic candidate-gene lists are the kind of pre-publication data a
    biotech/pharma caller has a real interest in keeping scoped to
    themselves (see docs/compliance-checklist.md).
    """
    run = await db.get(PipelineRun, run_id)
    if run is None or run.triggered_by != user.subject:
        # Same 404 whether the run doesn't exist or belongs to someone else —
        # a 403 here would confirm to an unauthorized caller that the run ID
        # exists at all.
        raise _not_found()
    return run


@router.post("", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    payload: RunCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
    supervisor: Supervisor = Depends(get_supervisor),
    settings: Settings = Depends(get_settings),
    user: AuthenticatedUser = Depends(get_current_user),
    rate_limiter: SlidingWindowRateLimiter = Depends(get_run_rate_limiter),
) -> PipelineRun:
    # Checked before touching the DB at all — a rejected request should cost
    # nothing, not even a row insert. See core/rate_limit.py for why this is
    # a first line of defense, not the final word, on runaway LLM spend.
    if not rate_limiter.allow(user.subject):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Rate limit exceeded: max {settings.run_rate_limit_max_calls} runs per "
                f"{int(settings.run_rate_limit_window_seconds)}s per caller."
            ),
        )

    run = PipelineRun(
        candidate_genes=payload.candidate_genes,
        status=RunStatus.RUNNING,
        stage_status_json={},
        triggered_by=user.subject,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    background_tasks.add_task(
        _execute_run,
        run.id,
        payload.candidate_genes,
        payload.top_n_for_experiments,
        supervisor,
        settings.run_timeout_seconds,
    )
    return run


@router.get("/{run_id}", response_model=RunOut)
async def get_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(get_current_user),
) -> PipelineRun:
    return await _get_owned_run(run_id, user, db)


@router.get("", response_model=list[RunOut])
async def list_runs(
    db: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(get_current_user),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[PipelineRun]:
    result = await db.execute(
        select(PipelineRun)
        .where(PipelineRun.triggered_by == user.subject)
        .order_by(PipelineRun.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(get_current_user),
) -> None:
    """Ties to docs/compliance-checklist.md's "right to deletion" item — even
    for today's candidate-gene-only entry point, there was previously no way
    to purge a run someone regrets submitting (e.g., pasted a real
    proprietary shortlist by mistake).
    """
    run = await _get_owned_run(run_id, user, db)
    await db.delete(run)
    await db.commit()
