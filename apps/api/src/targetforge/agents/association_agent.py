"""Agent 5 — Cohort/Association. See docs/agents.md. No LLM — pure statistics
(bioinformatics/association_stats.py)."""

from __future__ import annotations

from targetforge.bioinformatics.association_stats import (
    VariantCounts,
    compute_associations,
    significant_genes,
)
from targetforge.schemas.pipeline import AssociationResult, VariantAssociation


class CohortAssociationAgent:
    def run(self, run_id: str, variant_counts: list[VariantCounts], q_threshold: float = 0.05) -> AssociationResult:
        stats = compute_associations(variant_counts)
        associations = [
            VariantAssociation(
                gene_symbol=s.gene_symbol,
                chrom=s.chrom,
                pos=s.pos,
                odds_ratio=s.odds_ratio,
                p_value=s.p_value,
                q_value=s.q_value,
                case_allele_count=s.case_allele_count,
                control_allele_count=s.control_allele_count,
            )
            for s in stats
        ]
        candidates = significant_genes(stats, q_threshold)
        return AssociationResult(run_id=run_id, associations=associations, candidate_genes=candidates)
