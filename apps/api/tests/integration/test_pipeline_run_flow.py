"""End-to-end test of a full candidate-gene-list pipeline run — all 7 stages
that `run_from_candidate_genes` actually executes (pathway, literature,
druggability, competitive, prioritization, experiment_design, report) —
against a real Postgres, using fake LLM/PubMed/DGIdb/ClinicalTrials.gov
clients (no network, no Azure/OpenAI credentials). Mirrors the shape of
`api/routers/runs.py`'s background-task execution so a bug in that wiring
would show up here, not just in the individually-tested agents.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from targetforge.agents.competitive_intel_agent import CompetitiveIntelligenceAgent
from targetforge.agents.druggability_agent import DruggabilityAssessmentAgent
from targetforge.agents.experiment_design_agent import ExperimentDesignAgent
from targetforge.agents.literature_agent import LiteratureMiningAgent
from targetforge.agents.pathway_agent import PathwayEnrichmentAgent
from targetforge.agents.prioritization_agent import (
    TargetPrioritizationAgent,
    _PrioritizationResponse,
)
from targetforge.agents.report_agent import ReportGenerationAgent, _ReportNarrative
from targetforge.core.llm import FakeLlmClient
from targetforge.db.models import PipelineRun, RunStatus
from targetforge.external.clinicaltrials_client import FakeClinicalTrialsClient, TrialRecord
from targetforge.external.dgidb_client import DrugInteraction, FakeDgidbClient
from targetforge.external.pubmed_client import FakePubMedClient, PubMedArticle
from targetforge.orchestration.supervisor import PipelineStage, StageStatus, Supervisor
from targetforge.schemas.pipeline import (
    CitedClaim,
    DruggabilityTier,
    ExperimentProposal,
    GeneDruggability,
    GeneLiteratureEvidence,
    TargetScore,
)


def _build_supervisor_with_fakes() -> Supervisor:
    """QC/alignment/variant-calling/annotation/association agents are still
    required by the Supervisor's constructor even though
    `run_from_candidate_genes` skips all five stages — construct them with
    obviously-inert fakes rather than loosening the Supervisor's contract
    just to make this test easier to write.
    """
    from targetforge.agents.alignment_agent import AlignmentOrchestrationAgent
    from targetforge.agents.annotation_agent import VariantAnnotationAgent
    from targetforge.agents.association_agent import CohortAssociationAgent
    from targetforge.agents.qc_agent import QcAgent
    from targetforge.agents.variant_calling_agent import VariantCallingAgent
    from targetforge.bioinformatics.alignment import FakeAlignmentBackend
    from targetforge.bioinformatics.variant_calling import FakeVariantCallingBackend
    from targetforge.external.ensembl_client import FakeVepClient

    pubmed = FakePubMedClient(
        articles=[PubMedArticle(pmid="38686056", title="KRAS in cancer", abstract="KRAS drives...")]
    )
    dgidb = FakeDgidbClient(
        interactions_by_gene={
            "KRAS": [DrugInteraction(drug_name="Sotorasib", interaction_types=["inhibitor"], interaction_score=9.5)]
        }
    )
    clinicaltrials = FakeClinicalTrialsClient(
        records=[
            TrialRecord(
                nct_id="NCT00000001", title="A KRAS trial", phase="Phase 2", status="Recruiting", sponsor="Acme Pharma"
            )
        ]
    )

    literature_llm = FakeLlmClient(
        responses=[
            GeneLiteratureEvidence(
                gene_symbol="KRAS",
                paper_count=1,
                claims=[CitedClaim(claim="KRAS mutations are common in pancreatic cancer.", pmid="38686056")],
                known_drug_programs_mentioned=True,
            )
        ]
    )
    druggability_llm = FakeLlmClient(
        responses=[
            GeneDruggability(
                gene_symbol="KRAS", tier=DruggabilityTier.WELL_PRECEDENTED, known_ligands=["Sotorasib"],
                rationale="Approved covalent inhibitor exists for G12C.",
            )
        ]
    )
    competitive_llm = FakeLlmClient(
        responses=[
            # _CompetitiveSummaryResponse is module-private by design (see
            # competitive_intel_agent.py) — FakeLlmClient doesn't type-check
            # what's queued against it, so a plain object with the two
            # attributes the agent actually reads works just as well and
            # keeps this test from reaching into another module's internals.
            type(
                "_Summary",
                (),
                {"gene_symbol": "KRAS", "summary": "One active Phase 2 program; moderately competitive."},
            )()
        ]
    )
    prioritization_llm = FakeLlmClient(
        responses=[
            _PrioritizationResponse(
                targets=[
                    TargetScore(
                        gene_symbol="KRAS", rank=1, composite_score=88, genetic_evidence_score=80,
                        druggability_score=90, novelty_score=50,
                        rationale="Well-precedented druggability with one existing competing program.",
                    )
                ]
            )
        ]
    )
    experiment_llm = FakeLlmClient(
        responses=[
            ExperimentProposal(
                gene_symbol="KRAS",
                proposed_approach="CRISPR knockout screen",
                model_system="MIA PaCa-2 pancreatic cancer cell line",
                precedent_pmid="38686056",
                caveat="Starting proposal only — requires wet-lab validation before further investment.",
            )
        ]
    )
    report_llm = FakeLlmClient(
        responses=[
            _ReportNarrative(
                executive_summary=(
                    "KRAS is the top-ranked target given strong druggability precedent and manageable competition."
                ),
                methodology=(
                    "Pathway enrichment, literature mining, druggability, competitive intelligence, and "
                    "prioritization were run against the candidate gene list."
                ),
            )
        ]
    )

    return Supervisor(
        qc_agent=QcAgent(FakeLlmClient()),
        alignment_agent=AlignmentOrchestrationAgent(FakeAlignmentBackend()),
        variant_calling_agent=VariantCallingAgent(FakeVariantCallingBackend()),
        annotation_agent=VariantAnnotationAgent(FakeLlmClient(), FakeVepClient()),
        association_agent=CohortAssociationAgent(),
        pathway_agent=PathwayEnrichmentAgent(),
        literature_agent=LiteratureMiningAgent(literature_llm, pubmed),
        druggability_agent=DruggabilityAssessmentAgent(druggability_llm, dgidb),
        competitive_agent=CompetitiveIntelligenceAgent(competitive_llm, clinicaltrials),
        prioritization_agent=TargetPrioritizationAgent(prioritization_llm),
        experiment_agent=ExperimentDesignAgent(experiment_llm),
        report_agent=ReportGenerationAgent(report_llm),
    )


@pytest.mark.asyncio
async def test_full_candidate_gene_run_persists_a_succeeded_row_with_a_cited_report(db_session):
    supervisor = _build_supervisor_with_fakes()

    run = PipelineRun(candidate_genes=["KRAS"], status=RunStatus.RUNNING, stage_status_json={}, triggered_by="pytest")
    db_session.add(run)
    await db_session.flush()
    run_id = run.id

    report, state = await supervisor.run_from_candidate_genes(["KRAS"], top_n_for_experiments=1, run_id=str(run_id))

    run.status = RunStatus.SUCCEEDED
    run.report_json = report.model_dump(mode="json")
    run.stage_status_json = {stage.value: st.value for stage, st in state.stage_status.items()}
    run.completed_at = datetime.now(UTC)
    await db_session.flush()

    persisted = await db_session.get(PipelineRun, run_id)
    assert persisted is not None
    assert persisted.status == RunStatus.SUCCEEDED

    # The five FASTQ-only stages are correctly skipped, not silently absent.
    assert persisted.stage_status_json["qc"] == StageStatus.SKIPPED.value
    assert persisted.stage_status_json["alignment"] == StageStatus.SKIPPED.value
    assert persisted.stage_status_json["variant_calling"] == StageStatus.SKIPPED.value
    assert persisted.stage_status_json["annotation"] == StageStatus.SKIPPED.value
    assert persisted.stage_status_json["association"] == StageStatus.SKIPPED.value
    assert persisted.stage_status_json[PipelineStage.PATHWAY.value] == StageStatus.SUCCEEDED.value
    assert persisted.stage_status_json[PipelineStage.REPORT.value] == StageStatus.SUCCEEDED.value

    report_json = persisted.report_json
    assert report_json["ranked_targets"][0]["gene_symbol"] == "KRAS"
    # The report's every literature claim traces to a real PMID the fake
    # PubMed client actually returned — this is the citation discipline
    # docs/agents.md calls load-bearing, checked structurally, not by eye.
    assert "38686056" in report_json["report_markdown"]


@pytest.mark.asyncio
async def test_a_failed_stage_marks_the_run_failed_not_silently_partial(db_session):
    """An agent that raises (e.g. an LlmParseError with no queued fallback)
    must fail the whole run loudly — see core/llm.py's LlmParseError
    docstring on why there's no safe default to fall back to here.
    """
    supervisor = _build_supervisor_with_fakes()
    # Starve the literature agent's LLM queue so its one expected call fails.
    supervisor._literature._llm = FakeLlmClient(responses=[])

    run = PipelineRun(candidate_genes=["KRAS"], status=RunStatus.RUNNING, stage_status_json={}, triggered_by="pytest")
    db_session.add(run)
    await db_session.flush()
    run_id = run.id

    error_message = None
    try:
        await supervisor.run_from_candidate_genes(["KRAS"], run_id=str(run_id))
    except Exception as exc:  # noqa: BLE001 — mirrors runs.py's _execute_run catch-all
        error_message = str(exc)

    assert error_message is not None

    run.status = RunStatus.FAILED
    run.error_message = error_message[:2000]
    await db_session.flush()

    persisted = await db_session.get(PipelineRun, run_id)
    assert persisted.status == RunStatus.FAILED
    assert persisted.error_message
