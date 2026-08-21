"""Alignment job orchestration — submits BWA-MEM2 as an Azure Batch task per
sample. This module genuinely cannot run a real alignment in this
environment (needs a multi-GB reference genome index and real compute), so
`FakeAlignmentBackend` is what tests and local dev exercise. `AzureBatchAlignmentBackend`
is production-shaped real code — see docs/scaling-to-real-genomes.md for what
changes to point it at a real Batch account and reference.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from targetforge.schemas.pipeline import JobStatus


@dataclass
class AlignmentJobResult:
    sample_id: str
    status: JobStatus
    bam_blob_path: str | None
    alignment_rate: float | None
    mean_coverage: float | None


class AlignmentBackend(Protocol):
    async def align(self, sample_id: str, fastq_blob_paths: list[str], reference_build: str) -> AlignmentJobResult: ...


class AzureBatchAlignmentBackend:
    """Submits `bwa-mem2 mem <reference> <r1.fastq> <r2.fastq> | samtools sort`
    as a containerized Azure Batch task. The container image is defined in
    `infra/bicep/modules/batch-pool.bicep`'s task image reference; the
    reference genome is expected pre-staged on the pool's mounted Azure
    Files share (staging a ~3GB BWA index per-task would dominate runtime).
    """

    def __init__(self, batch_account_url: str, pool_id: str) -> None:
        self._batch_account_url = batch_account_url
        self._pool_id = pool_id

    async def align(self, sample_id: str, fastq_blob_paths: list[str], reference_build: str) -> AlignmentJobResult:
        from azure.batch.aio import BatchServiceClient
        from azure.batch.models import TaskAddParameter
        from azure.identity.aio import DefaultAzureCredential

        task_id = f"align-{sample_id}-{uuid.uuid4().hex[:8]}"
        command = (
            f"/bin/bash -c 'bwa-mem2 mem -t 8 /reference/{reference_build}.fa "
            f"{' '.join(fastq_blob_paths)} | samtools sort -o /output/{sample_id}.bam'"
        )

        async with DefaultAzureCredential() as credential, BatchServiceClient(
            credential=credential, batch_url=self._batch_account_url
        ) as client:
            await client.task.add(
                job_id=f"targetforge-align-{sample_id[:8]}",
                task=TaskAddParameter(id=task_id, command_line=command),
            )

        # In production this returns PENDING immediately; the Supervisor
        # polls task status separately (see orchestration/supervisor.py) —
        # alignment of a real sample takes 20-60+ minutes, far too long for a
        # synchronous request/response.
        return AlignmentJobResult(
            sample_id=sample_id, status=JobStatus.PENDING, bam_blob_path=None,
            alignment_rate=None, mean_coverage=None,
        )


class FakeAlignmentBackend:
    """Test double: returns a deterministic, plausible result instantly."""

    async def align(self, sample_id: str, fastq_blob_paths: list[str], reference_build: str) -> AlignmentJobResult:
        return AlignmentJobResult(
            sample_id=sample_id,
            status=JobStatus.SUCCEEDED,
            bam_blob_path=f"aligned/{sample_id}.bam",
            alignment_rate=0.97,
            mean_coverage=32.5,
        )
