import asyncio
import os
import random
import xml.etree.ElementTree as ET

import httpx

S2_BASE = "https://api.semanticscholar.org/graph/v1/paper"
S2_SEARCH = "https://api.semanticscholar.org/graph/v1/paper/search"
PUBMED_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
ARXIV_SEARCH = "https://export.arxiv.org/api/query"
FIELDS = "title,abstract"
DEFAULT_TIMEOUT = int(os.environ.get("VERITAS_TIMEOUT", "30"))
_RETRY_DELAYS = (1.0, 2.0, 4.0)  # exponential backoff for 429/5xx responses
_JITTER_MAX = 0.25  # max jitter fraction added to each retry delay
_ATOM_NS = "http://www.w3.org/2005/Atom"


class PaperNotFoundError(Exception):
    def __init__(self, paper_id: str) -> None:
        self.paper_id = paper_id
        super().__init__(f"Semantic Scholar returned 404 for paper ID {paper_id}")


class AbstractFetchError(Exception):
    def __init__(self, paper_id: str, message: str) -> None:
        self.paper_id = paper_id
        super().__init__(message)


class SearchError(Exception):
    pass


async def _fetch_one(
    client: httpx.AsyncClient,
    paper_id: str,
    api_key: str | None,
) -> dict[str, str | None]:
    headers: dict[str, str] = {}
    if api_key:
        headers["x-api-key"] = api_key

    delays = iter(_RETRY_DELAYS)
    while True:
        try:
            response = await client.get(
                f"{S2_BASE}/{paper_id}",
                params={"fields": FIELDS},
                headers=headers,
                timeout=DEFAULT_TIMEOUT,
            )
        except httpx.TimeoutException as e:
            raise AbstractFetchError(paper_id, f"Request timed out for {paper_id}") from e
        except httpx.RequestError as e:
            raise AbstractFetchError(paper_id, f"Network error for {paper_id}: {e}") from e

        if response.status_code == 404:
            raise PaperNotFoundError(paper_id)
        if response.status_code == 429 or response.status_code >= 500:
            delay = next(delays, None)
            if delay is None:
                raise AbstractFetchError(
                    paper_id,
                    f"Semantic Scholar returned {response.status_code} for paper {paper_id} after all retries.",
                )
            await asyncio.sleep(delay + random.uniform(0, delay * _JITTER_MAX))
            continue
        if response.status_code != 200:
            raise AbstractFetchError(
                paper_id,
                f"Semantic Scholar returned {response.status_code} for {paper_id}",
            )

        data = response.json()
        return {
            "paper_id": paper_id,
            "title": data.get("title"),
            "abstract": data.get("abstract"),
        }


async def fetch_abstracts(
    paper_ids: list[str],
    api_key: str | None = None,
) -> list[dict[str, str | None]]:
    """Fetch title and abstract for each paper_id in parallel."""
    if api_key is None:
        api_key = os.environ.get("VERITAS_S2_API_KEY")

    async with httpx.AsyncClient() as client:
        tasks = [_fetch_one(client, pid, api_key) for pid in paper_ids]
        return await asyncio.gather(*tasks)


def fetch_abstracts_sync(
    paper_ids: list[str],
    api_key: str | None = None,
) -> list[dict[str, str | None]]:
    return asyncio.run(fetch_abstracts(paper_ids, api_key))


async def _search_s2(
    client: httpx.AsyncClient,
    keywords: str,
    limit: int,
    api_key: str | None,
) -> list[dict[str, str | None]]:
    headers: dict[str, str] = {}
    if api_key:
        headers["x-api-key"] = api_key

    delays = iter(_RETRY_DELAYS)
    while True:
        try:
            response = await client.get(
                S2_SEARCH,
                params={"query": keywords, "fields": FIELDS, "limit": limit},
                headers=headers,
                timeout=DEFAULT_TIMEOUT,
            )
        except httpx.TimeoutException as e:
            raise SearchError(f"Search request timed out for query: {keywords!r}") from e
        except httpx.RequestError as e:
            raise SearchError(f"Network error during search: {e}") from e

        if response.status_code == 429 or response.status_code >= 500:
            delay = next(delays, None)
            if delay is None:
                raise SearchError(
                    f"Semantic Scholar returned {response.status_code} for search query: {keywords!r} after all retries."
                )
            await asyncio.sleep(delay + random.uniform(0, delay * _JITTER_MAX))
            continue
        break

    if response.status_code != 200:
        raise SearchError(
            f"Semantic Scholar search returned {response.status_code} for query: {keywords!r}"
        )

    data = response.json()
    papers = data.get("data", [])
    return [
        {
            "paper_id": p.get("paperId", ""),
            "title": p.get("title"),
            "abstract": p.get("abstract"),
            "source": "semantic_scholar",
        }
        for p in papers
        if p.get("paperId")
    ]


async def _search_pubmed(
    client: httpx.AsyncClient,
    keywords: str,
    limit: int,
) -> list[dict[str, str | None]]:
    try:
        esearch_resp = await client.get(
            PUBMED_ESEARCH,
            params={"db": "pubmed", "term": keywords, "retmax": limit, "retmode": "json"},
            timeout=DEFAULT_TIMEOUT,
        )
    except httpx.TimeoutException as e:
        raise SearchError(f"PubMed esearch timed out for query: {keywords!r} (source: pubmed)") from e
    except httpx.RequestError as e:
        raise SearchError(f"PubMed esearch network error: {e} (source: pubmed)") from e

    if esearch_resp.status_code != 200:
        raise SearchError(
            f"PubMed esearch returned {esearch_resp.status_code} for query: {keywords!r} (source: pubmed)"
        )

    pmids = esearch_resp.json().get("esearchresult", {}).get("idlist", [])
    if not pmids:
        return []

    try:
        efetch_resp = await client.get(
            PUBMED_EFETCH,
            params={"db": "pubmed", "id": ",".join(pmids), "rettype": "abstract", "retmode": "xml"},
            timeout=DEFAULT_TIMEOUT,
        )
    except httpx.TimeoutException as e:
        raise SearchError(f"PubMed efetch timed out for query: {keywords!r} (source: pubmed)") from e
    except httpx.RequestError as e:
        raise SearchError(f"PubMed efetch network error: {e} (source: pubmed)") from e

    if efetch_resp.status_code != 200:
        raise SearchError(
            f"PubMed efetch returned {efetch_resp.status_code} for query: {keywords!r} (source: pubmed)"
        )

    root = ET.fromstring(efetch_resp.text)
    results = []
    for article in root.findall(".//PubmedArticle"):
        pmid_el = article.find(".//PMID")
        title_el = article.find(".//ArticleTitle")
        abstract_el = article.find(".//AbstractText")
        pmid = pmid_el.text if pmid_el is not None else None
        if not pmid:
            continue
        results.append(
            {
                "paper_id": f"PMID:{pmid}",
                "title": title_el.text if title_el is not None else None,
                "abstract": abstract_el.text if abstract_el is not None else None,
                "source": "pubmed",
            }
        )
    return results


async def _search_arxiv(
    client: httpx.AsyncClient,
    keywords: str,
    limit: int,
) -> list[dict[str, str | None]]:
    try:
        response = await client.get(
            ARXIV_SEARCH,
            params={"search_query": f"all:{keywords}", "max_results": limit},
            timeout=DEFAULT_TIMEOUT,
        )
    except httpx.TimeoutException as e:
        raise SearchError(f"arXiv search timed out for query: {keywords!r} (source: arxiv)") from e
    except httpx.RequestError as e:
        raise SearchError(f"arXiv search network error: {e} (source: arxiv)") from e

    if response.status_code != 200:
        raise SearchError(
            f"arXiv returned {response.status_code} for query: {keywords!r} (source: arxiv)"
        )

    root = ET.fromstring(response.text)
    ns = {"atom": _ATOM_NS}
    results = []
    for entry in root.findall("atom:entry", ns):
        id_el = entry.find("atom:id", ns)
        title_el = entry.find("atom:title", ns)
        summary_el = entry.find("atom:summary", ns)
        if id_el is None or not id_el.text:
            continue
        arxiv_url = id_el.text.strip()
        arxiv_id = arxiv_url.split("/abs/")[-1] if "/abs/" in arxiv_url else arxiv_url
        results.append(
            {
                "paper_id": arxiv_id,
                "title": title_el.text.strip() if title_el is not None and title_el.text else None,
                "abstract": summary_el.text.strip() if summary_el is not None and summary_el.text else None,
                "source": "arxiv",
            }
        )
    return results


async def search_papers(
    keywords: str,
    limit: int = 5,
    api_key: str | None = None,
) -> list[dict[str, str | None]]:
    """Search for papers by keyword.

    Tries Semantic Scholar first. Falls back to PubMed E-utilities on 429 or
    failure after retries, then falls back to arXiv as a second fallback.
    Results are normalised to {paper_id, title, abstract, source}.
    """
    if api_key is None:
        api_key = os.environ.get("VERITAS_S2_API_KEY")

    async with httpx.AsyncClient() as client:
        try:
            return await _search_s2(client, keywords, limit, api_key)
        except SearchError:
            pass

        try:
            return await _search_pubmed(client, keywords, limit)
        except SearchError:
            pass

        return await _search_arxiv(client, keywords, limit)


def search_papers_sync(
    keywords: str,
    limit: int = 5,
    api_key: str | None = None,
) -> list[dict[str, str | None]]:
    """Synchronous wrapper for search_papers."""
    return asyncio.run(search_papers(keywords, limit, api_key))
