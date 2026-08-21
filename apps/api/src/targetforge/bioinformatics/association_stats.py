"""Real case/control genetic association statistics — Fisher's exact test per
variant plus Benjamini-Hochberg multiple-testing correction, the same
statistical approach standard association tools (PLINK et al.) use for
single-variant tests. Pure `scipy`/`numpy`, no LLM, no external binary — see
docs/agents.md, Agent 5, for why this stage is explicitly not agentic:
a p-value is not a judgment call.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import fisher_exact


@dataclass
class VariantCounts:
    gene_symbol: str
    chrom: str
    pos: int
    case_alt_count: int
    case_ref_count: int
    control_alt_count: int
    control_ref_count: int


@dataclass
class AssociationStat:
    gene_symbol: str
    chrom: str
    pos: int
    odds_ratio: float
    p_value: float
    q_value: float
    case_allele_count: int
    control_allele_count: int


def benjamini_hochberg(p_values: list[float]) -> list[float]:
    """Standard BH step-up procedure. Returns q-values in the same order as
    the input p-values. Correcting for multiple testing is not optional here:
    testing thousands of variants at an uncorrected p < 0.05 threshold would
    produce hundreds of false-positive "associations" by chance alone.
    """
    n = len(p_values)
    if n == 0:
        return []
    order = np.argsort(p_values)
    ranked_p = np.array(p_values)[order]
    ranks = np.arange(1, n + 1)
    raw_q = ranked_p * n / ranks
    # Enforce monotonicity (BH's step-up requirement: q-values must be
    # non-decreasing from the largest p-value down).
    q_monotone = np.minimum.accumulate(raw_q[::-1])[::-1]
    q_monotone = np.clip(q_monotone, 0, 1)

    q_values = np.empty(n)
    q_values[order] = q_monotone
    return q_values.tolist()


def compute_associations(variants: list[VariantCounts]) -> list[AssociationStat]:
    p_values: list[float] = []
    odds_ratios: list[float] = []

    for v in variants:
        table = [[v.case_alt_count, v.case_ref_count], [v.control_alt_count, v.control_ref_count]]
        odds_ratio, p_value = fisher_exact(table)
        p_values.append(p_value)
        odds_ratios.append(odds_ratio)

    q_values = benjamini_hochberg(p_values)

    return [
        AssociationStat(
            gene_symbol=v.gene_symbol,
            chrom=v.chrom,
            pos=v.pos,
            odds_ratio=odds_ratios[i],
            p_value=p_values[i],
            q_value=q_values[i],
            case_allele_count=v.case_alt_count,
            control_allele_count=v.control_alt_count,
        )
        for i, v in enumerate(variants)
    ]


def significant_genes(associations: list[AssociationStat], q_threshold: float = 0.05) -> list[str]:
    seen: list[str] = []
    for assoc in sorted(associations, key=lambda a: a.q_value):
        if assoc.q_value <= q_threshold and assoc.gene_symbol not in seen:
            seen.append(assoc.gene_symbol)
    return seen
