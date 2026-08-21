from __future__ import annotations

from targetforge.bioinformatics.pathway_enrichment import load_pathways, run_enrichment


class TestLoadPathways:
    def test_pathways_load_and_have_unique_ids(self):
        background, pathways = load_pathways()
        assert background > 0
        ids = [p.id for p in pathways]
        assert len(ids) == len(set(ids))

    def test_every_pathway_has_at_least_one_gene(self):
        _, pathways = load_pathways()
        assert all(len(p.genes) > 0 for p in pathways)


class TestRunEnrichment:
    def test_known_p53_pathway_genes_enrich(self):
        hits = run_enrichment(["TP53", "MDM2", "ATM", "CHEK2", "CDKN1A"], q_threshold=0.10)
        assert any(h.pathway_id == "hsa04115" for h in hits)

    def test_unrelated_random_genes_do_not_enrich(self):
        hits = run_enrichment(["ZZZFAKE1", "ZZZFAKE2", "ZZZFAKE3"], q_threshold=0.10)
        assert hits == []

    def test_empty_candidate_list_yields_no_hits(self):
        assert run_enrichment([], q_threshold=0.10) == []

    def test_hits_sorted_by_q_value_ascending(self):
        hits = run_enrichment(["TP53", "MDM2", "ATM", "CHEK2", "CDKN1A", "KRAS", "BRAF"], q_threshold=1.0)
        q_values = [h.q_value for h in hits]
        assert q_values == sorted(q_values)
