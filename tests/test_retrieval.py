"""Tests for veritas.retrieval — all network calls are mocked."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from veritas.retrieval import (
    AbstractFetchError,
    PaperNotFoundError,
    SearchError,
    _fetch_one,
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
    assert "Rate limit exceeded" in str(exc_info.value)
    # Should have slept 3 times (1s, 2s, 4s) before giving up
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
    mock_sleep.assert_called_once_with(1.0)


@pytest.mark.asyncio
async def test_fetch_one_server_error_raises_abstract_fetch_error() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _make_response(500)
    with pytest.raises(AbstractFetchError):
        await _fetch_one(client, "xyz", api_key=None)


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
# search_papers (async)
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
async def test_search_papers_rate_limit_retries_then_raises() -> None:
    with patch("veritas.retrieval.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=_make_search_response(429))
        mock_cls.return_value = mock_client

        with patch("veritas.retrieval.asyncio.sleep") as mock_sleep:
            mock_sleep.return_value = None
            with pytest.raises(SearchError) as exc_info:
                await search_papers("test")

    assert "Rate limit exceeded" in str(exc_info.value)
    assert mock_sleep.call_count == 3


@pytest.mark.asyncio
async def test_search_papers_rate_limit_succeeds_after_retry() -> None:
    s2_papers = [{"paperId": "abc123", "title": "Retried", "abstract": "OK."}]
    with patch("veritas.retrieval.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(
            side_effect=[
                _make_search_response(429),
                _make_search_response(200, s2_papers),
            ]
        )
        mock_cls.return_value = mock_client

        with patch("veritas.retrieval.asyncio.sleep") as mock_sleep:
            mock_sleep.return_value = None
            results = await search_papers("test")

    assert len(results) == 1
    assert results[0]["paper_id"] == "abc123"
    mock_sleep.assert_called_once_with(1.0)


@pytest.mark.asyncio
async def test_search_papers_non_200_raises_search_error() -> None:
    with patch("veritas.retrieval.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=_make_search_response(500))
        mock_cls.return_value = mock_client

        with pytest.raises(SearchError):
            await search_papers("test")


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
