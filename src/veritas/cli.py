"""veritas CLI — verify scientific claims against paper abstracts."""

from __future__ import annotations

import json
import sys

import rich
import typer

from veritas import verify
from veritas.retrieval import AbstractFetchError, PaperNotFoundError

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
    if not paper_ids:
        _fatal("invalid_input", "At least one --paper-id is required (or use --json-stdin)")
    if len(claim) > 2000:
        _fatal("invalid_input", f"Claim too long ({len(claim)} chars; max 2000)")
    if len(paper_ids) > 10:
        _fatal("invalid_input", f"Too many paper IDs ({len(paper_ids)}; max 10)")

    try:
        result = verify(claim=claim, paper_ids=list(paper_ids))
    except PaperNotFoundError as e:
        _fatal("paper_not_found", str(e), paper_id=e.paper_id)
    except AbstractFetchError as e:
        _fatal("fetch_error", str(e), paper_id=e.paper_id)
    except Exception as e:
        _fatal("internal_error", str(e))

    rich.print_json(json.dumps(result))


def _fatal(error: str, message: str, **extra: str) -> None:
    payload: dict[str, str] = {"error": error, "message": message, **extra}
    print(json.dumps(payload), file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    app()
