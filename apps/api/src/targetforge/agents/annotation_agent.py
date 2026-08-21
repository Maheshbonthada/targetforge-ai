"""Agent 4 — Variant Annotation. See docs/agents.md.

Calls the real Ensembl VEP REST API for consequence/gene/impact annotation.
LLM use is summarization only — the annotation facts themselves come
straight from VEP, never from the model.
"""

from __future__ import annotations

from pydantic import BaseModel

from targetforge.core.llm import LlmClient
from targetforge.external.ensembl_client import VepClient
from targetforge.schemas.pipeline import (
    AnnotatedVariant,
    AnnotatedVariantSet,
    CalledVariant,
    ClinicalSignificance,
)

_SYSTEM_PROMPT = """You are a clinical genomics annotator. You will be given
a list of annotated variants (gene, consequence, impact, population
frequency). Write one short paragraph summarizing the set: how many variants,
how many genes affected, and how many look potentially significant (rare +
high/moderate impact). Do not invent numbers not present in the data.

Respond with ONLY a JSON object matching exactly: {"summary": string}
"""


class _SummaryResponse(BaseModel):
    summary: str


_CLINVAR_MAP = {
    "benign": ClinicalSignificance.BENIGN,
    "likely_benign": ClinicalSignificance.LIKELY_BENIGN,
    "uncertain_significance": ClinicalSignificance.UNCERTAIN,
    "likely_pathogenic": ClinicalSignificance.LIKELY_PATHOGENIC,
    "pathogenic": ClinicalSignificance.PATHOGENIC,
}


class VariantAnnotationAgent:
    def __init__(self, llm: LlmClient, vep: VepClient) -> None:
        self._llm = llm
        self._vep = vep

    async def run(self, run_id: str, variants: list[CalledVariant]) -> AnnotatedVariantSet:
        # VEP's region-input format: "chrom start end ref/alt strand"
        region_notation = [f"{v.chrom} {v.pos} {v.pos} {v.ref}/{v.alt} 1" for v in variants]

        annotated: list[AnnotatedVariant] = []
        # VEP caps batch POSTs at 200 variants per call.
        for batch_start in range(0, len(region_notation), 200):
            batch = region_notation[batch_start : batch_start + 200]
            vep_results = await self._vep.annotate_region(batch)
            for vep_result, source_variant in zip(vep_results, variants[batch_start : batch_start + 200], strict=False):
                annotated.append(
                    AnnotatedVariant(
                        chrom=source_variant.chrom,
                        pos=source_variant.pos,
                        ref=source_variant.ref,
                        alt=source_variant.alt,
                        gene_symbol=vep_result.gene_symbol,
                        consequence=vep_result.consequence,
                        gnomad_af=vep_result.gnomad_af,
                        clinvar_significance=_CLINVAR_MAP.get(
                            (vep_result.clinvar_significance or "").lower(), ClinicalSignificance.NOT_IN_CLINVAR
                        ),
                        impact=vep_result.impact,
                    )
                )

        genes_affected = len({v.gene_symbol for v in annotated if v.gene_symbol != "UNKNOWN"})
        summary_input = "\n".join(
            f"- {v.gene_symbol}: {v.consequence} ({v.impact}), gnomAD AF={v.gnomad_af}" for v in annotated
        )
        result = await self._llm.complete_json(
            _SYSTEM_PROMPT, f"Annotated variants:\n{summary_input[:8000]}", _SummaryResponse
        )

        return AnnotatedVariantSet(
            run_id=run_id, variants=annotated, genes_affected=genes_affected, summary=result.summary
        )
