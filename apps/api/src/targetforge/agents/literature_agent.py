"""Agent 7 — Literature Mining. See docs/agents.md.

Genuinely agentic: for each candidate gene, it searches PubMed, reads the
returned abstracts, and decides what's worth summarizing — and every claim
it produces is tagged with the PMID it came from. The Report Generation
Agent (12) is built to refuse any claim without a citation, so this agent's
citation discipline is load-bearing for the whole pipeline's credibility,
not a nice-to-have.
"""

from __future__ import annotations

from targetforge.core.llm import LlmClient
from targetforge.external.pubmed_client import PubMedClient
from targetforge.schemas.pipeline import GeneLiteratureEvidence, LiteratureEvidence

_SYSTEM_PROMPT = """You are a genomics research literature analyst.

You will be given a gene symbol and a set of PubMed abstracts about it.
Summarize what these abstracts establish about the gene's disease
association, mechanism, and any mentioned drug development activity.

Rules:
- EVERY claim must cite the PMID of the specific abstract it came from. Never
  state something as fact without a supporting PMID from the provided set.
- If the abstracts don't clearly support a claim, don't make it — do not use
  outside knowledge not present in the provided abstracts.
- known_drug_programs_mentioned is true only if an abstract explicitly
  mentions a drug, inhibitor, or clinical program targeting this gene.

Respond with ONLY a JSON object matching exactly:
{
  "gene_symbol": string,
  "paper_count": integer,
  "claims": [{"claim": string, "pmid": string}, ...],
  "known_drug_programs_mentioned": boolean
}
"""


class LiteratureMiningAgent:
    def __init__(self, llm: LlmClient, pubmed: PubMedClient) -> None:
        self._llm = llm
        self._pubmed = pubmed

    async def run(self, run_id: str, candidate_genes: list[str]) -> LiteratureEvidence:
        gene_evidence: list[GeneLiteratureEvidence] = []
        for gene in candidate_genes:
            articles = await self._pubmed.search(f"{gene}[Gene] AND (cancer OR disease OR therapeutic target)")
            if not articles:
                gene_evidence.append(
                    GeneLiteratureEvidence(
                        gene_symbol=gene, paper_count=0, claims=[], known_drug_programs_mentioned=False
                    )
                )
                continue

            abstracts_text = "\n\n".join(
                f"PMID {a.pmid}: {a.title}\n{a.abstract}" for a in articles if a.abstract
            )
            user_prompt = f"Gene: {gene}\n\nAbstracts:\n{abstracts_text[:12000]}"
            result = await self._llm.complete_json(_SYSTEM_PROMPT, user_prompt, GeneLiteratureEvidence)
            # Trust the LLM's claims/summary, not its paper_count — that's a
            # fact we already know deterministically from the search results.
            result.paper_count = len(articles)
            gene_evidence.append(result)

        return LiteratureEvidence(run_id=run_id, genes=gene_evidence)
