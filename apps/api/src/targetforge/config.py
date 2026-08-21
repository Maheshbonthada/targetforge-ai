"""Centralized, typed application configuration. Same pattern as TrialReady:
everything from environment variables, nothing secret hardcoded, Azure Key
Vault references in production.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["local", "dev", "staging", "prod"] = "local"
    app_name: str = "targetforge-api"
    log_level: str = "INFO"
    api_root_path: str = ""
    cors_allowed_origins: str = "http://localhost:5173,http://localhost:3000"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    # --- Database ---
    database_url: str = Field(
        default="postgresql+asyncpg://targetforge:targetforge@localhost:5434/targetforge"
    )
    db_pool_size: int = 5
    db_max_overflow: int = 5

    # --- Pipeline run execution ---
    # Hard wall-clock ceiling on one background-task run (see
    # api/routers/runs.py's `_execute_run`). Each external client already has
    # its own per-call timeout+retry (external/*.py), but nothing previously
    # bounded the *whole* run — a stuck run held its background task (and,
    # implicitly, the request-serving replica's resources) forever, showing
    # up as a permanently "running" row with no automatic recovery. 25 genes
    # x 7 LLM-calling agents x tenacity's up-to-3-retries-at-8s-backoff worst
    # case is comfortably under 10 minutes; this is set well above the
    # expected case, not tuned to the happy path.
    run_timeout_seconds: int = 900
    # On startup, any run still marked RUNNING from a previous process
    # lifetime is unreachable — nothing is going to resume executing its
    # background task — and is reconciled to FAILED (see main.py's lifespan).
    # This does not fix the underlying "BackgroundTasks isn't durable across
    # restarts" limitation (see docs/scaling-to-real-genomes.md §5); it stops
    # that limitation from presenting as a silently-stuck row forever.
    orphaned_run_reconciliation_enabled: bool = True

    # --- Cost protection (see core/rate_limit.py) ---
    run_rate_limit_max_calls: int = 5
    run_rate_limit_window_seconds: float = 600.0

    # --- Auth ---
    entra_tenant_id: str = ""
    entra_client_id: str = ""
    entra_issuer: str = ""
    entra_audience: str = ""
    auth_disabled_for_local_dev: bool = False

    # --- LLM provider (see trialready's config.py for the rationale) ---
    ai_provider: Literal["azure_openai", "openai"] = "azure_openai"
    azure_openai_endpoint: str = ""
    azure_openai_deployment_reasoning: str = "gpt-4o"  # prioritization, report writing — needs real reasoning
    azure_openai_deployment_fast: str = "gpt-4o-mini"  # literature/druggability/competitive summarization
    azure_openai_api_version: str = "2024-08-01-preview"
    openai_api_key: str = ""
    openai_model_reasoning: str = "gpt-4o"
    openai_model_fast: str = "gpt-4o-mini"

    # --- Azure Blob Storage (genomic files: FASTQ, BAM, VCF) ---
    storage_account_url: str = ""
    genomic_files_container: str = "genomic-files"

    # --- Azure Batch (real alignment/variant-calling compute — see docs/scaling-to-real-genomes.md) ---
    batch_account_url: str = ""
    batch_pool_id: str = "targetforge-compute-pool"

    # --- External bioinformatics/evidence APIs (all free/keyless unless noted) ---
    pubmed_api_key: str = ""  # optional — raises NCBI rate limit from 3/s to 10/s
    ncbi_tool_email: str = "research@targetforge.example"  # required courtesy field by NCBI E-utilities
    ensembl_vep_base_url: str = "https://rest.ensembl.org"
    dgidb_base_url: str = "https://dgidb.org/api/graphql"
    clinicaltrials_base_url: str = "https://clinicaltrials.gov/api/v2"

    key_vault_url: str = ""
    applicationinsights_connection_string: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
