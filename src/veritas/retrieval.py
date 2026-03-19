import asyncio
import os

import httpx

S2_BASE = "https://api.semanticscholar.org/graph/v1/paper"
S2_SEARCH = "https://api.semanticscholar.org/graph/v1/paper/search"
FIELDS = "title,abstract"
DEFAULT_TIMEOUT = int(os.environ.get("VERITAS_TIMEOUT", "30"))


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


async def search_papers(
    keywords: str,
    limit: int = 5,
    api_key: str | None = None,
) -> list[dict[str, str | None]]:
    """Search Semantic Scholar by keyword query, returning papers with abstracts."""
    if api_key is None:
        api_key = os.environ.get("VERITAS_S2_API_KEY")

    headers: dict[str, str] = {}
    if api_key:
        headers["x-api-key"] = api_key

    async with httpx.AsyncClient() as client:
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
        }
        for p in papers
        if p.get("paperId")
    ]


def search_papers_sync(
    keywords: str,
    limit: int = 5,
    api_key: str | None = None,
) -> list[dict[str, str | None]]:
    """Synchronous wrapper for search_papers."""
    return asyncio.run(search_papers(keywords, limit, api_key))
