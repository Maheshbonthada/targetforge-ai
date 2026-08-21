"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-21
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    run_status = postgresql.ENUM("running", "succeeded", "failed", name="run_status")
    run_status.create(op.get_bind())

    op.create_table(
        "pipeline_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("candidate_genes", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column(
            "status", postgresql.ENUM(name="run_status", create_type=False), nullable=False, server_default="running"
        ),
        sa.Column("stage_status_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("report_json", postgresql.JSONB(), nullable=True),
        sa.Column("error_message", sa.String(2000), nullable=True),
        sa.Column("triggered_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_pipeline_runs_created_at", "pipeline_runs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_pipeline_runs_created_at", table_name="pipeline_runs")
    op.drop_table("pipeline_runs")
    postgresql.ENUM(name="run_status").drop(op.get_bind())
