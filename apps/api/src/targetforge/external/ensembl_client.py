"""Ensembl VEP (Variant Effect Predictor) REST API client — free, keyless,
maintained by EMBL-EBI. Used by the Variant Annotation Agent (4) to annotate
called variants with predicted consequence, gene, and impact.

This is the same VEP tool used inside real production genomics pipelines,
just called via its hosted REST API instead of a local install — appropriate
for annotating a candidate gene list's worth of variants; a whole-genome
cohort VCF would use a local/containerized VEP install instead (see
docs/scaling-to-real-genomes.md) to avoid hammering the public endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential


@dataclass
class VepAnnotation:
    variant_input: str
    gene_symbol: str
    consequence: str
    impact: str
    gnomad_af: float | None = None
    clinvar_significance: str | None = None
    transcript_consequences: list[dict] = field(default_factory=list)


class VepClient(Protocol):
    async def annotate_region(self, region_notation: list[str]) -> list[VepAnnotation]: ...


class RealVepClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    @retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    async def annotate_region(self, region_notation: list[str]) -> list[VepAnnotation]:
        """`region_notation` entries look like '1 230710048 230710048 A/G . . .'
        (chrom start end alleles), VEP's region-input format. VEP caps batch
        POST requests at 200 variants — callers batch larger sets themselves.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self._base_url}/vep/human/region",
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                json={"variants": region_notation},
            )
            response.raise_for_status()
            results = response.json()

        annotations: list[VepAnnotation] = []
        for result in results:
            transcript_consequences = result.get("transcript_consequences", [])
            most_severe = transcript_consequences[0] if transcript_consequences else {}
            colocated = result.get("colocated_variants", [{}])
            clinvar = next(
                (c.get("clin_sig", [None])[0] for c in colocated if c.get("clin_sig")), None
            )
            gnomad_af = next(
                (c.get("frequencies", {}).get(most_severe.get("allele_string", ""), {}).get("af") for c in colocated),
                None,
            )
            annotations.append(
                VepAnnotation(
                    variant_input=result.get("input", ""),
                    gene_symbol=most_severe.get("gene_symbol", "UNKNOWN"),
                    consequence=result.get("most_severe_consequence", "unknown"),
                    impact=most_severe.get("impact", "MODIFIER"),
                    gnomad_af=gnomad_af,
                    clinvar_significance=clinvar,
                    transcript_consequences=transcript_consequences,
                )
            )
        return annotations


class FakeVepClient:
    def __init__(self, annotations: list[VepAnnotation] | None = None) -> None:
        self._annotations = annotations or []

    async def annotate_region(self, region_notation: list[str]) -> list[VepAnnotation]:
        return self._annotations
