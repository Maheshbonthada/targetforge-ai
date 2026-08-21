"""Real hypergeometric pathway enrichment testing against curated gene sets
(data/knowledge/pathways.yaml). Same statistical test (hypergeometric /
Fisher's exact on 2x2 gene-set-membership tables) that GSEA-style and
KEGG/Reactome enrichment tools use. Pure `scipy`, no LLM — see docs/agents.md,
Agent 6.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from scipy.stats import hypergeom

from targetforge.bioinformatics.association_stats import benjamini_hochberg


@dataclass
class PathwaySet:
    id: str
    name: str
    source: str
    genes: frozenset[str]


@dataclass
class EnrichmentHit:
    pathway_id: str
    pathway_name: str
    source: str
    member_genes: list[str]
    p_value: float
    q_value: float


def _pathways_path() -> Path:
    """`TARGETFORGE_PATHWAYS_PATH` is always set in the container (see
    apps/api/Dockerfile) and in production, pointing at wherever
    data/knowledge/pathways.yaml was copied to. The repo-root-relative
    fallback below only has to hold for local dev, where this file's depth
    under the repo root is fixed — but it's computed lazily, on first call,
    not at import time: eagerly computing it at module import broke the
    packaged container, where this module lives at a different depth
    (/app/src/... instead of apps/api/src/...) and the override is always
    set anyway, so the fallback is never reached there.
    """
    override = os.environ.get("TARGETFORGE_PATHWAYS_PATH")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[5] / "data" / "knowledge" / "pathways.yaml"


@lru_cache
def load_pathways() -> tuple[int, tuple[PathwaySet, ...]]:
    with _pathways_path().open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    background = int(raw["background_gene_count"])
    pathways = tuple(
        PathwaySet(id=p["id"], name=p["name"], source=p["source"], genes=frozenset(p["genes"]))
        for p in raw["pathways"]
    )
    return background, pathways


def run_enrichment(candidate_genes: list[str], q_threshold: float = 0.10) -> list[EnrichmentHit]:
    """Hypergeometric test: for each pathway, what's the probability of
    seeing this many candidate genes fall inside it by chance, given the
    pathway's size and the background gene universe?

    P(X >= k) where:
      k = observed overlap between candidate_genes and the pathway
      pathway_size = size of the pathway gene set
      candidate_count = number of candidate genes tested
      background = total genes in the background universe
    """
    background, pathways = load_pathways()
    candidate_set = frozenset(candidate_genes)
    candidate_count = len(candidate_set)

    raw_hits: list[tuple[PathwaySet, list[str], float]] = []
    for pathway in pathways:
        overlap = sorted(candidate_set & pathway.genes)
        if not overlap:
            continue
        # sf(k-1) = P(X >= k) — the survival function shifted by one, since
        # scipy's hypergeom.sf(k, ...) computes P(X > k), not P(X >= k).
        p_value = hypergeom.sf(len(overlap) - 1, background, len(pathway.genes), candidate_count)
        raw_hits.append((pathway, overlap, float(p_value)))

    if not raw_hits:
        return []

    q_values = benjamini_hochberg([p for _, _, p in raw_hits])

    hits = [
        EnrichmentHit(
            pathway_id=pathway.id,
            pathway_name=pathway.name,
            source=pathway.source,
            member_genes=overlap,
            p_value=p_value,
            q_value=q_values[i],
        )
        for i, (pathway, overlap, p_value) in enumerate(raw_hits)
    ]
    significant = [h for h in hits if h.q_value <= q_threshold]
    return sorted(significant, key=lambda h: h.q_value)
