"""DGIdb (Drug-Gene Interaction Database) GraphQL client — free, keyless,
maintained by Washington University. Used by the Druggability Assessment
Agent (8) to find known ligands/drugs for a candidate gene, the primary
signal for "has anyone ever drugged this target before."
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

_QUERY = """
query getInteractions($names: [String!]) {
  genes(names: $names) {
    nodes {
      name
      interactions {
        drug { name }
        interactionScore
        interactionTypes { type }
      }
    }
  }
}
"""


@dataclass
class DrugInteraction:
    drug_name: str
    interaction_types: list[str]
    interaction_score: float | None


class DgidbClient(Protocol):
    async def get_interactions(self, gene_symbol: str) -> list[DrugInteraction]: ...


class RealDgidbClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url

    @retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    async def get_interactions(self, gene_symbol: str) -> list[DrugInteraction]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                self._base_url, json={"query": _QUERY, "variables": {"names": [gene_symbol]}}
            )
            response.raise_for_status()
            payload = response.json()

        nodes = payload.get("data", {}).get("genes", {}).get("nodes", [])
        if not nodes:
            return []
        interactions = nodes[0].get("interactions", [])
        return [
            DrugInteraction(
                drug_name=i.get("drug", {}).get("name", "unknown"),
                interaction_types=[t.get("type", "") for t in i.get("interactionTypes", [])],
                interaction_score=i.get("interactionScore"),
            )
            for i in interactions
        ]


class FakeDgidbClient:
    def __init__(self, interactions_by_gene: dict[str, list[DrugInteraction]] | None = None) -> None:
        self._interactions_by_gene = interactions_by_gene or {}

    async def get_interactions(self, gene_symbol: str) -> list[DrugInteraction]:
        return self._interactions_by_gene.get(gene_symbol, [])
