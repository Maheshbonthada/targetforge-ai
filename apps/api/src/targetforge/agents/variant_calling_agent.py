"""Agent 3 — Variant Calling. See docs/agents.md. No LLM — orchestrates GATK
(bioinformatics/variant_calling.py)."""

from __future__ import annotations

from targetforge.bioinformatics.variant_calling import VariantCallingBackend
from targetforge.schemas.pipeline import VariantCallResult


class VariantCallingAgent:
    def __init__(self, backend: VariantCallingBackend) -> None:
        self._backend = backend

    async def run(self, run_id: str, bam_blob_paths: list[str], reference_build: str) -> VariantCallResult:
        result = await self._backend.call_cohort(run_id, bam_blob_paths, reference_build)
        return VariantCallResult(
            run_id=run_id,
            cohort_vcf_blob_path=result.cohort_vcf_blob_path or "",
            total_variants=result.total_variants,
            ti_tv_ratio=result.ti_tv_ratio,
            status=result.status,
        )
