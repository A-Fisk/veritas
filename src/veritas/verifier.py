import json
import os

import anthropic
from anthropic.types import TextBlock

from veritas.state import PaperResult, VerificationResult

DEFAULT_MODEL = os.environ.get("VERITAS_MODEL", "claude-sonnet-4-6")

_SYSTEM_PROMPT = """\
You are a scientific claim verifier. Given a claim and a set of paper abstracts,
assess whether the claim is supported by the evidence.

Respond ONLY with valid JSON matching this exact schema:
{
  "verdict": "verified" | "uncertain" | "not_supported",
  "confidence": <float 0.0–1.0>,
  "reasoning": "<1–3 sentence explanation>",
  "papers": [
    {
      "paper_id": "<id>",
      "title": "<title or null>",
      "verdict": "supported" | "partially_supported" | "not_supported" | "insufficient_evidence",
      "note": "<brief note>"
    }
  ]
}

Verdict definitions:
- verified: claim is directly and consistently supported
- uncertain: partial support, mixed or qualified evidence
- not_supported: papers do not support or contradict the claim
"""


def _build_user_prompt(claim: str, papers: list[dict[str, str | None]]) -> str:
    lines = [f"Claim: {claim}", "", "Papers:"]
    for i, paper in enumerate(papers, 1):
        lines.append(f"\n{i}. Paper ID: {paper['paper_id']}")
        lines.append(f"   Title: {paper.get('title') or '(unknown)'}")
        abstract = paper.get("abstract")
        lines.append(f"   Abstract: {abstract or '(no abstract available)'}")
    return "\n".join(lines)


def _parse_response(
    raw: str,
    claim: str,
    papers: list[dict[str, str | None]],
) -> VerificationResult:
    data = json.loads(raw)
    return VerificationResult(
        claim=claim,
        verdict=data["verdict"],
        confidence=float(data["confidence"]),
        reasoning=data["reasoning"],
        papers=[
            PaperResult(
                paper_id=p["paper_id"],
                title=p.get("title"),
                verdict=p["verdict"],
                note=p["note"],
            )
            for p in data["papers"]
        ],
    )


def run_verification(
    claim: str,
    papers: list[dict[str, str | None]],
    model: str | None = None,
    api_key: str | None = None,
) -> VerificationResult:
    """Call Claude to verify a claim against fetched paper abstracts."""
    client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
    model = model or DEFAULT_MODEL
    user_prompt = _build_user_prompt(claim, papers)

    for attempt in range(2):
        message = client.messages.create(
            model=model,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        first = message.content[0] if message.content else None
        raw = first.text if isinstance(first, TextBlock) else ""
        try:
            return _parse_response(raw, claim, papers)
        except (json.JSONDecodeError, KeyError, TypeError):
            if attempt == 1:
                # Second failure — return fallback result
                return VerificationResult(
                    claim=claim,
                    verdict="uncertain",
                    confidence=0.0,
                    reasoning="Failed to parse LLM response after 2 attempts.",
                    papers=[
                        PaperResult(
                            paper_id=str(p["paper_id"] or ""),
                            title=p.get("title"),
                            verdict="insufficient_evidence",
                            note="Parse error",
                        )
                        for p in papers
                    ],
                )

    # Unreachable, but satisfies type checker
    raise RuntimeError("Unexpected exit from verification loop")


_KEYWORD_SYSTEM = """\
You are a search query expert. Extract 2-4 concise scientific keywords from a claim.
Respond with ONLY a space-separated list of keywords — no punctuation, no explanation.
"""


def distill_keywords(
    claim: str,
    model: str | None = None,
    api_key: str | None = None,
) -> str:
    """Use Claude to distil a claim into concise search keywords."""
    client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
    model = model or DEFAULT_MODEL

    message = client.messages.create(
        model=model,
        max_tokens=50,
        system=_KEYWORD_SYSTEM,
        messages=[{"role": "user", "content": f"Claim: {claim}"}],
    )
    first = message.content[0] if message.content else None
    raw = first.text.strip() if isinstance(first, TextBlock) else ""
    return raw or claim
