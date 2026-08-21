"""Integration test fixtures.

These tests run against a real Postgres (via `docker compose up -d` locally —
see docker-compose.yml, host port 5434 — or the `postgres` service container
in `.github/workflows/ci.yml`) — not SQLite. The schema uses Postgres-specific
types (JSONB, native ENUM, ARRAY, UUID) precisely because that's what
production runs on; a SQLite stand-in would validate a different database
than the one this system ships on, which defeats the point. Same rationale as
TrialReady's integration tests.
"""

from __future__ import annotations

import os

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from targetforge.db.models import Base

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://targetforge:targetforge@localhost:5434/targetforge"
)


@pytest_asyncio.fixture
async def engine():
    # Function-scoped, not session-scoped: asyncpg connections are bound to
    # the event loop they were created on, and pytest-asyncio gives each test
    # function its own event loop by default — see conftest note in
    # TrialReady's equivalent fixture for the exact failure mode this avoids.
    eng = create_async_engine(DATABASE_URL, future=True)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db_session(engine) -> AsyncSession:
    """One outer transaction + SAVEPOINT per test, rolled back afterward —
    SQLAlchemy 2.0's documented `join_transaction_mode="create_savepoint"`
    pattern, so tests never see each other's data.
    """
    async with engine.connect() as conn:
        await conn.begin()
        session_factory = async_sessionmaker(
            bind=conn,
            expire_on_commit=False,
            class_=AsyncSession,
            join_transaction_mode="create_savepoint",
        )
        async with session_factory() as session:
            yield session
        await conn.rollback()
