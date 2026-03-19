from typing import Literal, TypedDict


class PaperResult(TypedDict):
    paper_id: str
    title: str | None
    verdict: Literal["supported", "partially_supported", "not_supported", "insufficient_evidence"]
    note: str


class VerificationResult(TypedDict):
    claim: str
    verdict: Literal["verified", "uncertain", "not_supported"]
    confidence: float
    reasoning: str
    papers: list[PaperResult]
