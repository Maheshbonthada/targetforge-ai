"""NCBI PubMed E-utilities client — free, keyless (an API key just raises the
rate limit from 3 req/s to 10 req/s; see Settings.pubmed_api_key).

This is a REAL external API, not a stub — the Literature Mining Agent (7)
calls this against the live PubMed index. Two calls per query: `esearch`
(query -> list of PMIDs) then `efetch` (PMIDs -> titles/abstracts).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from xml.etree import ElementTree

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


@dataclass
class PubMedArticle:
    pmid: str
    title: str
    abstract: str


class PubMedClient(Protocol):
    async def search(self, query: str, max_results: int = 10) -> list[PubMedArticle]: ...


class NcbiPubMedClient:
    def __init__(self, tool_email: str, api_key: str = "") -> None:
        self._tool_email = tool_email
        self._api_key = api_key

    @retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    async def search(self, query: str, max_results: int = 10) -> list[PubMedArticle]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            search_resp = await client.get(
                f"{_BASE_URL}/esearch.fcgi",
                params=self._with_auth(
                    {"db": "pubmed", "term": query, "retmax": max_results, "retmode": "json", "sort": "relevance"}
                ),
            )
            search_resp.raise_for_status()
            pmids: list[str] = search_resp.json().get("esearchresult", {}).get("idlist", [])
            if not pmids:
                return []

            fetch_resp = await client.get(
                f"{_BASE_URL}/efetch.fcgi",
                params=self._with_auth({"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"}),
            )
            fetch_resp.raise_for_status()
            return _parse_pubmed_xml(fetch_resp.text)

    def _with_auth(self, params: dict) -> dict:
        params = {**params, "tool": "targetforge", "email": self._tool_email}
        if self._api_key:
            params["api_key"] = self._api_key
        return params


def _parse_pubmed_xml(xml_text: str) -> list[PubMedArticle]:
    root = ElementTree.fromstring(xml_text)
    articles: list[PubMedArticle] = []
    for article_el in root.findall(".//PubmedArticle"):
        pmid_el = article_el.find(".//PMID")
        title_el = article_el.find(".//ArticleTitle")
        abstract_parts = article_el.findall(".//AbstractText")
        if pmid_el is None or pmid_el.text is None:
            continue
        abstract = " ".join(part.text for part in abstract_parts if part.text)
        articles.append(
            PubMedArticle(
                pmid=pmid_el.text,
                title="".join(title_el.itertext()) if title_el is not None else "",
                abstract=abstract,
            )
        )
    return articles


class FakePubMedClient:
    """Test double."""

    def __init__(self, articles: list[PubMedArticle] | None = None) -> None:
        self._articles = articles or []

    async def search(self, query: str, max_results: int = 10) -> list[PubMedArticle]:
        return self._articles[:max_results]
