"""Variant calling orchestration — GATK HaplotypeCaller per sample, then
GenotypeGVCFs for joint genotyping across the cohort, as containerized Azure
Batch tasks. Same real-orchestration-code/fake-execution split as
alignment.py — see that module's docstring and
docs/scaling-to-real-genomes.md.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from targetforge.schemas.pipeline import JobStatus


@dataclass
class VariantCallingJobResult:
    cohort_vcf_blob_path: str | None
    total_variants: int
    ti_tv_ratio: float
    status: JobStatus


class VariantCallingBackend(Protocol):
    async def call_cohort(
        self, run_id: str, bam_blob_paths: list[str], reference_build: str
    ) -> VariantCallingJobResult: ...


class AzureBatchVariantCallingBackend:
    """Two-phase GATK workflow: HaplotypeCaller in GVCF mode per sample
    (parallelizable, one Batch task each), then a single GenotypeGVCFs task
    joint-genotyping the cohort. The per-sample fan-out/fan-in is exactly the
    kind of thing the Supervisor's state machine exists to track — see
    orchestration/supervisor.py.
    """

    def __init__(self, batch_account_url: str, pool_id: str) -> None:
        self._batch_account_url = batch_account_url
        self._pool_id = pool_id

    async def call_cohort(
        self, run_id: str, bam_blob_paths: list[str], reference_build: str
    ) -> VariantCallingJobResult:
        from azure.batch.aio import BatchServiceClient
        from azure.batch.models import TaskAddParameter
        from azure.identity.aio import DefaultAzureCredential

        job_id = f"targetforge-variantcall-{run_id[:8]}"
        gvcf_paths = []
        async with DefaultAzureCredential() as credential, BatchServiceClient(
            credential=credential, batch_url=self._batch_account_url
        ) as client:
            for bam_path in bam_blob_paths:
                sample_id = bam_path.rsplit("/", 1)[-1].removesuffix(".bam")
                gvcf_path = f"variants/{sample_id}.g.vcf.gz"
                gvcf_paths.append(gvcf_path)
                command = (
                    f"gatk HaplotypeCaller -R /reference/{reference_build}.fa "
                    f"-I {bam_path} -O {gvcf_path} -ERC GVCF"
                )
                await client.task.add(
                    job_id=job_id,
                    task=TaskAddParameter(id=f"hc-{sample_id}-{uuid.uuid4().hex[:6]}", command_line=command),
                )

            joint_genotype_cmd = (
                f"gatk GenotypeGVCFs -R /reference/{reference_build}.fa "
                f"{' '.join(f'-V {p}' for p in gvcf_paths)} -O variants/{run_id}.cohort.vcf.gz"
            )
            await client.task.add(
                job_id=job_id,
                task=TaskAddParameter(id=f"joint-genotype-{run_id[:8]}", command_line=joint_genotype_cmd),
            )

        return VariantCallingJobResult(
            cohort_vcf_blob_path=None, total_variants=0, ti_tv_ratio=0.0, status=JobStatus.PENDING
        )


class FakeVariantCallingBackend:
    async def call_cohort(
        self, run_id: str, bam_blob_paths: list[str], reference_build: str
    ) -> VariantCallingJobResult:
        return VariantCallingJobResult(
            cohort_vcf_blob_path=f"variants/{run_id}.cohort.vcf.gz",
            total_variants=4_200 * len(bam_blob_paths),
            ti_tv_ratio=2.05,  # ~2.0-2.1 is the expected sanity-check range for human WGS
            status=JobStatus.SUCCEEDED,
        )
