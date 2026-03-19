# Veritas Paragraph Decomposition: Architecture Design

## Executive Summary

This document specifies the architecture for extending Veritas to handle full paragraphs containing multiple claims, rather than just single claims. The extension will decompose paragraphs into discrete logical statements, track dependencies between claims, and provide both per-claim and paragraph-level verification results while maintaining backward compatibility with the existing single-claim workflow.

## 1. Problem Statement

Currently, Veritas handles only single claims like "Aspirin reduces cardiovascular risk by 30%." However, scientific texts often contain paragraphs with multiple interrelated claims:

> "Drug A significantly reduces disease X progression by 50% in randomized controlled trials. This therapeutic effect is particularly pronounced in patients under 65 years old. However, the treatment is associated with increased risk of gastrointestinal side effects, including nausea in 15% of patients and headaches in 8% of patients."

This paragraph contains at least 4 distinct, verifiable claims with logical dependencies. Manual decomposition is tedious and error-prone.

## 2. Current Architecture Analysis

### 2.1 Existing Components
- **CLI Layer** (`cli.py`): Handles `--claim` and `--paper-id` inputs, JSON stdin
- **Library Layer** (`__init__.py`): `verify()` and `search_and_verify()` functions
- **Verification Engine** (`verifier.py`): Claude-based claim assessment with structured prompts
- **Retrieval Service** (`retrieval.py`): Semantic Scholar API integration
- **Data Models** (`state.py`): `VerificationResult` and `PaperResult` TypedDicts

### 2.2 Current Data Flow
```
Input (claim + paper_ids) → Abstract Retrieval → Claude Verification → JSON Output
```

### 2.3 Existing Capabilities to Leverage
- Abstract fetching and caching from Semantic Scholar
- Structured LLM prompting with retry logic
- JSON schema validation and error handling
- CLI argument parsing and output formatting
- Library interface for programmatic access

## 3. Proposed Architecture

### 3.1 New Components

#### 3.1.1 Claim Decomposer (`src/veritas/decomposer.py`)
Responsible for breaking paragraphs into discrete claims and analyzing dependencies.

```python
@dataclass
class ExtractedClaim:
    id: str                    # "claim_1", "claim_2", etc.
    text: str                  # The isolated claim text
    context: str               # Relevant context from paragraph
    confidence: float          # Extraction confidence (0.0-1.0)
    dependencies: List[str]    # IDs of claims this depends on
    claim_type: ClaimType      # PRIMARY, SUPPORTING, QUALIFYING
    position: int              # Order in original paragraph

class ClaimDecomposer:
    def decompose(self, paragraph: str) -> DecompositionResult
    def analyze_dependencies(self, claims: List[ExtractedClaim]) -> DependencyGraph
```

#### 3.1.2 Paragraph Verifier (`src/veritas/paragraph_verifier.py`)
Orchestrates verification of multiple claims and aggregates results.

```python
class ParagraphVerifier:
    def verify_paragraph(
        self,
        claims: List[ExtractedClaim],
        papers: List[Paper]
    ) -> ParagraphVerificationResult

    def aggregate_verdicts(self, results: List[VerificationResult]) -> OverallVerdict
```

#### 3.1.3 Extended State Models (`src/veritas/state.py`)
New data structures for paragraph verification results.

```python
class ParagraphVerificationResult(TypedDict):
    paragraph: str
    overall_verdict: Literal["verified", "mixed", "not_supported"]
    overall_confidence: float
    summary_reasoning: str
    extracted_claims: List[ClaimVerificationResult]
    decomposition_metadata: DecompositionMetadata

class ClaimVerificationResult(TypedDict):
    claim_id: str
    text: str
    verdict: Literal["verified", "uncertain", "not_supported"]
    confidence: float
    reasoning: str
    dependencies: List[str]
    papers: List[PaperResult]

class DecompositionMetadata(TypedDict):
    claims_extracted: int
    avg_extraction_confidence: float
    dependency_graph: List[List[str]]
    processing_time_ms: int
```

### 3.2 Integration Points

#### 3.2.1 CLI Extensions (`src/veritas/cli.py`)
```bash
# New paragraph mode
veritas verify-paragraph --paragraph "Multi-claim text..." --paper-id DOI:...

# Alternative JSON input
echo '{"type": "paragraph", "paragraph": "...", "paper_ids": [...]}' | veritas verify-paragraph --json-stdin

# Debugging mode
veritas extract-claims --paragraph "..." --output-format json
```

#### 3.2.2 Library Interface Extensions (`src/veritas/__init__.py`)
```python
def verify_paragraph(
    paragraph: str,
    paper_ids: List[str],
    max_claims: int = 10,
    min_confidence: float = 0.7,
    model: str = None,
    api_key: str = None,
    s2_api_key: str = None
) -> ParagraphVerificationResult

def extract_claims_only(
    paragraph: str,
    max_claims: int = 10,
    model: str = None,
    api_key: str = None
) -> DecompositionResult
```

## 4. Claim Decomposition Strategy

### 4.1 LLM Prompting Approach
Use Claude for claim extraction with carefully crafted system prompts:

```
System: You are a scientific claim decomposer. Break paragraphs into discrete, independently verifiable factual claims.

Guidelines:
1. Each claim should be a complete, standalone assertion
2. Preserve quantitative details (percentages, timeframes, sample sizes)
3. Identify logical relationships between claims
4. Exclude opinions, background context, and methodology descriptions
5. Focus on empirical findings and causal relationships

For each extracted claim, provide:
- claim_id: Sequential identifier
- text: The isolated claim statement
- dependencies: IDs of other claims this logically depends on
- claim_type: PRIMARY (main finding), SUPPORTING (evidence), or QUALIFYING (limitation/condition)
- confidence: Your confidence in the claim boundary (0.0-1.0)

Respond with JSON only.
```

### 4.2 Dependency Analysis
Identify relationships between claims:

- **Logical dependencies**: "This effect is stronger in X" depends on "Drug has effect Y"
- **Temporal dependencies**: "After 6 months" depends on earlier timeframe claims
- **Conditional dependencies**: "However, in patients with X" qualifies previous claims
- **Quantitative dependencies**: Subgroup analyses depend on overall effect claims

### 4.3 Claim Classification
- **PRIMARY**: Core research findings, main therapeutic effects
- **SUPPORTING**: Evidence supporting primary claims, statistical details
- **QUALIFYING**: Limitations, conditions, adverse events, subgroup effects

## 5. Verification Workflow

### 5.1 Sequential Processing
```
1. Decompose paragraph → List[ExtractedClaim]
2. Fetch abstracts for all paper_ids (parallel)
3. Verify each claim against all papers (parallel)
4. Aggregate results based on dependencies and claim types
5. Generate paragraph-level verdict and summary
```

### 5.2 Parallel Claim Verification
All claims verified simultaneously for efficiency. Dependencies used for:
- Result interpretation (dependent claim failures affect confidence)
- Error propagation (if dependency fails, mark dependents as uncertain)
- Summary generation (primary claim failures are weighted higher)

### 5.3 Aggregation Rules
**Overall verdict determination:**
- **verified**: All primary claims verified + ≥80% supporting claims verified
- **mixed**: Some primary claims verified, some not, OR primary claims uncertain
- **not_supported**: Majority of primary claims not supported or contradicted

**Confidence calculation:**
- Weight primary claims 2x, supporting claims 1x, qualifying claims 0.5x
- Account for extraction confidence in final score
- Penalize for dependency chain failures

## 6. Error Handling & Edge Cases

### 6.1 Malformed Input
- **Single sentence**: Auto-route to single-claim verification
- **No extractable claims**: Return error with suggestion to use `verify` instead
- **Too many claims** (>max_claims): Truncate with warning or return error

### 6.2 Extraction Issues
- **Low confidence extraction**: Include warnings in output, suggest manual review
- **Circular dependencies**: Detect cycles, break arbitrarily, mark affected claims
- **Ambiguous boundaries**: Use conservative extraction, note uncertainty in metadata

### 6.3 Verification Conflicts
- **Contradictory claims within paragraph**: Flag in output, treat as uncertain
- **Dependency verification failure**: Propagate uncertainty to dependent claims
- **Insufficient evidence for subclaims**: Mark as uncertain rather than not_supported

## 7. Output Format Specification

### 7.1 Standard JSON Output
```json
{
  "paragraph": "Original paragraph text...",
  "overall_verdict": "mixed",
  "overall_confidence": 0.73,
  "summary_reasoning": "Primary efficacy claim verified by 2 RCTs. Subgroup analysis uncertain due to limited evidence. Safety profile partially supported.",
  "extracted_claims": [
    {
      "claim_id": "claim_1",
      "text": "Drug A reduces disease X progression by 50%",
      "verdict": "verified",
      "confidence": 0.89,
      "reasoning": "Directly supported by 2 large RCTs with consistent effect sizes",
      "dependencies": [],
      "claim_type": "PRIMARY",
      "papers": [
        {
          "paper_id": "DOI:10.1000/example1",
          "title": "Phase III trial of Drug A",
          "verdict": "supported",
          "note": "Reports 48% reduction in primary endpoint"
        }
      ]
    },
    {
      "claim_id": "claim_2",
      "text": "Effect is stronger in patients under 65 years",
      "verdict": "uncertain",
      "confidence": 0.54,
      "reasoning": "Limited subgroup data, only one study reports age stratification",
      "dependencies": ["claim_1"],
      "claim_type": "QUALIFYING",
      "papers": [...]
    }
  ],
  "decomposition_metadata": {
    "claims_extracted": 4,
    "avg_extraction_confidence": 0.82,
    "dependency_graph": [["claim_2", "claim_1"], ["claim_3", "claim_1"]],
    "processing_time_ms": 2340
  }
}
```

### 7.2 Alternative Output Formats
```bash
--format summary      # Overall verdict only
--format claims       # Per-claim breakdown table
--format dependencies # Dependency graph visualization
--format debug        # Include extraction prompts and intermediate results
```

## 8. Implementation Plan

### Phase 1: Core Decomposition (MVP)
**Goals**: Basic paragraph → claims extraction and verification
**Timeline**: 2-3 weeks
**Deliverables**:
- `ClaimDecomposer` with LLM-based extraction
- Basic dependency detection for obvious cases
- `ParagraphVerifier` with simple aggregation
- CLI command `verify-paragraph` with JSON output
- Unit tests for decomposition accuracy

**Acceptance Criteria**:
- Handle paragraphs with 2-5 claims
- Extract claims with >80% accuracy on test cases
- Generate reasonable paragraph-level verdicts
- Maintain existing single-claim functionality

### Phase 2: Enhanced Analysis
**Goals**: Sophisticated dependency analysis and error handling
**Timeline**: 1-2 weeks
**Deliverables**:
- Advanced dependency detection (temporal, conditional, quantitative)
- Claim type classification (PRIMARY/SUPPORTING/QUALIFYING)
- Robust error handling for edge cases
- Improved aggregation logic with weighted scoring

**Acceptance Criteria**:
- Handle complex paragraphs with 6-10 claims
- Correctly identify 90% of claim dependencies
- Graceful handling of malformed inputs
- Confidence calibration matches human expert judgment

### Phase 3: User Experience Enhancement
**Goals**: Interactive features and output customization
**Timeline**: 1-2 weeks
**Deliverables**:
- Interactive claim review mode (`--interactive`)
- Multiple output formats (`--format` options)
- Claim extraction debugging tools
- Performance optimizations

**Acceptance Criteria**:
- Interactive mode allows claim editing before verification
- Multiple output formats meet different user needs
- Processing time <10s for typical paragraphs
- Memory usage <500MB for large inputs

### Phase 4: Advanced Features (Future)
**Goals**: Production-ready features for scale
**Deliverables**:
- Batch paragraph processing
- Custom decomposition strategies
- Caching of extraction results
- API endpoint for web integration

## 9. Testing Strategy

### 9.1 Unit Tests
- **Claim extraction**: Test on diverse scientific paragraph types
- **Dependency detection**: Verify logical relationship identification
- **Aggregation logic**: Test verdict calculation with various claim combinations
- **Error handling**: Malformed inputs, API failures, parsing errors

### 9.2 Integration Tests
- **End-to-end workflows**: Full paragraph verification with real papers
- **CLI interface**: All command combinations and input formats
- **Library interface**: Programmatic usage scenarios
- **Performance**: Response time and memory usage under load

### 9.3 Acceptance Tests
- **Real scientific abstracts**: Curated set of paragraphs with known ground truth
- **Cross-validation**: Compare with human expert decomposition
- **Robustness**: Various writing styles, claim types, complexity levels
- **Edge cases**: Single sentences, very long paragraphs, ambiguous text

### 9.4 Test Data Curation
Create test suite with:
- 50+ scientific paragraphs from various domains
- Manual ground truth for claim boundaries and dependencies
- Expected verification results for common paper sets
- Edge cases and adversarial examples

## 10. Backward Compatibility

### 10.1 Preserved Interfaces
- Existing `verify()` and `search_and_verify()` functions unchanged
- CLI `veritas verify --claim` works exactly as before
- All existing configuration variables honored
- Output schema for single claims identical

### 10.2 Shared Infrastructure
- Reuse abstract retrieval logic (`retrieval.py`)
- Reuse core verification prompts (`verifier.py`)
- Extend but don't break existing state models (`state.py`)
- Share authentication and configuration systems

### 10.3 Migration Path
- New features are purely additive
- Users can adopt paragraph mode incrementally
- No changes required to existing integrations
- Clear documentation distinguishes single vs paragraph modes

## 11. Configuration & Deployment

### 11.1 New Environment Variables
```bash
export VERITAS_MAX_CLAIMS=10           # Max claims per paragraph
export VERITAS_MIN_EXTRACTION_CONF=0.7 # Min confidence threshold
export VERITAS_PARAGRAPH_TIMEOUT=60    # Timeout for paragraph processing
```

### 11.2 Dependencies
- No new external dependencies required
- Reuse existing anthropic, httpx, typer, rich packages
- Consider optional dependencies for visualization (graphviz)

### 11.3 Performance Considerations
- Parallel claim verification minimizes latency
- LLM call overhead: +1 call for extraction, same N calls for verification
- Memory usage: Linear in number of claims extracted
- Typical processing time: 5-15 seconds for 3-8 claims

## 12. Success Metrics

### 12.1 Functional Metrics
- **Extraction accuracy**: >85% claim boundary agreement with experts
- **Dependency detection**: >80% precision/recall on logical relationships
- **Verdict accuracy**: >90% agreement with expert paragraph assessment
- **Error rate**: <5% unhandled exceptions on real-world inputs

### 12.2 Performance Metrics
- **Latency**: <10s end-to-end for typical paragraphs
- **Throughput**: Handle 100+ paragraphs/hour in batch mode
- **Memory**: <500MB peak usage for 10-claim paragraphs
- **API costs**: <2x single-claim cost per paragraph

### 12.3 Adoption Metrics
- **Usage growth**: 50%+ of users try paragraph mode within 3 months
- **User satisfaction**: >4.0/5.0 rating for paragraph verification accuracy
- **Error reduction**: 70% reduction in user-reported claim decomposition issues

---

## Conclusion

This architecture extends Veritas with paragraph decomposition capability while preserving the simplicity and reliability of the existing single-claim workflow. The design emphasizes modularity, robust error handling, and user experience, enabling scientists to efficiently verify complex multi-claim paragraphs against literature evidence.

The phased implementation approach allows for iterative refinement based on real user feedback while delivering immediate value with the MVP. The comprehensive testing strategy ensures reliability and accuracy comparable to manual expert decomposition.