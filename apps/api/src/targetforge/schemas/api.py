"""HTTP request/response DTOs, kept separate from the pipeline schemas (see
schemas/pipeline.py) — same rationale as TrialReady.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

# A real HGNC gene symbol is short (almost always <=15 chars) and drawn from a
# narrow alphabet. Bounding both length and character set here isn't about
# gene-symbol correctness (a typo'd real symbol should still pass through to
# the pipeline and fail gracefully downstream) — it's about refusing to embed
# an arbitrarily large or control-character-laden string into every LLM
# prompt this run will generate across up to 7 reasoning agents, which is
# both a cost blowup and a prompt-injection surface neither this schema nor
# any downstream agent is designed to defend against otherwise.
GeneSymbol = Annotated[str, StringConstraints(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9\-.]+$")]


class RunCreate(BaseModel):
    candidate_genes: list[GeneSymbol] = Field(min_length=1, max_length=25)
    top_n_for_experiments: int = Field(default=3, ge=1, le=10)

    @field_validator("candidate_genes", mode="before")
    @classmethod
    def _strip_whitespace(cls, genes: object) -> object:
        # Runs BEFORE pydantic validates each item against GeneSymbol's
        # pattern constraint — a real caller pasting "KRAS, EGFR, TP53"
        # (see the frontend's textarea placeholder) legitimately produces
        # surrounding whitespace per item, which is not itself invalid data,
        # just untrimmed. Trimming after the pattern check (the original,
        # buggy order here — caught by
        # tests/integration/test_runs_api.py::test_candidate_genes_are_normalized_and_deduped_server_side)
        # rejected exactly that legitimate input.
        if isinstance(genes, list):
            return [g.strip() if isinstance(g, str) else g for g in genes]
        return genes

    @field_validator("candidate_genes")
    @classmethod
    def _normalize_and_dedupe(cls, genes: list[str]) -> list[str]:
        # Normalize server-side rather than trusting the frontend's
        # `.toUpperCase()` (apps/web/src/pages/RunsListPage.tsx) — that's a
        # UX nicety, not a validation boundary; any other caller of this API
        # (curl, a future integration) gets the same normalization here.
        seen: dict[str, None] = {}
        for gene in genes:
            normalized = gene.upper()
            if normalized:
                seen[normalized] = None
        if not seen:
            raise ValueError("candidate_genes must contain at least one non-empty gene symbol")
        return list(seen)


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
