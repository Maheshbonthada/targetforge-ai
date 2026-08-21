"""Agent 6 — Pathway Enrichment. See docs/agents.md. No LLM — pure statistics
(bioinformatics/pathway_enrichment.py)."""

from __future__ import annotations

from targetforge.bioinformatics.pathway_enrichment import run_enrichment
from targetforge.schemas.pipeline import PathwayEnrichmentResult, PathwayHit


class PathwayEnrichmentAgent:
    def run(self, run_id: str, candidate_genes: list[str], q_threshold: float = 0.10) -> PathwayEnrichmentResult:
        hits = run_enrichment(candidate_genes, q_threshold)
        pathways = [
            PathwayHit(
                pathway_id=h.pathway_id,
                pathway_name=h.pathway_name,
                source=h.source,
                member_genes=h.member_genes,
                p_value=h.p_value,
                q_value=h.q_value,
            )
            for h in hits
        ]
        return PathwayEnrichmentResult(run_id=run_id, pathways=pathways)
