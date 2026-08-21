"""Typed input/output contracts for every agent in the pipeline (see docs/agents.md).

Every agent takes one of these in and returns one of these out — no agent
passes untyped dicts to another. This is what makes the Supervisor able to
persist, inspect, and resume a run: every stage's output is a serializable,
schema-validated record, not an implicit convention between two functions.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

# --- Stage 1: QC ---


class SampleQc(BaseModel):
    sample_id: str
    total_reads: int
    q30_rate: float = Field(ge=0.0, le=1.0)
    gc_content: float = Field(ge=0.0, le=1.0)
    mean_read_length: float
    adapter_contamination_pct: float = Field(ge=0.0, le=100.0)
    passed: bool
    flags: list[str] = Field(default_factory=list)


class QcResult(BaseModel):
    run_id: str
    samples: list[SampleQc]
    summary: str  # LLM-generated plain-language rollup
    samples_passed: int
    samples_failed: int


# --- Stage 2: Alignment ---


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class SampleAlignment(BaseModel):
    sample_id: str
    bam_blob_path: str | None = None
    alignment_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    mean_coverage: float | None = None
    status: JobStatus


class AlignmentResult(BaseModel):
    run_id: str
    reference_build: str
    samples: list[SampleAlignment]


# --- Stage 3: Variant calling ---


class VariantCallResult(BaseModel):
    run_id: str
    cohort_vcf_blob_path: str
    total_variants: int
    ti_tv_ratio: float  # sanity-check metric: ~2.0-2.1 for whole-genome human data
    status: JobStatus


# --- Stage 4: Annotation ---


class CalledVariant(BaseModel):
    """A single variant call, already parsed out of the cohort VCF (see
    VariantCallResult.cohort_vcf_blob_path). VCF parsing itself is a real
    production step (via `cyvcf2`/`pysam`) not implemented here — the
    annotation agent takes already-parsed variant records so it's testable
    independent of a real VCF file. See docs/scaling-to-real-genomes.md.
    """

    chrom: str
    pos: int
    ref: str
    alt: str


class ClinicalSignificance(str, Enum):
    BENIGN = "benign"
    LIKELY_BENIGN = "likely_benign"
    UNCERTAIN = "uncertain_significance"
    LIKELY_PATHOGENIC = "likely_pathogenic"
    PATHOGENIC = "pathogenic"
    NOT_IN_CLINVAR = "not_in_clinvar"


class AnnotatedVariant(BaseModel):
    chrom: str
    pos: int
    ref: str
    alt: str
    gene_symbol: str
    consequence: str  # e.g. "missense_variant", "stop_gained"
    gnomad_af: float | None = None
    clinvar_significance: ClinicalSignificance = ClinicalSignificance.NOT_IN_CLINVAR
    impact: str  # VEP's HIGH/MODERATE/LOW/MODIFIER


class AnnotatedVariantSet(BaseModel):
    run_id: str
    variants: list[AnnotatedVariant]
    genes_affected: int
    summary: str


# --- Stage 5: Cohort / association ---


class VariantAssociation(BaseModel):
    gene_symbol: str
    chrom: str
    pos: int
    odds_ratio: float
    p_value: float
    q_value: float  # multiple-testing corrected (Benjamini-Hochberg)
    case_allele_count: int
    control_allele_count: int


class AssociationResult(BaseModel):
    run_id: str
    associations: list[VariantAssociation]
    candidate_genes: list[str]  # genes passing the q-value significance threshold


# --- Stage 6: Pathway enrichment ---


class PathwayHit(BaseModel):
    pathway_id: str
    pathway_name: str
    source: str  # "KEGG" | "Reactome" | "GO"
    member_genes: list[str]
    p_value: float
    q_value: float


class PathwayEnrichmentResult(BaseModel):
    run_id: str
    pathways: list[PathwayHit]


# --- Stage 7: Literature mining ---


class CitedClaim(BaseModel):
    claim: str
    pmid: str


class GeneLiteratureEvidence(BaseModel):
    gene_symbol: str
    paper_count: int
    claims: list[CitedClaim]
    known_drug_programs_mentioned: bool


class LiteratureEvidence(BaseModel):
    run_id: str
    genes: list[GeneLiteratureEvidence]


# --- Stage 8: Druggability ---


class DruggabilityTier(str, Enum):
    WELL_PRECEDENTED = "well_precedented"
    EMERGING_MODALITY = "emerging_modality"
    CHALLENGING = "challenging"
    UNKNOWN = "unknown"


class GeneDruggability(BaseModel):
    gene_symbol: str
    tier: DruggabilityTier
    known_ligands: list[str] = Field(default_factory=list)
    rationale: str


class DruggabilityAssessment(BaseModel):
    run_id: str
    genes: list[GeneDruggability]


# --- Stage 9: Competitive intelligence ---


class ExistingProgram(BaseModel):
    trial_id: str
    phase: str
    sponsor: str
    status: str


class GeneCompetitiveLandscape(BaseModel):
    gene_symbol: str
    existing_programs: list[ExistingProgram]
    summary: str


class CompetitiveLandscape(BaseModel):
    run_id: str
    genes: list[GeneCompetitiveLandscape]


# --- Stage 10: Prioritization ---


class TargetScore(BaseModel):
    gene_symbol: str
    rank: int
    composite_score: float = Field(ge=0.0, le=100.0)
    genetic_evidence_score: float
    druggability_score: float
    novelty_score: float  # inversely related to competitive crowding
    rationale: str  # itemized, references which inputs drove the score


class TargetRanking(BaseModel):
    run_id: str
    targets: list[TargetScore]


# --- Stage 11: Experiment design ---


class ExperimentProposal(BaseModel):
    # protected_namespaces=() — pydantic reserves the "model_" field-name
    # prefix for its own API by default; `model_system` (a cell line/organism
    # model, nothing to do with an ML model) is a false-positive collision.
    model_config = ConfigDict(protected_namespaces=())

    gene_symbol: str
    proposed_approach: str
    model_system: str
    precedent_pmid: str | None = None
    caveat: str  # explicit "this is a starting proposal, not a validated protocol"


class ExperimentDesignResult(BaseModel):
    run_id: str
    proposals: list[ExperimentProposal]


# --- Stage 12: Report ---


class GenomicsReport(BaseModel):
    run_id: str
    executive_summary: str
    methodology: str
    ranked_targets: list[TargetScore]
    report_markdown: str
    generated_at: str
