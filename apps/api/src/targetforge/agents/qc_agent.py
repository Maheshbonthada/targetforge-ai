"""Agent 1 — QC. See docs/agents.md.

Pass/fail is a deterministic numeric rule (bioinformatics/fastq_qc.py); the
LLM's only job is turning the per-sample flag list into one readable
cohort-level summary paragraph.
"""

from __future__ import annotations

from pydantic import BaseModel

from targetforge.bioinformatics.fastq_qc import (
    compute_metrics,
    evaluate_pass_fail,
    parse_fastq_records,
)
from targetforge.core.llm import LlmClient
from targetforge.schemas.pipeline import QcResult, SampleQc

_SYSTEM_PROMPT = """You are a sequencing QC analyst. You will be given
per-sample pass/fail results and flags for a sequencing run. Write one
short paragraph summarizing the cohort's QC status for a non-bioinformatician
program lead — how many samples are fine, which ones need attention and why.
Do not invent any numbers not present in the data.

Respond with ONLY a JSON object matching exactly: {"summary": string}
"""


class _SummaryResponse(BaseModel):
    summary: str


class QcAgent:
    def __init__(self, llm: LlmClient) -> None:
        self._llm = llm

    async def run(self, run_id: str, fastq_by_sample: dict[str, str]) -> QcResult:
        """`fastq_by_sample` maps sample_id -> raw FASTQ text content."""
        samples: list[SampleQc] = []
        for sample_id, fastq_text in fastq_by_sample.items():
            records = parse_fastq_records(fastq_text)
            metrics = compute_metrics(records)
            passed, flags = evaluate_pass_fail(metrics)
            samples.append(
                SampleQc(
                    sample_id=sample_id,
                    total_reads=metrics.total_reads,
                    q30_rate=metrics.q30_rate,
                    gc_content=metrics.gc_content,
                    mean_read_length=metrics.mean_read_length,
                    adapter_contamination_pct=metrics.adapter_contamination_pct,
                    passed=passed,
                    flags=flags,
                )
            )

        flags_text = "\n".join(
            f"- {s.sample_id}: {'PASS' if s.passed else 'FAIL'}"
            + (f" ({'; '.join(s.flags)})" if s.flags else "")
            for s in samples
        )
        result = await self._llm.complete_json(
            _SYSTEM_PROMPT, f"Per-sample QC results:\n{flags_text}", _SummaryResponse
        )

        return QcResult(
            run_id=run_id,
            samples=samples,
            summary=result.summary,
            samples_passed=sum(1 for s in samples if s.passed),
            samples_failed=sum(1 for s in samples if not s.passed),
        )
