"""Tests for veritas.retrieval — all network calls are mocked."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from veritas.retrieval import (
    AbstractFetchError,
    PaperNotFoundError,
    SearchError,
    _fetch_one,
    _search_arxiv,
    _search_pubmed,
    _search_s2,
    fetch_abstracts,
    fetch_abstracts_sync,
    search_papers,
    search_papers_sync,
)


def _make_response(status_code: int, json_body: dict | None = None) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    if json_body is not None:
        resp.json.return_value = json_body
    return resp


# ---------------------------------------------------------------------------
# _fetch_one
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_one_success() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _make_response(
        200, {"title": "Great Paper", "abstract": "An abstract."}
    )
    result = await _fetch_one(client, "2301.00001", api_key=None)
    assert result["paper_id"] == "2301.00001"
    assert result["title"] == "Great Paper"
    assert result["abstract"] == "An abstract."


@pytest.mark.asyncio
async def test_fetch_one_404_raises_paper_not_found() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _make_response(404)
    with pytest.raises(PaperNotFoundError) as exc_info:
        await _fetch_one(client, "bad-id", api_key=None)
    assert exc_info.value.paper_id == "bad-id"


@pytest.mark.asyncio
async def test_fetch_one_rate_limit_retries_then_raises() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    # Always returns 429 — exhausts all retries
    client.get.return_value = _make_response(429)
    with patch("veritas.retrieval.asyncio.sleep") as mock_sleep:
        mock_sleep.return_value = None
        with pytest.raises(AbstractFetchError) as exc_info:
            await _fetch_one(client, "2301.00001", api_key=None)
    assert exc_info.value.paper_id == "2301.00001"
    assert "after all retries" in str(exc_info.value)
    # Should have slept 3 times (1s, 2s, 4s + jitter) before giving up
    assert mock_sleep.call_count == 3


@pytest.mark.asyncio
async def test_fetch_one_rate_limit_succeeds_after_retry() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.side_effect = [
        _make_response(429),
        _make_response(200, {"title": "Retried Paper", "abstract": "Got it."}),
    ]
    with patch("veritas.retrieval.asyncio.sleep") as mock_sleep:
        mock_sleep.return_value = None
        result = await _fetch_one(client, "2301.00001", api_key=None)
    assert result["title"] == "Retried Paper"
    # sleep is called once with 1.0 + jitter (between 1.0 and 1.25)
    assert mock_sleep.call_count == 1
    sleep_arg = mock_sleep.call_args[0][0]
    assert 1.0 <= sleep_arg <= 1.25 + 1e-9


@pytest.mark.asyncio
async def test_fetch_one_server_error_retries_then_raises() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    # Always returns 500 — exhausts all retries
    client.get.return_value = _make_response(500)
    with patch("veritas.retrieval.asyncio.sleep") as mock_sleep:
        mock_sleep.return_value = None
        with pytest.raises(AbstractFetchError) as exc_info:
            await _fetch_one(client, "xyz", api_key=None)
    assert exc_info.value.paper_id == "xyz"
    assert "after all retries" in str(exc_info.value)
    assert mock_sleep.call_count == 3


@pytest.mark.asyncio
async def test_fetch_one_server_error_succeeds_after_retry() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.side_effect = [
        _make_response(503),
        _make_response(200, {"title": "Recovered", "abstract": "OK."}),
    ]
    with patch("veritas.retrieval.asyncio.sleep") as mock_sleep:
        mock_sleep.return_value = None
        result = await _fetch_one(client, "2301.00001", api_key=None)
    assert result["title"] == "Recovered"
    assert mock_sleep.call_count == 1


@pytest.mark.asyncio
async def test_fetch_one_timeout_raises_abstract_fetch_error() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.side_effect = httpx.TimeoutException("timed out")
    with pytest.raises(AbstractFetchError):
        await _fetch_one(client, "2301.00001", api_key=None)


@pytest.mark.asyncio
async def test_fetch_one_network_error_raises_abstract_fetch_error() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.side_effect = httpx.ConnectError("connection refused")
    with pytest.raises(AbstractFetchError):
        await _fetch_one(client, "2301.00001", api_key=None)


@pytest.mark.asyncio
async def test_fetch_one_sets_api_key_header() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _make_response(200, {"title": "T", "abstract": "A"})
    await _fetch_one(client, "2301.00001", api_key="my-key")
    _, kwargs = client.get.call_args
    assert kwargs["headers"]["x-api-key"] == "my-key"


@pytest.mark.asyncio
async def test_fetch_one_no_api_key_no_header() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _make_response(200, {"title": "T", "abstract": "A"})
    await _fetch_one(client, "2301.00001", api_key=None)
    _, kwargs = client.get.call_args
    assert "x-api-key" not in kwargs["headers"]


@pytest.mark.asyncio
async def test_fetch_one_missing_fields_are_none() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _make_response(200, {})
    result = await _fetch_one(client, "2301.00001", api_key=None)
    assert result["title"] is None
    assert result["abstract"] is None


# ---------------------------------------------------------------------------
# fetch_abstracts (async, high-level)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_abstracts_parallel_success() -> None:
    responses = [
        _make_response(200, {"title": f"Paper {i}", "abstract": f"Abs {i}"})
        for i in range(3)
    ]
    with patch("veritas.retrieval.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=responses)
        mock_cls.return_value = mock_client

        results = await fetch_abstracts(["id0", "id1", "id2"])

    assert len(results) == 3
    assert results[0]["paper_id"] == "id0"


# ---------------------------------------------------------------------------
# fetch_abstracts_sync
# ---------------------------------------------------------------------------


def test_fetch_abstracts_sync_success() -> None:
    with patch("veritas.retrieval.asyncio.run") as mock_run:
        mock_run.return_value = [{"paper_id": "p1", "title": "T", "abstract": "A"}]
        result = fetch_abstracts_sync(["p1"])
    assert result[0]["paper_id"] == "p1"


def test_fetch_abstracts_sync_reads_env_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERITAS_S2_API_KEY", "env-key")
    with patch("veritas.retrieval.asyncio.run") as mock_run:
        mock_run.return_value = [{"paper_id": "p1", "title": None, "abstract": None}]
        fetch_abstracts_sync(["p1"])
    mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# _search_s2 (unit tests for S2-specific helper)
# ---------------------------------------------------------------------------


def _make_search_response(
    status_code: int, papers: list[dict] | None = None
) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    if papers is not None:
        resp.json.return_value = {"total": len(papers), "data": papers}
    return resp


@pytest.mark.asyncio
async def test_search_s2_success() -> None:
    s2_papers = [
        {"paperId": "abc123", "title": "Great Study", "abstract": "Shows X."},
    ]
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _make_search_response(200, s2_papers)
    results = await _search_s2(client, "melatonin", limit=1, api_key=None)
    assert results[0]["paper_id"] == "abc123"
    assert results[0]["source"] == "semantic_scholar"


@pytest.mark.asyncio
async def test_search_s2_rate_limit_retries_then_raises() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _make_search_response(429)
    with patch("veritas.retrieval.asyncio.sleep") as mock_sleep:
        mock_sleep.return_value = None
        with pytest.raises(SearchError) as exc_info:
            await _search_s2(client, "test", limit=5, api_key=None)
    assert "after all retries" in str(exc_info.value)
    assert mock_sleep.call_count == 3


@pytest.mark.asyncio
async def test_search_s2_rate_limit_succeeds_after_retry() -> None:
    s2_papers = [{"paperId": "abc123", "title": "Retried", "abstract": "OK."}]
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.side_effect = [
        _make_search_response(429),
        _make_search_response(200, s2_papers),
    ]
    with patch("veritas.retrieval.asyncio.sleep") as mock_sleep:
        mock_sleep.return_value = None
        results = await _search_s2(client, "test", limit=5, api_key=None)
    assert results[0]["paper_id"] == "abc123"
    assert mock_sleep.call_count == 1
    sleep_arg = mock_sleep.call_args[0][0]
    assert 1.0 <= sleep_arg <= 1.25 + 1e-9


@pytest.mark.asyncio
async def test_search_s2_server_error_retries_then_raises() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _make_search_response(500)
    with patch("veritas.retrieval.asyncio.sleep") as mock_sleep:
        mock_sleep.return_value = None
        with pytest.raises(SearchError) as exc_info:
            await _search_s2(client, "test", limit=5, api_key=None)
    assert "after all retries" in str(exc_info.value)
    assert mock_sleep.call_count == 3


@pytest.mark.asyncio
async def test_search_s2_sets_api_key_header() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _make_search_response(200, [])
    await _search_s2(client, "test", limit=5, api_key="my-key")
    _, kwargs = client.get.call_args
    assert kwargs["headers"]["x-api-key"] == "my-key"


# ---------------------------------------------------------------------------
# _search_pubmed (unit tests)
# ---------------------------------------------------------------------------

_PUBMED_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID Version="1">12345678</PMID>
      <Article>
        <ArticleTitle>Melatonin and Sleep Quality</ArticleTitle>
        <Abstract>
          <AbstractText>Melatonin improves sleep onset latency.</AbstractText>
        </Abstract>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>"""

_PUBMED_ESEARCH_JSON = {
    "esearchresult": {"idlist": ["12345678"]}
}


@pytest.mark.asyncio
async def test_search_pubmed_success() -> None:
    esearch_resp = _make_response(200, _PUBMED_ESEARCH_JSON)
    efetch_resp = MagicMock(spec=httpx.Response)
    efetch_resp.status_code = 200
    efetch_resp.text = _PUBMED_XML

    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.side_effect = [esearch_resp, efetch_resp]

    results = await _search_pubmed(client, "melatonin sleep", limit=1)
    assert len(results) == 1
    assert results[0]["paper_id"] == "PMID:12345678"
    assert results[0]["title"] == "Melatonin and Sleep Quality"
    assert results[0]["abstract"] == "Melatonin improves sleep onset latency."
    assert results[0]["source"] == "pubmed"


@pytest.mark.asyncio
async def test_search_pubmed_empty_results() -> None:
    esearch_resp = _make_response(200, {"esearchresult": {"idlist": []}})
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = esearch_resp
    results = await _search_pubmed(client, "obscure query", limit=5)
    assert results == []


@pytest.mark.asyncio
async def test_search_pubmed_esearch_error_raises() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _make_response(500)
    with pytest.raises(SearchError) as exc_info:
        await _search_pubmed(client, "test", limit=5)
    assert "source: pubmed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_search_pubmed_efetch_error_raises() -> None:
    esearch_resp = _make_response(200, _PUBMED_ESEARCH_JSON)
    efetch_resp = _make_response(503)
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.side_effect = [esearch_resp, efetch_resp]
    with pytest.raises(SearchError) as exc_info:
        await _search_pubmed(client, "test", limit=5)
    assert "source: pubmed" in str(exc_info.value)


# ---------------------------------------------------------------------------
# _search_arxiv (unit tests)
# ---------------------------------------------------------------------------

_ARXIV_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2301.00001v1</id>
    <title>Sleep Patterns in Mammals</title>
    <summary>A study of mammalian sleep patterns across species.</summary>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2302.00002v2</id>
    <title>Circadian Rhythms Review</title>
    <summary>Comprehensive review of circadian biology.</summary>
  </entry>
</feed>"""


@pytest.mark.asyncio
async def test_search_arxiv_success() -> None:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.text = _ARXIV_XML

    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = resp

    results = await _search_arxiv(client, "sleep", limit=2)
    assert len(results) == 2
    assert results[0]["paper_id"] == "2301.00001v1"
    assert results[0]["title"] == "Sleep Patterns in Mammals"
    assert results[0]["abstract"] == "A study of mammalian sleep patterns across species."
    assert results[0]["source"] == "arxiv"
    assert results[1]["paper_id"] == "2302.00002v2"


@pytest.mark.asyncio
async def test_search_arxiv_error_raises() -> None:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 503
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = resp
    with pytest.raises(SearchError) as exc_info:
        await _search_arxiv(client, "test", limit=5)
    assert "source: arxiv" in str(exc_info.value)


# ---------------------------------------------------------------------------
# search_papers (integration — fallback chain)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_papers_success() -> None:
    s2_papers = [
        {"paperId": "abc123", "title": "Great Study", "abstract": "Shows X."},
        {"paperId": "def456", "title": "Another Study", "abstract": "Shows Y."},
    ]
    with patch("veritas.retrieval.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=_make_search_response(200, s2_papers))
        mock_cls.return_value = mock_client

        results = await search_papers("melatonin light", limit=2)

    assert len(results) == 2
    assert results[0]["paper_id"] == "abc123"
    assert results[0]["title"] == "Great Study"
    assert results[1]["paper_id"] == "def456"
    assert results[0]["source"] == "semantic_scholar"


@pytest.mark.asyncio
async def test_search_papers_filters_missing_paper_id() -> None:
    s2_papers = [
        {"paperId": "abc123", "title": "Good", "abstract": "A."},
        {"paperId": None, "title": "No ID", "abstract": "B."},
        {"title": "Also no ID", "abstract": "C."},
    ]
    with patch("veritas.retrieval.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=_make_search_response(200, s2_papers))
        mock_cls.return_value = mock_client

        results = await search_papers("test query")

    assert len(results) == 1
    assert results[0]["paper_id"] == "abc123"


@pytest.mark.asyncio
async def test_search_papers_empty_results() -> None:
    with patch("veritas.retrieval.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=_make_search_response(200, []))
        mock_cls.return_value = mock_client

        results = await search_papers("obscure query")

    assert results == []


@pytest.mark.asyncio
async def test_search_papers_falls_back_to_pubmed_on_s2_rate_limit() -> None:
    """When S2 returns 429 after all retries, fall back to PubMed."""
    esearch_resp = _make_response(200, _PUBMED_ESEARCH_JSON)
    efetch_resp = MagicMock(spec=httpx.Response)
    efetch_resp.status_code = 200
    efetch_resp.text = _PUBMED_XML

    def mock_get(url: str, **kwargs):  # type: ignore[no-untyped-def]
        if "semanticscholar" in url:
            return _make_search_response(429)
        if "esearch" in url:
            return esearch_resp
        return efetch_resp

    with patch("veritas.retrieval.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=mock_get)
        mock_cls.return_value = mock_client

        with patch("veritas.retrieval.asyncio.sleep"):
            results = await search_papers("melatonin sleep", limit=1)

    assert len(results) == 1
    assert results[0]["source"] == "pubmed"
    assert results[0]["paper_id"] == "PMID:12345678"


@pytest.mark.asyncio
async def test_search_papers_falls_back_to_arxiv_on_pubmed_failure() -> None:
    """When S2 and PubMed both fail, fall back to arXiv."""
    arxiv_resp = MagicMock(spec=httpx.Response)
    arxiv_resp.status_code = 200
    arxiv_resp.text = _ARXIV_XML

    def mock_get(url: str, **kwargs):  # type: ignore[no-untyped-def]
        if "semanticscholar" in url:
            return _make_search_response(429)
        if "ncbi.nlm.nih.gov" in url:
            return _make_response(503)
        return arxiv_resp

    with patch("veritas.retrieval.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=mock_get)
        mock_cls.return_value = mock_client

        with patch("veritas.retrieval.asyncio.sleep"):
            results = await search_papers("sleep", limit=2)

    assert len(results) == 2
    assert results[0]["source"] == "arxiv"
    assert results[0]["paper_id"] == "2301.00001v1"


@pytest.mark.asyncio
async def test_search_papers_all_sources_fail_raises() -> None:
    """When all three sources fail, SearchError is raised."""
    with patch("veritas.retrieval.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        # All requests return 429
        mock_client.get = AsyncMock(return_value=_make_search_response(429))
        mock_cls.return_value = mock_client

        with patch("veritas.retrieval.asyncio.sleep"):
            with pytest.raises(SearchError):
                await search_papers("test")


@pytest.mark.asyncio
async def test_search_papers_s2_rate_limit_sleeps_three_times_before_fallback() -> None:
    """S2 retry logic (3 sleeps) fires before the fallback chain begins."""
    arxiv_resp = MagicMock(spec=httpx.Response)
    arxiv_resp.status_code = 200
    arxiv_resp.text = _ARXIV_XML

    def mock_get(url: str, **kwargs):  # type: ignore[no-untyped-def]
        if "semanticscholar" in url:
            return _make_search_response(429)
        if "ncbi.nlm.nih.gov" in url:
            return _make_response(503)
        return arxiv_resp

    with patch("veritas.retrieval.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=mock_get)
        mock_cls.return_value = mock_client

        with patch("veritas.retrieval.asyncio.sleep") as mock_sleep:
            mock_sleep.return_value = None
            results = await search_papers("sleep", limit=2)

    # S2 retries 3 times before falling back; PubMed/arXiv don't retry
    assert mock_sleep.call_count == 3
    assert results[0]["source"] == "arxiv"


@pytest.mark.asyncio
async def test_search_papers_timeout_raises_search_error() -> None:
    with patch("veritas.retrieval.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        mock_cls.return_value = mock_client

        with pytest.raises(SearchError):
            await search_papers("test")


@pytest.mark.asyncio
async def test_search_papers_network_error_raises_search_error() -> None:
    with patch("veritas.retrieval.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_cls.return_value = mock_client

        with pytest.raises(SearchError):
            await search_papers("test")


@pytest.mark.asyncio
async def test_search_papers_sets_api_key_header() -> None:
    with patch("veritas.retrieval.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=_make_search_response(200, []))
        mock_cls.return_value = mock_client

        await search_papers("test", api_key="my-key")

    _, kwargs = mock_client.get.call_args
    assert kwargs["headers"]["x-api-key"] == "my-key"


# ---------------------------------------------------------------------------
# search_papers_sync
# ---------------------------------------------------------------------------


def test_search_papers_sync_success() -> None:
    with patch("veritas.retrieval.asyncio.run") as mock_run:
        mock_run.return_value = [{"paper_id": "abc", "title": "T", "abstract": "A"}]
        result = search_papers_sync("some keywords")
    assert result[0]["paper_id"] == "abc"
