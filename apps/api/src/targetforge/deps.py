"""FastAPI dependency wiring — assembles the Supervisor from real or fake
implementations based on config, same "one place decides" principle as
TrialReady's deps.py.
"""

from __future__ import annotations

from functools import lru_cache

from targetforge.agents.alignment_agent import AlignmentOrchestrationAgent
from targetforge.agents.annotation_agent import VariantAnnotationAgent
from targetforge.agents.association_agent import CohortAssociationAgent
from targetforge.agents.competitive_intel_agent import CompetitiveIntelligenceAgent
from targetforge.agents.druggability_agent import DruggabilityAssessmentAgent
from targetforge.agents.experiment_design_agent import ExperimentDesignAgent
from targetforge.agents.literature_agent import LiteratureMiningAgent
from targetforge.agents.pathway_agent import PathwayEnrichmentAgent
from targetforge.agents.prioritization_agent import TargetPrioritizationAgent
from targetforge.agents.qc_agent import QcAgent
from targetforge.agents.report_agent import ReportGenerationAgent
from targetforge.agents.variant_calling_agent import VariantCallingAgent
from targetforge.bioinformatics.alignment import (
    AlignmentBackend,
    AzureBatchAlignmentBackend,
    FakeAlignmentBackend,
)
from targetforge.bioinformatics.variant_calling import (
    AzureBatchVariantCallingBackend,
    FakeVariantCallingBackend,
    VariantCallingBackend,
)
from targetforge.config import get_settings
from targetforge.core.llm import build_llm_client
from targetforge.core.rate_limit import SlidingWindowRateLimiter
from targetforge.external.clinicaltrials_client import RealClinicalTrialsClient
from targetforge.external.dgidb_client import RealDgidbClient
from targetforge.external.ensembl_client import FakeVepClient, RealVepClient, VepClient
from targetforge.external.pubmed_client import NcbiPubMedClient
from targetforge.orchestration.supervisor import Supervisor


@lru_cache
def _build_supervisor() -> Supervisor:
    """Cached singleton, same pattern as db/session.py's `get_engine()`.

    Previously this construction happened fresh on every single request:
    a new LLM SDK client (which opens its own connection pool internally)
    and a new httpx-backed client for each of PubMed/DGIdb/ClinicalTrials.gov/
    VEP, per request. Under concurrent load that's unbounded socket/client
    churn for objects that are otherwise stateless and safe to share — none
    of them hold per-request state, they're pure "here's how to call this
    external service" wrappers. Settings are read once, at first build, via
    the already-cached `get_settings()`; nothing here reacts to a settings
    change mid-process, which matches how every other cached singleton in
    this codebase (get_engine, get_session_factory) already behaves.
    """
    settings = get_settings()
    fast_llm = build_llm_client(settings, fast=True)
    reasoning_llm = build_llm_client(settings, fast=False)

    pubmed = NcbiPubMedClient(tool_email=settings.ncbi_tool_email, api_key=settings.pubmed_api_key)
    dgidb = RealDgidbClient(settings.dgidb_base_url)
    clinicaltrials = RealClinicalTrialsClient(settings.clinicaltrials_base_url)

    # VEP: real REST client always available (free/keyless); genomic
    # coordinates only exist once real variant calling has run, so this is
    # exercised for real once agent 3's output is wired to agent 4 (see
    # docs/scaling-to-real-genomes.md) — Fake is used for the
    # candidate-genes-only entry point, which never calls agent 4 at all.
    vep: VepClient
    # Deliberately if/else, not the ternary ruff (SIM108) suggests: mypy
    # loses the declared VepClient annotation as inference context inside a
    # parenthesized ternary here and infers the two branches' common type as
    # bare `object` instead — the if/else form type-checks correctly against
    # the annotation.
    if settings.ensembl_vep_base_url:  # noqa: SIM108
        vep = RealVepClient(settings.ensembl_vep_base_url)
    else:
        vep = FakeVepClient()

    # Explicit Protocol-typed locals — without them mypy infers the variable's
    # type from whichever branch it sees first and flags the other branch's
    # assignment as incompatible, even though both classes satisfy the same
    # Protocol (see AlignmentBackend/VariantCallingBackend).
    alignment_backend: AlignmentBackend
    variant_calling_backend: VariantCallingBackend
    if settings.batch_account_url:
        alignment_backend = AzureBatchAlignmentBackend(settings.batch_account_url, settings.batch_pool_id)
        variant_calling_backend = AzureBatchVariantCallingBackend(settings.batch_account_url, settings.batch_pool_id)
    else:
        alignment_backend = FakeAlignmentBackend()
        variant_calling_backend = FakeVariantCallingBackend()

    return Supervisor(
        qc_agent=QcAgent(fast_llm),
        alignment_agent=AlignmentOrchestrationAgent(alignment_backend),
        variant_calling_agent=VariantCallingAgent(variant_calling_backend),
        annotation_agent=VariantAnnotationAgent(fast_llm, vep),
        association_agent=CohortAssociationAgent(),
        pathway_agent=PathwayEnrichmentAgent(),
        literature_agent=LiteratureMiningAgent(fast_llm, pubmed),
        druggability_agent=DruggabilityAssessmentAgent(fast_llm, dgidb),
        competitive_agent=CompetitiveIntelligenceAgent(fast_llm, clinicaltrials),
        prioritization_agent=TargetPrioritizationAgent(reasoning_llm),
        experiment_agent=ExperimentDesignAgent(fast_llm),
        report_agent=ReportGenerationAgent(reasoning_llm),
    )


@lru_cache
def get_run_rate_limiter() -> SlidingWindowRateLimiter:
    """One process-lifetime instance — a limiter rebuilt per request would
    never accumulate any call history and would allow, not limit, everything.
    """
    settings = get_settings()
    return SlidingWindowRateLimiter(
        max_calls=settings.run_rate_limit_max_calls, window_seconds=settings.run_rate_limit_window_seconds
    )


def get_supervisor() -> Supervisor:
    """The actual FastAPI dependency — thin wrapper over the cached
    singleton builder above. Kept as a separate public function (rather than
    routers depending on `_build_supervisor` directly) so tests can swap it
    via `app.dependency_overrides[get_supervisor] = ...` without reaching
    into the private cache.
    """
    return _build_supervisor()
