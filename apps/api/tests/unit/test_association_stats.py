from __future__ import annotations

from targetforge.bioinformatics.association_stats import (
    VariantCounts,
    benjamini_hochberg,
    compute_associations,
    significant_genes,
)


class TestBenjaminiHochberg:
    def test_empty_input(self):
        assert benjamini_hochberg([]) == []

    def test_all_significant_p_values_stay_small(self):
        q = benjamini_hochberg([0.001, 0.002, 0.001])
        assert all(v < 0.01 for v in q)

    def test_monotonicity_is_enforced(self):
        # q-values must be non-decreasing when p-values are sorted ascending
        p = [0.001, 0.5, 0.01, 0.9, 0.02]
        q = benjamini_hochberg(p)
        order = sorted(range(len(p)), key=lambda i: p[i])
        sorted_q = [q[i] for i in order]
        assert sorted_q == sorted(sorted_q)

    def test_q_values_never_exceed_one(self):
        q = benjamini_hochberg([0.9, 0.95, 0.99])
        assert all(v <= 1.0 for v in q)


class TestComputeAssociations:
    def test_strong_case_enrichment_yields_low_p_value(self):
        # Clear enrichment in cases: 45/100 vs 5/100 controls
        variants = [VariantCounts("GENE_A", "1", 100, 45, 55, 5, 95)]
        stats = compute_associations(variants)
        assert stats[0].p_value < 0.001
        assert stats[0].odds_ratio > 1.0

    def test_no_association_yields_high_p_value(self):
        variants = [VariantCounts("GENE_B", "1", 200, 25, 75, 25, 75)]
        stats = compute_associations(variants)
        assert stats[0].p_value > 0.5

    def test_output_length_matches_input(self):
        variants = [
            VariantCounts("A", "1", 1, 10, 90, 5, 95),
            VariantCounts("B", "2", 2, 20, 80, 18, 82),
        ]
        stats = compute_associations(variants)
        assert len(stats) == 2


class TestSignificantGenes:
    def test_filters_by_q_threshold(self):
        variants = [
            VariantCounts("STRONG", "1", 1, 45, 55, 5, 95),
            VariantCounts("WEAK", "2", 2, 26, 74, 25, 75),
        ]
        stats = compute_associations(variants)
        candidates = significant_genes(stats, q_threshold=0.05)
        assert "STRONG" in candidates
        assert "WEAK" not in candidates

    def test_deduplicates_genes_with_multiple_variants(self):
        variants = [
            VariantCounts("DUP", "1", 1, 45, 55, 5, 95),
            VariantCounts("DUP", "1", 2, 40, 60, 6, 94),
        ]
        stats = compute_associations(variants)
        candidates = significant_genes(stats, q_threshold=0.05)
        assert candidates.count("DUP") == 1
