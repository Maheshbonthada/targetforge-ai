"""Agent 10 (Target Prioritization) against a fake LLM. Confirms the agent
re-sorts by rank rather than trusting the LLM's array order — the one
behavior in this agent worth locking down with a test, since a report with
targets out of rank order would be a visible, credibility-damaging bug.
"""

from __future__ import annotations

import pytest

from targetforge.agents.prioritization_agent import (
    TargetPrioritizationAgent,
    _PrioritizationResponse,
)
from targetforge.core.llm import FakeLlmClient
from targetforge.schemas.pipeline import (
    AssociationResult,
    CompetitiveLandscape,
    DruggabilityAssessment,
    DruggabilityTier,
    GeneCompetitiveLandscape,
    GeneDruggability,
    GeneLiteratureEvidence,
    LiteratureEvidence,
    PathwayEnrichmentResult,
    TargetScore,
)


def _empty_pipeline_inputs(genes: list[str]):
    association = AssociationResult(run_id="run-1", associations=[], candidate_genes=genes)
    pathways = PathwayEnrichmentResult(run_id="run-1", pathways=[])
    literature = LiteratureEvidence(
        run_id="run-1",
        genes=[
            GeneLiteratureEvidence(gene_symbol=g, paper_count=0, claims=[], known_drug_programs_mentioned=False)
            for g in genes
        ],
    )
    druggability = DruggabilityAssessment(
        run_id="run-1",
        genes=[
            GeneDruggability(gene_symbol=g, tier=DruggabilityTier.UNKNOWN, known_ligands=[], rationale="n/a")
            for g in genes
        ],
    )
    competitive = CompetitiveLandscape(
        run_id="run-1",
        genes=[GeneCompetitiveLandscape(gene_symbol=g, existing_programs=[], summary="none") for g in genes],
    )
    return association, pathways, literature, druggability, competitive


@pytest.mark.asyncio
async def test_targets_are_returned_sorted_by_rank_not_llm_array_order():
    genes = ["KRAS", "EGFR"]
    # Deliberately out of rank order in the queued response.
    llm_response = _PrioritizationResponse(
        targets=[
            TargetScore(
                gene_symbol="EGFR", rank=2, composite_score=60, genetic_evidence_score=50,
                druggability_score=80, novelty_score=30, rationale="Crowded but druggable.",
            ),
            TargetScore(
                gene_symbol="KRAS", rank=1, composite_score=90, genetic_evidence_score=90,
                druggability_score=85, novelty_score=60, rationale="Strong evidence, high novelty.",
            ),
        ]
    )
    llm = FakeLlmClient(responses=[llm_response])
    agent = TargetPrioritizationAgent(llm)

    ranking = await agent.run("run-1", *_empty_pipeline_inputs(genes))

    assert [t.gene_symbol for t in ranking.targets] == ["KRAS", "EGFR"]
    assert [t.rank for t in ranking.targets] == [1, 2]
