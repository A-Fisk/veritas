from veritas.retrieval import fetch_abstracts_sync, search_papers_sync
from veritas.state import VerificationResult
from veritas.verifier import distill_keywords, run_verification


def verify(
    claim: str,
    paper_ids: list[str],
    model: str | None = None,
    api_key: str | None = None,
    s2_api_key: str | None = None,
) -> VerificationResult:
    """Verify a claim against Semantic Scholar papers.

    Fetches abstracts for each paper_id, then calls Claude to assess
    whether the claim is supported by the evidence.

    Args:
        claim: The claim to verify (1–2000 chars).
        paper_ids: Semantic Scholar IDs, DOIs, or arXiv IDs (1–10).
        model: Claude model ID override (default: VERITAS_MODEL env var).
        api_key: Anthropic API key override (default: ANTHROPIC_API_KEY env var).
        s2_api_key: Semantic Scholar API key (default: VERITAS_S2_API_KEY env var).

    Returns:
        VerificationResult with verdict, confidence, reasoning, and per-paper results.
    """
    papers = fetch_abstracts_sync(paper_ids, api_key=s2_api_key)
    return run_verification(claim, papers, model=model, api_key=api_key)


def search_and_verify(
    claim: str,
    top_k: int = 5,
    model: str | None = None,
    api_key: str | None = None,
    s2_api_key: str | None = None,
) -> tuple[VerificationResult, list[dict[str, str | None]], str]:
    """Distil keywords from claim, search Semantic Scholar, then verify.

    Args:
        claim: The claim to verify (1–2000 chars).
        top_k: Number of papers to retrieve from search (default 5).
        model: Claude model ID override (default: VERITAS_MODEL env var).
        api_key: Anthropic API key override (default: ANTHROPIC_API_KEY env var).
        s2_api_key: Semantic Scholar API key (default: VERITAS_S2_API_KEY env var).

    Returns:
        Tuple of (VerificationResult, papers_list, keywords_used).
    """
    keywords = distill_keywords(claim, model=model, api_key=api_key)
    papers = search_papers_sync(keywords, limit=top_k, api_key=s2_api_key)
    result = run_verification(claim, papers, model=model, api_key=api_key)
    return result, papers, keywords


__all__ = ["verify", "search_and_verify", "VerificationResult"]
