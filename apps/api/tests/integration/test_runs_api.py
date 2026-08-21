"""HTTP-router-layer tests — the layer nothing previously exercised. Every
other test in this repo either calls an agent directly or calls
`Supervisor.run_from_candidate_genes` directly; a regression in the FastAPI
wiring itself (a route not actually requiring auth, ownership filtering
missing from a query, a validation constraint not actually attached to the
schema) would have passed every other test in this suite. These tests hit
the real ASGI app over HTTP (`httpx.ASGITransport`), with the DB session
swapped for the SAVEPOINT-scoped fixture (see integration/conftest.py) and
the background pipeline execution stubbed out — this file is about the
request/response contract, not the pipeline itself (see
test_pipeline_run_flow.py for that).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio

from targetforge.core.rate_limit import SlidingWindowRateLimiter
from targetforge.core.security import AuthenticatedUser, get_current_user
from targetforge.db.models import PipelineRun, RunStatus
from targetforge.db.session import get_db_session
from targetforge.deps import get_run_rate_limiter, get_supervisor
from targetforge.main import app

_USER_A = AuthenticatedUser(subject="user-a", email="a@example.com", display_name="User A")
_USER_B = AuthenticatedUser(subject="user-b", email="b@example.com", display_name="User B")


@pytest.fixture(autouse=True)
def _stub_background_execution(monkeypatch):
    """The background task opens its own DB session via `get_session_factory()`
    (deliberately, in production — see runs.py's docstring on why), which
    means it runs on a different connection than this test's SAVEPOINT
    fixture and would never see the just-inserted row anyway. Stubbing it
    out keeps these tests about the HTTP contract, not an accidental,
    connection-boundary-crossing no-op.
    """
    monkeypatch.setattr("targetforge.api.routers.runs._execute_run", AsyncMock())


@pytest_asyncio.fixture
async def client(db_session):
    app.dependency_overrides[get_db_session] = lambda: db_session
    app.dependency_overrides[get_supervisor] = lambda: object()  # never called — execution is stubbed
    app.dependency_overrides[get_current_user] = lambda: _USER_A
    # get_run_rate_limiter is a process-wide @lru_cache singleton (see
    # deps.py) — sharing it across every test in this module (default: 5
    # calls/10min) would make later tests fail from rate-limit exhaustion
    # caused by earlier, unrelated tests, not by anything they're actually
    # checking. A fresh, generous limiter per test isolates that concern.
    # One shared instance per test (not a lambda constructing a new one per
    # call) — a limiter can only ever say "no" if it actually accumulates
    # call history across requests.
    generous_limiter = SlidingWindowRateLimiter(max_calls=1000, window_seconds=60)
    app.dependency_overrides[get_run_rate_limiter] = lambda: generous_limiter
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_rate_limit_returns_429_and_creates_no_row(client, db_session):
    # A lambda constructing a *new* limiter per call would never accumulate
    # any history across requests — one shared instance, returned by
    # reference, is what actually makes the second request see the first's
    # call.
    shared_limiter = SlidingWindowRateLimiter(max_calls=1, window_seconds=60)
    app.dependency_overrides[get_run_rate_limiter] = lambda: shared_limiter

    first = await client.post("/api/v1/runs", json={"candidate_genes": ["KRAS"]})
    assert first.status_code == 202

    second = await client.post("/api/v1/runs", json={"candidate_genes": ["EGFR"]})
    assert second.status_code == 429

    resp = await client.get("/api/v1/runs")
    # Only the first, accepted run exists — a rate-limited request must not
    # have inserted a row before being rejected.
    assert [r["candidate_genes"] for r in resp.json()] == [["KRAS"]]


async def test_create_run_returns_202_with_running_status(client):
    resp = await client.post("/api/v1/runs", json={"candidate_genes": ["KRAS"], "top_n_for_experiments": 2})
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "running"
    assert body["candidate_genes"] == ["KRAS"]
    assert body["triggered_by"] == "user-a"


async def test_candidate_genes_are_normalized_and_deduped_server_side(client):
    resp = await client.post("/api/v1/runs", json={"candidate_genes": [" kras ", "KRAS", "egfr"]})
    assert resp.status_code == 202
    assert resp.json()["candidate_genes"] == ["KRAS", "EGFR"]


async def test_oversized_gene_symbol_is_rejected(client):
    resp = await client.post("/api/v1/runs", json={"candidate_genes": ["K" * 21]})
    assert resp.status_code == 422


async def test_empty_gene_list_is_rejected(client):
    resp = await client.post("/api/v1/runs", json={"candidate_genes": []})
    assert resp.status_code == 422


async def test_gene_symbol_with_invalid_characters_is_rejected(client):
    resp = await client.post("/api/v1/runs", json={"candidate_genes": ["KRAS; DROP TABLE pipeline_runs;"]})
    assert resp.status_code == 422


async def test_owner_can_read_their_own_run(client):
    created = await client.post("/api/v1/runs", json={"candidate_genes": ["KRAS"]})
    run_id = created.json()["id"]

    resp = await client.get(f"/api/v1/runs/{run_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == run_id


async def test_a_different_user_gets_404_not_someone_elses_run(client, db_session):
    """The core ownership-isolation regression test: before this was wired
    up, any caller could read (and, before the delete-scoping fix, delete)
    any run regardless of who triggered it.
    """
    created = await client.post("/api/v1/runs", json={"candidate_genes": ["KRAS"]})
    run_id = created.json()["id"]

    app.dependency_overrides[get_current_user] = lambda: _USER_B
    resp = await client.get(f"/api/v1/runs/{run_id}")
    assert resp.status_code == 404

    delete_resp = await client.delete(f"/api/v1/runs/{run_id}")
    assert delete_resp.status_code == 404


async def test_list_runs_only_returns_the_caller_s_own_runs(client):
    await client.post("/api/v1/runs", json={"candidate_genes": ["KRAS"]})

    app.dependency_overrides[get_current_user] = lambda: _USER_B
    await client.post("/api/v1/runs", json={"candidate_genes": ["EGFR"]})

    resp = await client.get("/api/v1/runs")
    assert resp.status_code == 200
    genes_seen = [r["candidate_genes"] for r in resp.json()]
    assert ["EGFR"] in genes_seen
    assert ["KRAS"] not in genes_seen


async def test_list_runs_rejects_out_of_range_limit(client):
    assert (await client.get("/api/v1/runs", params={"limit": 0})).status_code == 422
    assert (await client.get("/api/v1/runs", params={"limit": 101})).status_code == 422


async def test_get_nonexistent_run_is_404(client):
    resp = await client.get(f"/api/v1/runs/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_delete_removes_the_run(client):
    created = await client.post("/api/v1/runs", json={"candidate_genes": ["KRAS"]})
    run_id = created.json()["id"]

    resp = await client.delete(f"/api/v1/runs/{run_id}")
    assert resp.status_code == 204

    assert (await client.get(f"/api/v1/runs/{run_id}")).status_code == 404


async def test_orphaned_run_reconciliation_marks_stale_running_rows_failed(engine):
    """`_reconcile_orphaned_runs` (main.py) reads via `get_session_factory()`
    — a real, committed connection, not this suite's usual SAVEPOINT
    fixture — so this test commits for real via the same `engine` fixture
    and cleans up explicitly afterward, rather than relying on rollback.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from targetforge.main import _reconcile_orphaned_runs

    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as setup_session:
        run = PipelineRun(
            candidate_genes=["KRAS"], status=RunStatus.RUNNING, stage_status_json={}, triggered_by="user-a"
        )
        setup_session.add(run)
        await setup_session.commit()
        run_id = run.id

    try:
        await _reconcile_orphaned_runs()

        async with session_factory() as verify_session:
            reconciled = await verify_session.get(PipelineRun, run_id)
            assert reconciled is not None
            assert reconciled.status == RunStatus.FAILED
            assert reconciled.error_message is not None
            assert "orphaned" in reconciled.error_message.lower()
    finally:
        async with session_factory() as cleanup_session:
            row = await cleanup_session.get(PipelineRun, run_id)
            if row is not None:
                await cleanup_session.delete(row)
                await cleanup_session.commit()
