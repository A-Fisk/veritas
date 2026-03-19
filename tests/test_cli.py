"""CLI integration tests (no network calls)."""

import json
from unittest.mock import patch

from typer.testing import CliRunner

from veritas.cli import app
from veritas.retrieval import SearchError
from veritas.state import PaperResult, VerificationResult

runner = CliRunner()

_MOCK_RESULT = VerificationResult(
    claim="Aspirin reduces cardiovascular risk",
    verdict="not_supported",
    confidence=0.85,
    reasoning="Both papers found no significant benefit.",
    papers=[
        PaperResult(
            paper_id="DOI:10.1000/test",
            title="Test Paper",
            verdict="not_supported",
            note="No benefit found.",
        )
    ],
)


def test_cli_missing_claim() -> None:
    result = runner.invoke(app, ["--paper-id", "2301.00001"])
    assert result.exit_code == 1


def test_cli_missing_paper_id() -> None:
    result = runner.invoke(app, ["--claim", "some claim"])
    assert result.exit_code == 1


def test_cli_success() -> None:
    with patch("veritas.cli.verify", return_value=_MOCK_RESULT):
        result = runner.invoke(
            app,
            ["--claim", "Aspirin reduces cardiovascular risk", "--paper-id", "DOI:10.1000/test"],
        )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["verdict"] == "not_supported"
    assert data["confidence"] == 0.85


def test_cli_json_stdin() -> None:
    payload = json.dumps(
        {"claim": "Aspirin reduces cardiovascular risk", "paper_ids": ["DOI:10.1000/test"]}
    )
    with patch("veritas.cli.verify", return_value=_MOCK_RESULT):
        result = runner.invoke(app, ["--json-stdin"], input=payload)
    assert result.exit_code == 0


def test_cli_claim_too_long() -> None:
    result = runner.invoke(
        app,
        ["--claim", "x" * 2001, "--paper-id", "2301.00001"],
    )
    assert result.exit_code == 1


def test_cli_too_many_paper_ids() -> None:
    args = ["--claim", "some claim"] + ["--paper-id", "p"] * 11
    result = runner.invoke(app, args)
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# --search mode
# ---------------------------------------------------------------------------

_SEARCH_PAPERS = [
    {"paper_id": "abc123", "title": "Melatonin Study", "abstract": "Constant light..."},
]

_SEARCH_MOCK_RESULT = VerificationResult(
    claim="Constant light suppresses melatonin",
    verdict="verified",
    confidence=0.9,
    reasoning="Study directly supports the claim.",
    papers=[
        PaperResult(
            paper_id="abc123",
            title="Melatonin Study",
            verdict="supported",
            note="Directly supports.",
        )
    ],
)


def test_cli_search_mode_success() -> None:
    with patch(
        "veritas.cli.search_and_verify",
        return_value=(_SEARCH_MOCK_RESULT, _SEARCH_PAPERS, "melatonin constant light"),
    ):
        result = runner.invoke(
            app, ["--claim", "Constant light suppresses melatonin", "--search"]
        )
    assert result.exit_code == 0
    # Output contains the JSON verdict; parse first JSON object from output
    data, _ = json.JSONDecoder().raw_decode(result.output)
    assert data["verdict"] == "verified"


def test_cli_search_mode_top_k_passed() -> None:
    with patch("veritas.cli.search_and_verify") as mock_sav:
        mock_sav.return_value = (
            _SEARCH_MOCK_RESULT,
            _SEARCH_PAPERS,
            "melatonin light",
        )
        runner.invoke(
            app,
            ["--claim", "Constant light suppresses melatonin", "--search", "--top-k", "10"],
        )
    call_kwargs = mock_sav.call_args
    assert call_kwargs.kwargs.get("top_k") == 10 or call_kwargs.args[1] == 10


def test_cli_search_and_paper_id_mutually_exclusive() -> None:
    result = runner.invoke(
        app,
        ["--claim", "some claim", "--search", "--paper-id", "2301.00001"],
    )
    assert result.exit_code == 1


def test_cli_search_missing_claim() -> None:
    result = runner.invoke(app, ["--search"])
    assert result.exit_code == 1


def test_cli_search_error_propagates() -> None:
    with patch("veritas.cli.search_and_verify", side_effect=SearchError("API failed")):
        result = runner.invoke(
            app, ["--claim", "some claim", "--search"]
        )
    assert result.exit_code == 1


def test_cli_paper_id_mode_still_works_without_search() -> None:
    with patch("veritas.cli.verify", return_value=_MOCK_RESULT):
        result = runner.invoke(
            app,
            ["--claim", "Aspirin reduces cardiovascular risk", "--paper-id", "DOI:10.1000/test"],
        )
    assert result.exit_code == 0
