"""ClinicalTrials.gov API v2 client — free, keyless, official government API.

Used by the Competitive Intelligence Agent (9) to check whether a candidate
gene/target already has active drug development programs — the single
question that determines whether pursuing it duplicates existing work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

_BASE_URL = "https://clinicaltrials.gov/api/v2"


@dataclass
class TrialRecord:
    nct_id: str
    title: str
    phase: str
    status: str
    sponsor: str


class ClinicalTrialsClient(Protocol):
    async def search_by_gene(self, gene_symbol: str, max_results: int = 10) -> list[TrialRecord]: ...


class RealClinicalTrialsClient:
    def __init__(self, base_url: str = _BASE_URL) -> None:
        self._base_url = base_url

    @retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    async def search_by_gene(self, gene_symbol: str, max_results: int = 10) -> list[TrialRecord]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{self._base_url}/studies",
                params={
                    "query.term": gene_symbol,
                    "pageSize": max_results,
                    "fields": "NCTId,BriefTitle,Phase,OverallStatus,LeadSponsorName",
                },
            )
            response.raise_for_status()
            studies = response.json().get("studies", [])

        records: list[TrialRecord] = []
        for study in studies:
            protocol = study.get("protocolSection", {})
            ident = protocol.get("identificationModule", {})
            status_mod = protocol.get("statusModule", {})
            design_mod = protocol.get("designModule", {})
            sponsor_mod = protocol.get("sponsorCollaboratorsModule", {})
            phases = design_mod.get("phases", [])
            records.append(
                TrialRecord(
                    nct_id=ident.get("nctId", ""),
                    title=ident.get("briefTitle", ""),
                    phase=phases[0] if phases else "N/A",
                    status=status_mod.get("overallStatus", "UNKNOWN"),
                    sponsor=sponsor_mod.get("leadSponsor", {}).get("name", "Unknown"),
                )
            )
        return records


class FakeClinicalTrialsClient:
    def __init__(self, records: list[TrialRecord] | None = None) -> None:
        self._records = records or []

    async def search_by_gene(self, gene_symbol: str, max_results: int = 10) -> list[TrialRecord]:
        return self._records[:max_results]
