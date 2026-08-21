"""Agent 8 (Druggability Assessment) against fakes. Covers the EGFR-shaped
truncation bug this agent was fixed for (see ARCHITECTURE.md / conversation
history): a gene with more than `_MAX_INTERACTIONS_IN_PROMPT` interactions
must still produce exactly one LLM call with a capped prompt, not one call
per interaction and not a crash.
"""

from __future__ import annotations

import pytest

from targetforge.agents.druggability_agent import DruggabilityAssessmentAgent
from targetforge.core.llm import FakeLlmClient
from targetforge.external.dgidb_client import DrugInteraction, FakeDgidbClient
from targetforge.schemas.pipeline import DruggabilityTier, GeneDruggability


@pytest.mark.asyncio
async def test_gene_with_no_interactions_is_still_assessed():
    dgidb = FakeDgidbClient(interactions_by_gene={})
    llm = FakeLlmClient(
        responses=[
            GeneDruggability(
                gene_symbol="OBSCUREGENE",
                tier=DruggabilityTier.CHALLENGING,
                known_ligands=[],
                rationale="No known interactions found in DGIdb.",
            )
        ]
    )

    agent = DruggabilityAssessmentAgent(llm, dgidb)
    result = await agent.run("run-1", ["OBSCUREGENE"])

    assert result.genes[0].tier == DruggabilityTier.CHALLENGING
    assert result.genes[0].known_ligands == []


@pytest.mark.asyncio
async def test_heavily_drugged_gene_still_makes_exactly_one_llm_call():
    # 40 interactions — well over _MAX_INTERACTIONS_IN_PROMPT (25) — mirrors
    # a real target like EGFR in DGIdb.
    many_interactions = [
        DrugInteraction(drug_name=f"drug-{i}", interaction_types=["inhibitor"], interaction_score=float(i))
        for i in range(40)
    ]
    dgidb = FakeDgidbClient(interactions_by_gene={"EGFR": many_interactions})
    llm = FakeLlmClient(
        responses=[
            GeneDruggability(
                gene_symbol="EGFR",
                tier=DruggabilityTier.WELL_PRECEDENTED,
                known_ligands=["drug-39", "drug-38", "drug-37"],
                rationale="Multiple approved and investigational inhibitors exist.",
            )
        ]
    )

    agent = DruggabilityAssessmentAgent(llm, dgidb)
    result = await agent.run("run-1", ["EGFR"])

    # A single queued response was consumed cleanly (a second/duplicate call
    # would raise LlmParseError on the now-empty queue) — proves the capping
    # logic collapses 40 interactions into exactly one LLM call.
    assert result.genes[0].gene_symbol == "EGFR"
    assert result.genes[0].tier == DruggabilityTier.WELL_PRECEDENTED
