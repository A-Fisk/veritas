"""Tests for veritas.retrieval — all network calls are mocked."""

import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from veritas.retrieval import (
    AbstractFetchError,
    PaperNotFoundError,
    _fetch_one,
    fetch_abstracts,
    fetch_abstracts_sync,
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
async def test_fetch_one_rate_limit_raises_abstract_fetch_error() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _make_response(429)
    with pytest.raises(AbstractFetchError) as exc_info:
        await _fetch_one(client, "2301.00001", api_key=None)
    assert exc_info.value.paper_id == "2301.00001"


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
