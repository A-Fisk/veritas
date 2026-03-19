"""veritas CLI — verify scientific claims against paper abstracts."""

from __future__ import annotations

import json
import sys

import rich
import typer

from veritas import search_and_verify, verify
from veritas.retrieval import AbstractFetchError, PaperNotFoundError, SearchError, fetch_abstracts_sync
from veritas.verifier import run_verification

app = typer.Typer(
    name="veritas",
    help="Verify scientific claims against Semantic Scholar papers.",
    add_completion=False,
)


@app.command()
def main(
    claim: str = typer.Option(
        "",
        "--claim",
        help="The claim to verify (required unless --json-stdin).",
    ),
    paper_ids: list[str] = typer.Option(
        [],
        "--paper-id",
        help="Semantic Scholar ID, DOI, or arXiv ID. Repeat for multiple papers.",
    ),
    json_stdin: bool = typer.Option(
        False,
        "--json-stdin",
        help='Read {"claim": "...", "paper_ids": [...]} from stdin.',
    ),
    search: bool = typer.Option(
        False,
        "--search",
        help="Auto-fetch relevant papers from Semantic Scholar instead of supplying IDs.",
    ),
    top_k: int = typer.Option(
        5,
        "--top-k",
        help="Number of papers to retrieve in --search mode (default 5).",
        min=1,
        max=20,
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Include per-paper abstract excerpts and individual verdicts in output.",
    ),
) -> None:
    """Verify a claim against one or more papers and print a JSON verdict."""
    if json_stdin:
        try:
            data = json.load(sys.stdin)
            claim = data["claim"]
            paper_ids = data["paper_ids"]
        except (json.JSONDecodeError, KeyError) as e:
            _fatal("invalid_input", f"Failed to parse JSON from stdin: {e}")

    if not claim:
        _fatal("invalid_input", "--claim is required (or use --json-stdin)")
    if len(claim) > 2000:
        _fatal("invalid_input", f"Claim too long ({len(claim)} chars; max 2000)")

    if search:
        if paper_ids:
            _fatal("invalid_input", "--search and --paper-id are mutually exclusive")
        _run_search_mode(claim, top_k, verbose)
    else:
        if not paper_ids:
            _fatal("invalid_input", "At least one --paper-id is required (or use --search)")
        if len(paper_ids) > 10:
            _fatal("invalid_input", f"Too many paper IDs ({len(paper_ids)}; max 10)")
        _run_paper_id_mode(claim, list(paper_ids), verbose)


def _run_search_mode(claim: str, top_k: int, verbose: bool) -> None:
    try:
        result, papers, keywords = search_and_verify(claim=claim, top_k=top_k)
    except SearchError as e:
        _fatal("search_error", str(e))
    except Exception as e:
        _fatal("internal_error", str(e))

    print(
        f"Search keywords: {keywords!r}  |  Papers found: {len(papers)}",
        file=sys.stderr,
    )
    output: dict = dict(result)
    if verbose:
        output = _enrich_with_abstracts(output, papers)
    rich.print_json(json.dumps(output))


def _run_paper_id_mode(claim: str, paper_ids: list[str], verbose: bool) -> None:
    try:
        if verbose:
            papers = fetch_abstracts_sync(paper_ids)
            result = run_verification(claim=claim, papers=papers)
            output: dict = _enrich_with_abstracts(dict(result), papers)
        else:
            result = verify(claim=claim, paper_ids=paper_ids)
            output = dict(result)
    except PaperNotFoundError as e:
        _fatal("paper_not_found", str(e), paper_id=e.paper_id)
    except AbstractFetchError as e:
        _fatal("fetch_error", str(e), paper_id=e.paper_id)
    except Exception as e:
        _fatal("internal_error", str(e))

    rich.print_json(json.dumps(output))


def _enrich_with_abstracts(
    output: dict,
    papers: list[dict[str, str | None]],
) -> dict:
    """Add abstract excerpts to each paper entry in the output."""
    abstract_by_id = {p["paper_id"]: p.get("abstract") for p in papers}
    output["papers"] = [
        {**paper, "abstract": abstract_by_id.get(paper["paper_id"])}
        for paper in output.get("papers", [])
    ]
    return output


def _fatal(error: str, message: str, **extra: str) -> None:
    payload: dict[str, str] = {"error": error, "message": message, **extra}
    print(json.dumps(payload), file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    app()
