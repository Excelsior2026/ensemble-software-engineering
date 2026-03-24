from __future__ import annotations

from pathlib import Path

import typer

from apps.contract_intelligence.orchestration.bid_review_runner import run_bid_review


app = typer.Typer(help="Contract intelligence pilot CLI")


@app.callback()
def main() -> None:
    """Run contract-intelligence pilot workflows."""


@app.command("bid-review")
def bid_review(
    project_dir: str = typer.Argument(..., help="Path to the project document folder"),
    artifacts_dir: str | None = typer.Option(
        None,
        "--artifacts-dir",
        help="Optional output directory for generated bid-review artifacts",
    ),
) -> None:
    """Run the deterministic construction bid-review pilot over a project folder."""
    result = run_bid_review(project_dir=project_dir, artifacts_dir=artifacts_dir)
    typer.echo(f"Project: {result.project_id}")
    typer.echo(f"Artifacts: {result.artifacts_dir}")
    typer.echo(f"Recommendation: {result.decision_summary.recommendation.value}")
    typer.echo(f"Overall risk: {result.decision_summary.overall_risk.value}")
    typer.echo(f"Human review required: {result.decision_summary.human_review_required}")
    typer.echo("Artifacts written:")
    for filename in sorted(result.artifact_paths):
        relative = Path(result.artifact_paths[filename]).resolve()
        typer.echo(f"  - {filename}: {relative}")


if __name__ == "__main__":
    app()
