"""Agent 11 — Experiment Design. See docs/agents.md.

Proposes a starting validation approach per top-ranked target, grounded in
the literature evidence already gathered by Agent 7 rather than invented
from scratch. Explicitly framed as a proposal for a scientist to refine, not
an executable protocol — the caveat field is mandatory, not decorative.
"""

from __future__ import annotations

from targetforge.core.llm import LlmClient
from targetforge.schemas.pipeline import (
    ExperimentDesignResult,
    ExperimentProposal,
    GeneLiteratureEvidence,
    TargetScore,
)

_SYSTEM_PROMPT = """You are a molecular biology experimental design consultant.

For the given target gene, propose a starting validation experiment: what
approach (e.g. CRISPR knockout/knockdown screen, in vitro binding assay,
reporter assay), what model system (cell line or organism), and cite a PMID
from the provided literature evidence if it establishes relevant precedent
(a prior study using a similar model for this gene or pathway).

This is a starting proposal for a scientist to refine, not a validated lab
protocol — say so explicitly in the caveat field.

Respond with ONLY a JSON object matching exactly:
{
  "gene_symbol": string,
  "proposed_approach": string,
  "model_system": string,
  "precedent_pmid": string or null,
  "caveat": string
}
"""


class ExperimentDesignAgent:
    def __init__(self, llm: LlmClient) -> None:
        self._llm = llm

    async def run(
        self, run_id: str, top_targets: list[TargetScore], literature_by_gene: dict[str, GeneLiteratureEvidence]
    ) -> ExperimentDesignResult:
        proposals: list[ExperimentProposal] = []
        for target in top_targets:
            lit = literature_by_gene.get(target.gene_symbol)
            claims_text = (
                "\n".join(f"- {c.claim} (PMID {c.pmid})" for c in lit.claims)
                if lit
                else "(no literature evidence available)"
            )
            user_prompt = (
                f"Target gene: {target.gene_symbol}\n"
                f"Prioritization rationale: {target.rationale}\n\n"
                f"Literature evidence:\n{claims_text}"
            )
            result = await self._llm.complete_json(_SYSTEM_PROMPT, user_prompt, ExperimentProposal)
            proposals.append(result)

        return ExperimentDesignResult(run_id=run_id, proposals=proposals)
