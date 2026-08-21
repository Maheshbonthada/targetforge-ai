"""Agent 7 (Literature Mining) against fakes — no network, no LLM API calls.
Exercises the two things that actually matter about this agent's contract:
it never fabricates a paper count the search didn't return, and it never
calls the LLM at all for a gene with zero hits (so a `FakeLlmClient` with no
queued responses left over is itself part of the assertion).
"""

from __future__ import annotations

import pytest

from targetforge.agents.literature_agent import LiteratureMiningAgent
from targetforge.core.llm import FakeLlmClient, LlmParseError
from targetforge.external.pubmed_client import FakePubMedClient, PubMedArticle
from targetforge.schemas.pipeline import CitedClaim, GeneLiteratureEvidence


@pytest.mark.asyncio
async def test_real_search_count_overrides_whatever_the_llm_says():
    articles = [
        PubMedArticle(pmid="11111111", title="KRAS in pancreatic cancer", abstract="KRAS mutations drive..."),
        PubMedArticle(pmid="22222222", title="Targeting KRAS G12D", abstract="A new inhibitor..."),
    ]
    pubmed = FakePubMedClient(articles=articles)

    # The LLM is deliberately fed a wrong paper_count (99) to prove the agent
    # overwrites it with the real search-result count rather than trusting
    # the model's arithmetic — see literature_agent.py's comment on this.
    llm = FakeLlmClient(
        responses=[
            GeneLiteratureEvidence(
                gene_symbol="KRAS",
                paper_count=99,
                claims=[CitedClaim(claim="KRAS mutations drive pancreatic cancer.", pmid="11111111")],
                known_drug_programs_mentioned=True,
            )
        ]
    )

    agent = LiteratureMiningAgent(llm, pubmed)
    result = await agent.run("run-1", ["KRAS"])

    assert len(result.genes) == 1
    gene = result.genes[0]
    assert gene.paper_count == 2  # real count from the search, not the LLM's 99
    assert gene.claims[0].pmid == "11111111"
    assert gene.known_drug_programs_mentioned is True


@pytest.mark.asyncio
async def test_gene_with_no_hits_gets_empty_evidence_and_never_calls_the_llm():
    pubmed = FakePubMedClient(articles=[])
    llm = FakeLlmClient(responses=[])  # any complete_json call here is a bug

    agent = LiteratureMiningAgent(llm, pubmed)
    result = await agent.run("run-1", ["ZZZFAKEGENE"])

    assert result.genes == [
        GeneLiteratureEvidence(
            gene_symbol="ZZZFAKEGENE", paper_count=0, claims=[], known_drug_programs_mentioned=False
        )
    ]


@pytest.mark.asyncio
async def test_processes_multiple_genes_in_order():
    pubmed = FakePubMedClient(articles=[PubMedArticle(pmid="1", title="t", abstract="a")])
    llm = FakeLlmClient(
        responses=[
            GeneLiteratureEvidence(gene_symbol="KRAS", paper_count=1, claims=[], known_drug_programs_mentioned=False),
            GeneLiteratureEvidence(gene_symbol="EGFR", paper_count=1, claims=[], known_drug_programs_mentioned=False),
        ]
    )

    agent = LiteratureMiningAgent(llm, pubmed)
    result = await agent.run("run-1", ["KRAS", "EGFR"])

    assert [g.gene_symbol for g in result.genes] == ["KRAS", "EGFR"]


@pytest.mark.asyncio
async def test_llm_parse_failure_propagates_as_llmparseerror():
    """No safe fallback exists for literature evidence (unlike TrialReady's
    confidence-gated classification) — a parse failure must surface, not
    silently produce an empty/wrong claim set.
    """
    pubmed = FakePubMedClient(articles=[PubMedArticle(pmid="1", title="t", abstract="a")])
    llm = FakeLlmClient(responses=[])  # empty queue forces LlmParseError on the one expected call

    agent = LiteratureMiningAgent(llm, pubmed)
    with pytest.raises(LlmParseError):
        await agent.run("run-1", ["KRAS"])
