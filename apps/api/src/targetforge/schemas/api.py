"""HTTP request/response DTOs, kept separate from the pipeline schemas (see
schemas/pipeline.py) — same rationale as TrialReady.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RunCreate(BaseModel):
    candidate_genes: list[str] = Field(min_length=1, max_length=25)
    top_n_for_experiments: int = Field(default=3, ge=1, le=10)


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    candidate_genes: list[str]
    status: str
    stage_status_json: dict
    report_json: dict | None
    error_message: str | None
    triggered_by: str
    created_at: datetime
    completed_at: datetime | None
