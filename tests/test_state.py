from veritas.state import PaperResult, VerificationResult


def test_verification_result_structure() -> None:
    paper = PaperResult(
        paper_id="2301.00001",
        title="Test Paper",
        verdict="supported",
        note="Directly supports the claim.",
    )
    result = VerificationResult(
        claim="Test claim",
        verdict="verified",
        confidence=0.9,
        reasoning="Clear support from the paper.",
        papers=[paper],
    )
    assert result["claim"] == "Test claim"
    assert result["verdict"] == "verified"
    assert result["confidence"] == 0.9
    assert len(result["papers"]) == 1
    assert result["papers"][0]["paper_id"] == "2301.00001"


def test_paper_result_no_title() -> None:
    paper = PaperResult(
        paper_id="2301.99999",
        title=None,
        verdict="insufficient_evidence",
        note="No abstract available.",
    )
    assert paper["title"] is None
    assert paper["verdict"] == "insufficient_evidence"
