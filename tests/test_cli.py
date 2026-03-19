"""CLI integration tests (no network calls)."""

import json
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from veritas.cli import app
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
