"""Agent 12 — Report Generation. See docs/agents.md.

Synthesizes the full run into a structured report. Deliberately constrained
by construction, not just instruction: this agent's prompt only ever
contains prior agents' structured JSON output, never an open-ended "write
about gene X" — so it cannot introduce a claim that didn't already pass
through an earlier agent's evidence-gathering (and, for literature claims,
citation) discipline.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel

from targetforge.core.llm import LlmClient
from targetforge.schemas.pipeline import (
    CompetitiveLandscape,
    DruggabilityAssessment,
    ExperimentDesignResult,
    GenomicsReport,
    LiteratureEvidence,
    TargetRanking,
)

_SYSTEM_PROMPT = """You are a scientific writer producing a target discovery
report for a biotech R&D team. You will be given the complete structured
output of a target discovery pipeline run. Write an executive summary (3-5
sentences, plain language, aimed at a program lead deciding what to fund
next) and a methodology paragraph (what pipeline stages produced this
evidence, referenced factually, not embellished).

Do not introduce any claim, gene, or number not present in the provided data.

Respond with ONLY a JSON object matching exactly:
{"executive_summary": string, "methodology": string}
"""


class _ReportNarrative(BaseModel):
    executive_summary: str
    methodology: str


class ReportGenerationAgent:
    def __init__(self, llm: LlmClient) -> None:
        self._llm = llm

    async def run(
        self,
        run_id: str,
        ranking: TargetRanking,
        literature: LiteratureEvidence,
        druggability: DruggabilityAssessment,
        competitive: CompetitiveLandscape,
        experiments: ExperimentDesignResult,
        reference_build: str,
    ) -> GenomicsReport:
        context = _build_context(ranking, literature, druggability, competitive, experiments)
        narrative = await self._llm.complete_json(_SYSTEM_PROMPT, context, _ReportNarrative)

        markdown = _render_markdown(
            narrative, ranking, literature, druggability, competitive, experiments, reference_build
        )

        return GenomicsReport(
            run_id=run_id,
            executive_summary=narrative.executive_summary,
            methodology=narrative.methodology,
            ranked_targets=ranking.targets,
            report_markdown=markdown,
            generated_at=datetime.now(UTC).isoformat(),
        )


def _build_context(
    ranking: TargetRanking,
    literature: LiteratureEvidence,
    druggability: DruggabilityAssessment,
    competitive: CompetitiveLandscape,
    experiments: ExperimentDesignResult,
) -> str:
    lines = ["Ranked targets:"]
    for t in ranking.targets:
        lines.append(f"{t.rank}. {t.gene_symbol} (score {t.composite_score:.0f}) — {t.rationale}")
    lines.append(f"\nTotal candidate genes evaluated: {len(ranking.targets)}")
    programs_count = sum(1 for g in competitive.genes if g.existing_programs)
    lines.append(f"Genes with existing competitive programs: {programs_count}")
    lines.append(f"Genes with literature evidence found: {sum(1 for g in literature.genes if g.paper_count > 0)}")
    return "\n".join(lines)


def _render_markdown(
    narrative: _ReportNarrative,
    ranking: TargetRanking,
    literature: LiteratureEvidence,
    druggability: DruggabilityAssessment,
    competitive: CompetitiveLandscape,
    experiments: ExperimentDesignResult,
    reference_build: str,
) -> str:
    lit_by_gene = {g.gene_symbol: g for g in literature.genes}
    drug_by_gene = {g.gene_symbol: g for g in druggability.genes}
    comp_by_gene = {g.gene_symbol: g for g in competitive.genes}
    exp_by_gene = {e.gene_symbol: e for e in experiments.proposals}

    parts = [
        "# TargetForge Discovery Report",
        "",
        "## Executive Summary",
        narrative.executive_summary,
        "",
        "## Methodology",
        narrative.methodology,
        f"\n*Reference build: {reference_build}*",
        "",
        "## Ranked Targets",
    ]

    for t in ranking.targets:
        parts.append(f"\n### {t.rank}. {t.gene_symbol} — score {t.composite_score:.0f}/100")
        parts.append(t.rationale)

        lit = lit_by_gene.get(t.gene_symbol)
        if lit and lit.claims:
            parts.append("\n**Literature evidence:**")
            for claim in lit.claims:
                parts.append(f"- {claim.claim} ([PMID {claim.pmid}](https://pubmed.ncbi.nlm.nih.gov/{claim.pmid}/))")

        drug = drug_by_gene.get(t.gene_symbol)
        if drug:
            parts.append(f"\n**Druggability:** {drug.tier.value} — {drug.rationale}")

        comp = comp_by_gene.get(t.gene_symbol)
        if comp:
            parts.append(f"\n**Competitive landscape:** {comp.summary}")

        exp = exp_by_gene.get(t.gene_symbol)
        if exp:
            parts.append(f"\n**Suggested validation:** {exp.proposed_approach} ({exp.model_system})")
            parts.append(f"\n> {exp.caveat}")

    return "\n".join(parts)
