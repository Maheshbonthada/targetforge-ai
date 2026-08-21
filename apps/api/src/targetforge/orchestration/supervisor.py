"""Agent 13 — Supervisor. See docs/agents.md.

Coordinates every handoff between the 12 pipeline agents and persists
per-stage status so a run is inspectable and partially resumable, not a
black box between "upload" and "report." Makes no scientific judgment of its
own — if it started doing that, it would be a reasoning agent wearing an
orchestration hat, exactly the scope-blur docs/agents.md argues against.

Two entry points, matching the two ways a team actually starts:
- `run_from_candidate_genes`: skip sequencing entirely — a team that already
  has a gene list (from their own analysis, a public GWAS, whatever) runs
  stages 6-12 directly. This is the fully real, fully testable path today.
- `run_full_from_fastq`: the complete 1-13 pipeline. Stages 2-3 (alignment,
  variant calling) require real compute this environment doesn't have — see
  bioinformatics/alignment.py and variant_calling.py's Fake vs. Azure Batch
  backends, and docs/scaling-to-real-genomes.md.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

import structlog

from targetforge.agents.alignment_agent import AlignmentOrchestrationAgent
from targetforge.agents.annotation_agent import VariantAnnotationAgent
from targetforge.agents.association_agent import CohortAssociationAgent
from targetforge.agents.competitive_intel_agent import CompetitiveIntelligenceAgent
from targetforge.agents.druggability_agent import DruggabilityAssessmentAgent
from targetforge.agents.experiment_design_agent import ExperimentDesignAgent
from targetforge.agents.literature_agent import LiteratureMiningAgent
from targetforge.agents.pathway_agent import PathwayEnrichmentAgent
from targetforge.agents.prioritization_agent import TargetPrioritizationAgent
from targetforge.agents.qc_agent import QcAgent
from targetforge.agents.report_agent import ReportGenerationAgent
from targetforge.agents.variant_calling_agent import VariantCallingAgent
from targetforge.bioinformatics.association_stats import VariantCounts
from targetforge.schemas.pipeline import AssociationResult, CalledVariant, GenomicsReport

logger = structlog.get_logger(__name__)


class PipelineStage(str, Enum):
    QC = "qc"
    ALIGNMENT = "alignment"
    VARIANT_CALLING = "variant_calling"
    ANNOTATION = "annotation"
    ASSOCIATION = "association"
    PATHWAY = "pathway"
    LITERATURE = "literature"
    DRUGGABILITY = "druggability"
    COMPETITIVE = "competitive"
    PRIORITIZATION = "prioritization"
    EXPERIMENT_DESIGN = "experiment_design"
    REPORT = "report"


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class RunState:
    run_id: str
    started_at: str
    stage_status: dict[PipelineStage, StageStatus] = field(default_factory=dict)
    stage_error: dict[PipelineStage, str] = field(default_factory=dict)

    def mark(self, stage: PipelineStage, status: StageStatus, error: str | None = None) -> None:
        self.stage_status[stage] = status
        if error:
            self.stage_error[stage] = error
        logger.info("pipeline.stage", run_id=self.run_id, stage=stage.value, status=status.value)


class Supervisor:
    def __init__(
        self,
        *,
        qc_agent: QcAgent,
        alignment_agent: AlignmentOrchestrationAgent,
        variant_calling_agent: VariantCallingAgent,
        annotation_agent: VariantAnnotationAgent,
        association_agent: CohortAssociationAgent,
        pathway_agent: PathwayEnrichmentAgent,
        literature_agent: LiteratureMiningAgent,
        druggability_agent: DruggabilityAssessmentAgent,
        competitive_agent: CompetitiveIntelligenceAgent,
        prioritization_agent: TargetPrioritizationAgent,
        experiment_agent: ExperimentDesignAgent,
        report_agent: ReportGenerationAgent,
    ) -> None:
        self._qc = qc_agent
        self._alignment = alignment_agent
        self._variant_calling = variant_calling_agent
        self._annotation = annotation_agent
        self._association = association_agent
        self._pathway = pathway_agent
        self._literature = literature_agent
        self._druggability = druggability_agent
        self._competitive = competitive_agent
        self._prioritization = prioritization_agent
        self._experiment = experiment_agent
        self._report = report_agent

    async def run_from_candidate_genes(
        self, candidate_genes: list[str], *, top_n_for_experiments: int = 3, run_id: str | None = None
    ) -> tuple[GenomicsReport, RunState]:
        run_id = run_id or str(uuid.uuid4())
        state = RunState(run_id=run_id, started_at=datetime.now(UTC).isoformat())

        skipped_stages = (
            PipelineStage.QC,
            PipelineStage.ALIGNMENT,
            PipelineStage.VARIANT_CALLING,
            PipelineStage.ANNOTATION,
            PipelineStage.ASSOCIATION,
        )
        for skipped in skipped_stages:
            state.mark(skipped, StageStatus.SKIPPED)

        pathways = await self._run_stage(state, PipelineStage.PATHWAY, self._pathway.run, run_id, candidate_genes)
        literature = await self._run_stage(
            state, PipelineStage.LITERATURE, self._literature.run, run_id, candidate_genes
        )
        druggability = await self._run_stage(
            state, PipelineStage.DRUGGABILITY, self._druggability.run, run_id, candidate_genes
        )
        competitive = await self._run_stage(
            state, PipelineStage.COMPETITIVE, self._competitive.run, run_id, candidate_genes
        )

        # Prioritization needs an AssociationResult; a candidate-genes-only run
        # has no genetic association data, so we construct an empty-but-valid
        # one (every gene present, no statistical signal to report) rather
        # than special-casing the prioritization agent's contract for this
        # entry point.
        empty_association = AssociationResult(run_id=run_id, associations=[], candidate_genes=candidate_genes)

        ranking = await self._run_stage(
            state, PipelineStage.PRIORITIZATION, self._prioritization.run,
            run_id, empty_association, pathways, literature, druggability, competitive,
        )

        literature_by_gene = {g.gene_symbol: g for g in literature.genes}
        top_targets = ranking.targets[:top_n_for_experiments]
        experiments = await self._run_stage(
            state, PipelineStage.EXPERIMENT_DESIGN, self._experiment.run, run_id, top_targets, literature_by_gene
        )

        report = await self._run_stage(
            state,
            PipelineStage.REPORT,
            self._report.run,
            run_id,
            ranking,
            literature,
            druggability,
            competitive,
            experiments,
            "candidate-gene-list (no reference build)",
        )

        return report, state

    async def run_full_from_fastq(
        self,
        fastq_by_sample: dict[str, str],
        case_control_labels: dict[str, str],
        variant_counts: list[VariantCounts],
        called_variants: list[CalledVariant],
        reference_build: str = "GRCh38",
        *,
        run_id: str | None = None,
        top_n_for_experiments: int = 3,
    ) -> tuple[GenomicsReport, RunState]:
        """The complete 1-13 pipeline. `variant_counts` and `called_variants`
        are accepted directly (rather than derived from the alignment/
        variant-calling stages' real output) because those stages return
        PENDING against the real Azure Batch backend in this environment —
        see bioinformatics/alignment.py's docstring. Wiring stages 3->4->5
        together from a real cohort VCF is a parsing step
        (docs/scaling-to-real-genomes.md), not an orchestration change.
        """
        run_id = run_id or str(uuid.uuid4())
        state = RunState(run_id=run_id, started_at=datetime.now(UTC).isoformat())

        await self._run_stage(state, PipelineStage.QC, self._qc.run, run_id, fastq_by_sample)

        fastq_paths_by_sample = {s: [f"{s}_R1.fastq", f"{s}_R2.fastq"] for s in fastq_by_sample}
        await self._run_stage(
            state, PipelineStage.ALIGNMENT, self._alignment.run, run_id, fastq_paths_by_sample, reference_build
        )

        bam_paths = [f"aligned/{s}.bam" for s in fastq_by_sample]
        await self._run_stage(
            state, PipelineStage.VARIANT_CALLING, self._variant_calling.run, run_id, bam_paths, reference_build
        )

        await self._run_stage(state, PipelineStage.ANNOTATION, self._annotation.run, run_id, called_variants)

        association = await self._run_stage(
            state, PipelineStage.ASSOCIATION, self._association.run, run_id, variant_counts
        )
        pathways = await self._run_stage(
            state, PipelineStage.PATHWAY, self._pathway.run, run_id, association.candidate_genes
        )
        literature = await self._run_stage(
            state, PipelineStage.LITERATURE, self._literature.run, run_id, association.candidate_genes
        )
        druggability = await self._run_stage(
            state, PipelineStage.DRUGGABILITY, self._druggability.run, run_id, association.candidate_genes
        )
        competitive = await self._run_stage(
            state, PipelineStage.COMPETITIVE, self._competitive.run, run_id, association.candidate_genes
        )

        ranking = await self._run_stage(
            state, PipelineStage.PRIORITIZATION, self._prioritization.run,
            run_id, association, pathways, literature, druggability, competitive,
        )

        literature_by_gene = {g.gene_symbol: g for g in literature.genes}
        top_targets = ranking.targets[:top_n_for_experiments]
        experiments = await self._run_stage(
            state, PipelineStage.EXPERIMENT_DESIGN, self._experiment.run, run_id, top_targets, literature_by_gene
        )

        report = await self._run_stage(
            state, PipelineStage.REPORT, self._report.run,
            run_id, ranking, literature, druggability, competitive, experiments, reference_build,
        )

        return report, state

    async def _run_stage(self, state: RunState, stage: PipelineStage, fn, *args):
        state.mark(stage, StageStatus.RUNNING)
        try:
            result = fn(*args)
            if hasattr(result, "__await__"):
                result = await result
        except Exception as exc:
            state.mark(stage, StageStatus.FAILED, error=str(exc))
            raise
        state.mark(stage, StageStatus.SUCCEEDED)
        return result
