# Veritas

Veritas verifies scientific claims against literature. Give it a claim and one or more paper IDs; it fetches abstracts from Semantic Scholar, asks Claude to assess the evidence, and returns a structured verdict.

## Installation

Requires Python 3.11+.

```bash
uv sync
```

Set your Anthropic API key:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

## Basic usage

```bash
veritas verify \
  --claim "Aspirin reduces risk of cardiovascular events in primary prevention" \
  --paper-id "DOI:10.1056/NEJMoa1805819" \
  --paper-id "DOI:10.1056/NEJMoa1804988"
```

Paper IDs can be Semantic Scholar corpus IDs (numeric), DOIs (`DOI:10.xxxx/...`), or arXiv IDs (`ARXIV:2301.00001`).

For programmatic use, pipe JSON to `--json-stdin` to avoid shell escaping issues with long claims:

```bash
echo '{"claim": "...", "paper_ids": ["DOI:10.1056/NEJMoa1805819"]}' \
  | veritas verify --json-stdin
```

## Example

**Claim:** "Aspirin reduces risk of cardiovascular events in primary prevention"

**Papers:** Two large RCTs (ASCEND, ARRIVE)

```bash
veritas verify \
  --claim "Aspirin reduces risk of cardiovascular events in primary prevention" \
  --paper-id "DOI:10.1056/NEJMoa1805819" \
  --paper-id "DOI:10.1056/NEJMoa1804988"
```

**Output:**

```json
{
  "claim": "Aspirin reduces risk of cardiovascular events in primary prevention",
  "verdict": "not_supported",
  "confidence": 0.85,
  "reasoning": "Both papers are large RCTs examining aspirin for primary prevention. Both found no significant reduction in major cardiovascular events and noted increased bleeding risk, contradicting the claim.",
  "papers": [
    {
      "paper_id": "DOI:10.1056/NEJMoa1805819",
      "title": "Effects of Aspirin for Primary Prevention in Persons with Diabetes",
      "verdict": "not_supported",
      "note": "No significant benefit in cardiovascular events; increased risk of major bleeding"
    },
    {
      "paper_id": "DOI:10.1056/NEJMoa1804988",
      "title": "Aspirin to Reduce the Risk of Initial Vascular Events (ARRIVE)",
      "verdict": "not_supported",
      "note": "Primary endpoint not met; GI bleeding increased"
    }
  ]
}
```

## Interpreting verdicts

| Verdict | Meaning |
|---|---|
| `verified` | Claim is directly and consistently supported by the provided papers |
| `uncertain` | Papers partially support the claim, or evidence is mixed or qualified |
| `not_supported` | Papers do not support the claim, or directly contradict it |

The `confidence` field (0.0–1.0) reflects how certain the model is in its verdict — not a statistical probability, but a relative signal:

| Range | Interpretation |
|---|---|
| 0.8–1.0 | High confidence |
| 0.5–0.8 | Moderate confidence |
| 0.0–0.5 | Low confidence; treat verdict with caution |

Exit code 0 means the verification ran successfully (including `not_supported` — that's a valid result). Exit code 1 means a hard error occurred (network failure, unknown paper ID, etc.); the error is written as JSON to stderr.

## How it works

1. **Abstract retrieval** — Veritas fetches the title and abstract for each paper ID from the [Semantic Scholar Graph API](https://api.semanticscholar.org/). No API key is needed for low-volume use.
2. **LLM assessment** — The claim and all abstracts are sent to Claude in a single prompt. Claude returns a per-paper verdict and an overall verdict with confidence and reasoning.
3. **Structured output** — The response is validated and printed as JSON to stdout.

## Python library usage

Veritas can also be imported directly:

```python
from veritas import verify

result = verify(
    claim="Aspirin reduces risk of cardiovascular events in primary prevention",
    paper_ids=["DOI:10.1056/NEJMoa1805819", "DOI:10.1056/NEJMoa1804988"],
)
print(result["verdict"])     # "not_supported"
print(result["confidence"])  # 0.85
print(result["reasoning"])   # "Both papers are large RCTs..."
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | required | Anthropic API key |
| `VERITAS_MODEL` | `claude-sonnet-4-6` | Claude model to use |
| `VERITAS_S2_API_KEY` | optional | Semantic Scholar API key (higher rate limits) |
| `VERITAS_TIMEOUT` | `30` | Per-request timeout in seconds |
