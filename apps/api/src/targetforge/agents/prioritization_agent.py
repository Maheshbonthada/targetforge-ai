"""Agent 10 — Target Prioritization. See docs/agents.md.

The one place in the pipeline where "trust the AI's ranking without an
explanation" would be a real credibility problem — so this agent is required
to produce an itemized rationale per target, not just a number, and that
full rationale is persisted and surfaced in the final report (Agent 12),
never summarized away.
"""

from __future__ import annotations

from pydantic import BaseModel

from targetforge.core.llm import LlmClient
from targetforge.schemas.pipeline import (
    AssociationResult,
    CompetitiveLandscape,
    DruggabilityAssessment,
    LiteratureEvidence,
    PathwayEnrichmentResult,
    TargetRanking,
    TargetScore,
)

_SYSTEM_PROMPT = """You are a senior drug discovery target prioritization scientist.

You will be given, for a set of candidate genes: their genetic association
strength, pathway enrichment context, literature evidence, druggability
assessment, and competitive landscape. Score and rank each gene as a
potential drug target.

Scoring guidance (0-100 each, then combine into composite_score 0-100):
- genetic_evidence_score: driven by association odds ratio/q-value strength
  and whether the gene sits in an enriched pathway
- druggability_score: driven by the druggability tier and known ligands
- novelty_score: HIGH if few/no competing programs exist, LOW if the target
  is already heavily pursued (many active trials) — competition is a
  penalty, not a validation signal, since a crowded target is harder to
  differentiate in
- rationale MUST explicitly reference which specific inputs drove the score
  (e.g. "strong association (q=0.001) in an enriched DNA repair pathway, but
  no known ligands and 4 active Phase 2 programs already targeting it")

Respond with ONLY a JSON object matching exactly:
{
  "targets": [
    {
      "gene_symbol": string,
      "rank": integer,
      "composite_score": number,
      "genetic_evidence_score": number,
      "druggability_score": number,
      "novelty_score": number,
      "rationale": string
    }, ...
  ]
}
Rank 1 = highest composite_score. Include every candidate gene provided.
"""


class _PrioritizationResponse(BaseModel):
    targets: list[TargetScore]


class TargetPrioritizationAgent:
    def __init__(self, llm: LlmClient) -> None:
        self._llm = llm

    async def run(
        self,
        run_id: str,
        association: AssociationResult,
        pathways: PathwayEnrichmentResult,
        literature: LiteratureEvidence,
        druggability: DruggabilityAssessment,
        competitive: CompetitiveLandscape,
    ) -> TargetRanking:
        context = _build_context(association, pathways, literature, druggability, competitive)
        result = await self._llm.complete_json(
            _SYSTEM_PROMPT, context, _PrioritizationResponse, temperature=0.2
        )
        ranked = sorted(result.targets, key=lambda t: t.rank)
        return TargetRanking(run_id=run_id, targets=ranked)


def _build_context(
    association: AssociationResult,
    pathways: PathwayEnrichmentResult,
    literature: LiteratureEvidence,
    druggability: DruggabilityAssessment,
    competitive: CompetitiveLandscape,
) -> str:
    lines: list[str] = []
    assoc_by_gene = {a.gene_symbol: a for a in association.associations}
    lit_by_gene = {g.gene_symbol: g for g in literature.genes}
    drug_by_gene = {g.gene_symbol: g for g in druggability.genes}
    comp_by_gene = {g.gene_symbol: g for g in competitive.genes}
    pathway_genes = {gene for p in pathways.pathways for gene in p.member_genes}

    for gene in association.candidate_genes:
        assoc = assoc_by_gene.get(gene)
        lit = lit_by_gene.get(gene)
        drug = drug_by_gene.get(gene)
        comp = comp_by_gene.get(gene)
        in_pathway = gene in pathway_genes

        lines.append(f"## {gene}")
        if assoc:
            lines.append(f"- Genetic association: odds_ratio={assoc.odds_ratio:.2f}, q_value={assoc.q_value:.4f}")
        lines.append(f"- In an enriched pathway: {in_pathway}")
        if lit:
            lines.append(
                f"- Literature: {lit.paper_count} papers, "
                f"drug programs mentioned: {lit.known_drug_programs_mentioned}"
            )
            for claim in lit.claims[:3]:
                lines.append(f"  - {claim.claim} (PMID {claim.pmid})")
        if drug:
            lines.append(f"- Druggability: {drug.tier.value} — {drug.rationale}")
        if comp:
            lines.append(f"- Competitive landscape: {len(comp.existing_programs)} existing programs — {comp.summary}")
        lines.append("")

    return "\n".join(lines)
