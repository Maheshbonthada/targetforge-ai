"""Agent 8 — Druggability Assessment. See docs/agents.md.

The LLM's job here is narrow: turn DGIdb's structured drug-interaction
records into a plain-language tractability verdict. The tier assigned is
grounded entirely in what DGIdb actually returned — the agent is not asked
to invent druggability knowledge from parametric memory, which is exactly
the kind of claim that needs to trace to a real database, not a language
model's training data of unknown recency and provenance.
"""

from __future__ import annotations

from targetforge.core.llm import LlmClient
from targetforge.external.dgidb_client import DgidbClient
from targetforge.schemas.pipeline import DruggabilityAssessment, GeneDruggability

_SYSTEM_PROMPT = """You are a drug discovery tractability analyst.

You will be given a gene symbol and a list of known drug/ligand interactions
for it from DGIdb (the Drug-Gene Interaction Database).

Assign a druggability tier based ONLY on the provided interaction data:
- "well_precedented": multiple known drugs/inhibitors already exist for this target
- "emerging_modality": few or preclinical-stage interactions, or interactions
  suggesting a non-small-molecule modality (antibody, degrader, etc.)
- "challenging": no known interactions were provided, or the interaction
  types suggest a historically hard-to-drug target class (e.g. transcription factor)
- "unknown": insufficient information to judge

known_ligands: list AT MOST the 6 highest-relevance drug names, not every
interaction provided — a heavily-drugged target (e.g. a well-known kinase)
can have dozens of interactions, and the point of this field is a
representative sample, not an exhaustive list.

Respond with ONLY a JSON object matching exactly:
{
  "gene_symbol": string,
  "tier": "well_precedented" | "emerging_modality" | "challenging" | "unknown",
  "known_ligands": [string, ...],
  "rationale": string
}
"""

_MAX_INTERACTIONS_IN_PROMPT = 25  # a real target (e.g. EGFR) can have 100+; cap input, not just output


class DruggabilityAssessmentAgent:
    def __init__(self, llm: LlmClient, dgidb: DgidbClient) -> None:
        self._llm = llm
        self._dgidb = dgidb

    async def run(self, run_id: str, candidate_genes: list[str]) -> DruggabilityAssessment:
        assessments: list[GeneDruggability] = []
        for gene in candidate_genes:
            interactions = await self._dgidb.get_interactions(gene)
            top_interactions = sorted(
                interactions, key=lambda i: i.interaction_score or 0.0, reverse=True
            )[:_MAX_INTERACTIONS_IN_PROMPT]
            interactions_text = (
                "\n".join(
                    f"- {i.drug_name} (types: {', '.join(i.interaction_types) or 'unspecified'}, "
                    f"score: {i.interaction_score})"
                    for i in top_interactions
                )
                or "(no known interactions found in DGIdb)"
            )
            if len(interactions) > _MAX_INTERACTIONS_IN_PROMPT:
                omitted = len(interactions) - _MAX_INTERACTIONS_IN_PROMPT
                interactions_text += f"\n(+ {omitted} more, omitted for brevity)"
            user_prompt = f"Gene: {gene}\n\nKnown DGIdb interactions:\n{interactions_text}"
            result = await self._llm.complete_json(_SYSTEM_PROMPT, user_prompt, GeneDruggability)
            assessments.append(result)

        return DruggabilityAssessment(run_id=run_id, genes=assessments)
