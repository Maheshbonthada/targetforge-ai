"""Agent 2 — Alignment Orchestration. See docs/agents.md. No LLM involved —
submits and tracks BWA-MEM2 jobs (bioinformatics/alignment.py)."""

from __future__ import annotations

from targetforge.bioinformatics.alignment import AlignmentBackend
from targetforge.schemas.pipeline import AlignmentResult, SampleAlignment


class AlignmentOrchestrationAgent:
    def __init__(self, backend: AlignmentBackend) -> None:
        self._backend = backend

    async def run(
        self, run_id: str, fastq_paths_by_sample: dict[str, list[str]], reference_build: str
    ) -> AlignmentResult:
        samples: list[SampleAlignment] = []
        for sample_id, fastq_paths in fastq_paths_by_sample.items():
            result = await self._backend.align(sample_id, fastq_paths, reference_build)
            samples.append(
                SampleAlignment(
                    sample_id=sample_id,
                    bam_blob_path=result.bam_blob_path,
                    alignment_rate=result.alignment_rate,
                    mean_coverage=result.mean_coverage,
                    status=result.status,
                )
            )
        return AlignmentResult(run_id=run_id, reference_build=reference_build, samples=samples)
