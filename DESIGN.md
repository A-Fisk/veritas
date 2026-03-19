# Veritas: Claim Verification Tool — Design Document

## Overview

Veritas is a standalone Python CLI that takes a claim and one or more paper
references, fetches abstracts from Semantic Scholar, and returns a structured
verdict on whether the claim is supported by the papers.

It is designed to be called by darwin (or a human) as a verification signal
producer. The core value is the verification result; architecture is kept simple.

---

## 1. Input Contract

### Primary Interface: CLI flags

```bash
veritas verify \
  --claim "mRNA vaccines reduce COVID-19 hospitalization by 90%" \
  --paper-id 2301.00001 \
  --paper-id 2301.00002
```

Paper IDs are Semantic Scholar corpus IDs (numeric) or DOIs/arXiv IDs that
Semantic Scholar resolves.

### Alternative: JSON stdin (for programmatic use)

```bash
echo '{"claim": "...", "paper_ids": ["2301.00001", "2301.00002"]}' \
  | veritas verify --json-stdin
```

Darwin should use `--json-stdin` to avoid shell escaping issues with long claims.

### Input schema (JSON)

```json
{
  "claim": "string — the claim to verify",
  "paper_ids": ["string", "..."]
}
```

**Constraints:**
- `claim`: required, 1–2000 characters
- `paper_ids`: required, 1–10 paper IDs; veritas fetches the abstract for each

---

## 2. Output Contract

Veritas always writes JSON to stdout (regardless of invocation method).
Errors go to stderr with a non-zero exit code.

### Output schema

```json
{
  "claim": "mRNA vaccines reduce COVID-19 hospitalization by 90%",
  "verdict": "uncertain",
  "confidence": 0.62,
  "reasoning": "Two of the three papers report 88–91% efficacy against hospitalization in the pre-Omicron period; the third reports 65% during Omicron. The claim is broadly supported but overstated for the full pandemic period.",
  "papers": [
    {
      "paper_id": "2301.00001",
      "title": "Vaccine efficacy against hospitalization...",
      "verdict": "supported",
      "note": "Reports 91% efficacy in pre-Omicron cohort"
    },
    {
      "paper_id": "2301.00002",
      "title": "mRNA vaccine performance during Omicron...",
      "verdict": "partially_supported",
      "note": "Reports 65% efficacy during Omicron wave"
    }
  ]
}
```

### Verdict values

| Value | Meaning |
|---|---|
| `verified` | Claim is directly and consistently supported by the provided papers |
| `uncertain` | Papers partially support the claim, or evidence is mixed/qualified |
| `not_supported` | Papers do not support the claim, or directly contradict it |

### Confidence score

A float in `[0.0, 1.0]` reflecting the LLM's assessed certainty in the verdict.
Not a statistical probability — treat as a relative signal.

| Range | Interpretation |
|---|---|
| 0.8–1.0 | High confidence |
| 0.5–0.8 | Moderate confidence |
| 0.0–0.5 | Low confidence; treat verdict with caution |

### Error output (stderr, non-zero exit)

```json
{
  "error": "paper_not_found",
  "paper_id": "9999.99999",
  "message": "Semantic Scholar returned 404 for paper ID 9999.99999"
}
```

---

## 3. Internal Pipeline

```
Input (claim + paper_ids)
       │
       ▼
[Abstract Retrieval]
  Semantic Scholar API
  GET /paper/{paper_id}?fields=title,abstract
  → title + abstract per paper
       │
       ▼
[Verification Agent]
  LLM prompt: given claim + abstracts, assess support
  → per-paper verdict + overall verdict + confidence + reasoning
       │
       ▼
Output (JSON)
```

### 3.1 Abstract Retrieval

Use the Semantic Scholar Graph API (no API key required for low-volume use):

```
GET https://api.semanticscholar.org/graph/v1/paper/{paper_id}?fields=title,abstract
```

- Supports S2 corpus IDs, DOIs (`DOI:10.xxxx`), arXiv IDs (`ARXIV:2301.00001`)
- Fetch papers in parallel (asyncio or threadpool) to reduce latency
- If a paper has no abstract: include it with `abstract: null`, note in per-paper output

### 3.2 Verification Agent

Single LLM call (Claude claude-sonnet-4-6) with a structured prompt:

```
You are a scientific claim verifier.

Claim: <claim>

Papers:
1. Title: <title>
   Abstract: <abstract>
2. ...

Task: Assess whether the claim is supported by these papers.
For each paper, give: verdict (supported|partially_supported|not_supported|insufficient_evidence), brief note.
Then give: overall verdict (verified|uncertain|not_supported), confidence (0.0–1.0), reasoning (1–3 sentences).

Respond as JSON only.
```

Response parsed directly into output schema. If parse fails, retry once; on
second failure, return verdict `uncertain` with confidence 0.0 and error note.

### 3.3 Claim Decomposition (deferred)

For claims that are compound (multiple sub-assertions), decomposition into
sub-claims and aggregation is useful but adds complexity. **This is out of
scope for v1.** A single claim per call is the v1 contract.

---

## 4. Calling Convention from Darwin

### Recommended: Library import

Darwin and veritas are both Python. Import veritas as a library:

```python
from veritas import verify

result = verify(
    claim="mRNA vaccines reduce hospitalization by 90%",
    paper_ids=["2301.00001", "2301.00002"],
)
# result is a VerificationResult dataclass / TypedDict
```

**Why library over subprocess:**
- No serialization overhead for large abstracts
- Shared process — no cold-start cost
- Easier error propagation (exceptions vs exit codes)
- Simpler testing

The CLI (`veritas verify`) is a thin wrapper around the same `verify()` function.

### Subprocess fallback

If darwin is not Python, or isolation is needed:

```python
import subprocess, json

result = subprocess.run(
    ["veritas", "verify", "--json-stdin"],
    input=json.dumps({"claim": claim, "paper_ids": paper_ids}),
    capture_output=True, text=True
)
verdict = json.loads(result.stdout)
```

Exit code 0 = success (even for `not_supported` — that's a valid result, not an error).
Exit code 1 = hard error (network failure, bad input, LLM parse failure).

---

## 5. Configuration

Via environment variables (no config file for v1):

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | required | LLM API key |
| `VERITAS_MODEL` | `claude-sonnet-4-6` | LLM model ID |
| `VERITAS_S2_API_KEY` | optional | Semantic Scholar API key (for higher rate limits) |
| `VERITAS_TIMEOUT` | `30` | Per-request timeout in seconds |

---

## 6. Example End-to-End

### Input

```bash
veritas verify \
  --claim "Aspirin reduces risk of cardiovascular events in primary prevention" \
  --paper-id "DOI:10.1056/NEJMoa1805819" \
  --paper-id "DOI:10.1056/NEJMoa1804988"
```

### Output

```json
{
  "claim": "Aspirin reduces risk of cardiovascular events in primary prevention",
  "verdict": "not_supported",
  "confidence": 0.85,
  "reasoning": "Both papers are large RCTs (ASCEND and ARRIVE) examining aspirin for primary prevention. Both found no significant reduction in major cardiovascular events and noted increased bleeding risk, contradicting the claim.",
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

---

## 7. What's Out of Scope (v1)

- Full-text retrieval (abstracts only)
- Batch claims (one claim per invocation)
- Caching of Semantic Scholar responses
- Streaming output
- Web UI
- Claim decomposition into sub-claims
- Fine-tuned verification model

These can be added incrementally once the core signal is validated.
