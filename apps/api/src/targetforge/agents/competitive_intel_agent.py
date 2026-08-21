"""Agent 9 — Competitive Intelligence. See docs/agents.md.

Answers the question that saves a team a wasted quarter: does this target
already have an active drug development program? Grounded entirely in real
ClinicalTrials.gov records, not the model's memory of drug pipelines (which
is only as current as its training cutoff and frequently wrong about
fast-moving trial status).
"""

from __future__ import annotations

from pydantic import BaseModel

from targetforge.core.llm import LlmClient
from targetforge.external.clinicaltrials_client import ClinicalTrialsClient
from targetforge.schemas.pipeline import (
    CompetitiveLandscape,
    ExistingProgram,
    GeneCompetitiveLandscape,
)


class _CompetitiveSummaryResponse(BaseModel):
    """Narrower shape than GeneCompetitiveLandscape on purpose: the LLM only
    ever produces the summary prose. `existing_programs` is deterministic
    data straight from ClinicalTrials.gov (see run() below) — asking the LLM
    to also echo it back would just be an extra chance for it to drop or
    misquote a trial ID.
    """

    gene_symbol: str
    summary: str

_SYSTEM_PROMPT = """You are a pharmaceutical competitive intelligence analyst.

You will be given a gene symbol and a list of ClinicalTrials.gov trial
records that mention it. Summarize the competitive landscape: how many
active programs exist, what's the most advanced phase, and whether this
looks crowded or open.

Only reference trials from the provided list — do not invent trial IDs or
sponsors not present in the data.

Respond with ONLY a JSON object matching exactly:
{
  "gene_symbol": string,
  "summary": string
}
"""


class CompetitiveIntelligenceAgent:
    def __init__(self, llm: LlmClient, clinical_trials: ClinicalTrialsClient) -> None:
        self._llm = llm
        self._clinical_trials = clinical_trials

    async def run(self, run_id: str, candidate_genes: list[str]) -> CompetitiveLandscape:
        genes: list[GeneCompetitiveLandscape] = []
        for gene in candidate_genes:
            trials = await self._clinical_trials.search_by_gene(gene)
            existing_programs = [
                ExistingProgram(trial_id=t.nct_id, phase=t.phase, sponsor=t.sponsor, status=t.status)
                for t in trials
            ]

            if trials:
                trials_text = "\n".join(
                    f"- {t.nct_id}: {t.title} (phase {t.phase}, {t.status}, sponsor {t.sponsor})" for t in trials
                )
                user_prompt = f"Gene: {gene}\n\nTrials mentioning this gene:\n{trials_text}"
                llm_result = await self._llm.complete_json(
                    _SYSTEM_PROMPT, user_prompt, _CompetitiveSummaryResponse
                )
                summary = llm_result.summary
            else:
                summary = "No active or completed clinical trials found referencing this gene/target."

            genes.append(
                GeneCompetitiveLandscape(gene_symbol=gene, existing_programs=existing_programs, summary=summary)
            )

        return CompetitiveLandscape(run_id=run_id, genes=genes)
