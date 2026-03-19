"""Tests for veritas.verifier — Anthropic client is always mocked."""

import json
from unittest.mock import MagicMock, patch

import pytest

from veritas.verifier import _build_user_prompt, _parse_response, run_verification
from veritas.state import VerificationResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PAPERS = [
    {"paper_id": "2301.00001", "title": "Paper A", "abstract": "Abstract A."},
    {"paper_id": "2301.00002", "title": None, "abstract": None},
]

_VALID_RESPONSE = {
    "verdict": "verified",
    "confidence": 0.9,
    "reasoning": "Strong support from Paper A.",
    "papers": [
        {
            "paper_id": "2301.00001",
            "title": "Paper A",
            "verdict": "supported",
            "note": "Directly supports.",
        },
        {
            "paper_id": "2301.00002",
            "title": None,
            "verdict": "insufficient_evidence",
            "note": "No abstract.",
        },
    ],
}


def _make_message(text: str) -> MagicMock:
    content_block = MagicMock()
    content_block.text = text
    msg = MagicMock()
    msg.content = [content_block]
    return msg


# ---------------------------------------------------------------------------
# _build_user_prompt
# ---------------------------------------------------------------------------


def test_build_user_prompt_contains_claim() -> None:
    prompt = _build_user_prompt("My claim", _PAPERS)
    assert "My claim" in prompt


def test_build_user_prompt_contains_paper_ids() -> None:
    prompt = _build_user_prompt("claim", _PAPERS)
    assert "2301.00001" in prompt
    assert "2301.00002" in prompt


def test_build_user_prompt_shows_unknown_for_no_title() -> None:
    prompt = _build_user_prompt("claim", _PAPERS)
    assert "(unknown)" in prompt


def test_build_user_prompt_shows_no_abstract_placeholder() -> None:
    prompt = _build_user_prompt("claim", _PAPERS)
    assert "(no abstract available)" in prompt


# ---------------------------------------------------------------------------
# _parse_response
# ---------------------------------------------------------------------------


def test_parse_response_verdict_verified() -> None:
    result = _parse_response(json.dumps(_VALID_RESPONSE), "My claim", _PAPERS)
    assert result["verdict"] == "verified"


def test_parse_response_confidence_in_range() -> None:
    result = _parse_response(json.dumps(_VALID_RESPONSE), "My claim", _PAPERS)
    assert 0.0 <= result["confidence"] <= 1.0


def test_parse_response_reasoning_present() -> None:
    result = _parse_response(json.dumps(_VALID_RESPONSE), "My claim", _PAPERS)
    assert isinstance(result["reasoning"], str)
    assert len(result["reasoning"]) > 0


def test_parse_response_claim_preserved() -> None:
    result = _parse_response(json.dumps(_VALID_RESPONSE), "My claim", _PAPERS)
    assert result["claim"] == "My claim"


def test_parse_response_papers_list() -> None:
    result = _parse_response(json.dumps(_VALID_RESPONSE), "My claim", _PAPERS)
    assert len(result["papers"]) == 2
    assert result["papers"][0]["paper_id"] == "2301.00001"


def test_parse_response_uncertain_verdict() -> None:
    payload = {**_VALID_RESPONSE, "verdict": "uncertain"}
    result = _parse_response(json.dumps(payload), "claim", _PAPERS)
    assert result["verdict"] == "uncertain"


def test_parse_response_not_supported_verdict() -> None:
    payload = {**_VALID_RESPONSE, "verdict": "not_supported"}
    result = _parse_response(json.dumps(payload), "claim", _PAPERS)
    assert result["verdict"] == "not_supported"


def test_parse_response_invalid_json_raises() -> None:
    with pytest.raises(json.JSONDecodeError):
        _parse_response("not json", "claim", _PAPERS)


def test_parse_response_missing_key_raises() -> None:
    payload = {k: v for k, v in _VALID_RESPONSE.items() if k != "verdict"}
    with pytest.raises(KeyError):
        _parse_response(json.dumps(payload), "claim", _PAPERS)


# ---------------------------------------------------------------------------
# run_verification
# ---------------------------------------------------------------------------


def _patch_anthropic(text: str):
    """Context manager: patch anthropic.Anthropic to return a fixed message."""
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_message(text)
    return patch("veritas.verifier.anthropic.Anthropic", return_value=mock_client)


def test_run_verification_returns_verification_result() -> None:
    with _patch_anthropic(json.dumps(_VALID_RESPONSE)):
        result = run_verification("My claim", _PAPERS)
    assert isinstance(result, dict)
    assert "verdict" in result
    assert "confidence" in result
    assert "reasoning" in result
    assert "papers" in result


def test_run_verification_verdict_verified() -> None:
    with _patch_anthropic(json.dumps(_VALID_RESPONSE)):
        result = run_verification("My claim", _PAPERS)
    assert result["verdict"] == "verified"


def test_run_verification_confidence_in_range() -> None:
    with _patch_anthropic(json.dumps(_VALID_RESPONSE)):
        result = run_verification("My claim", _PAPERS)
    assert 0.0 <= result["confidence"] <= 1.0


def test_run_verification_reasoning_field_present() -> None:
    with _patch_anthropic(json.dumps(_VALID_RESPONSE)):
        result = run_verification("My claim", _PAPERS)
    assert isinstance(result["reasoning"], str)


def test_run_verification_fallback_on_bad_json() -> None:
    """Two consecutive bad responses → fallback result."""
    with _patch_anthropic("not valid json"):
        result = run_verification("claim", _PAPERS)
    assert result["verdict"] == "uncertain"
    assert result["confidence"] == 0.0
    assert "Failed to parse" in result["reasoning"]


def test_run_verification_fallback_preserves_paper_ids() -> None:
    with _patch_anthropic("not valid json"):
        result = run_verification("claim", _PAPERS)
    returned_ids = {p["paper_id"] for p in result["papers"]}
    assert "2301.00001" in returned_ids
    assert "2301.00002" in returned_ids


def test_run_verification_uses_provided_model() -> None:
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_message(json.dumps(_VALID_RESPONSE))
    with patch("veritas.verifier.anthropic.Anthropic", return_value=mock_client):
        run_verification("claim", _PAPERS, model="claude-haiku-4-5")
    call_kwargs = mock_client.messages.create.call_args
    assert call_kwargs.kwargs["model"] == "claude-haiku-4-5"


def test_run_verification_retries_once_on_bad_then_good() -> None:
    """First response is invalid JSON; second is valid."""
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        _make_message("bad json"),
        _make_message(json.dumps(_VALID_RESPONSE)),
    ]
    with patch("veritas.verifier.anthropic.Anthropic", return_value=mock_client):
        result = run_verification("claim", _PAPERS)
    assert result["verdict"] == "verified"
    assert mock_client.messages.create.call_count == 2
