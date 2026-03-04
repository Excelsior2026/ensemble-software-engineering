from __future__ import annotations

import typer

from ese.config import ConfigValidationError, load_config
from ese.doctor import run_doctor
from ese.init_wizard import ROLE_DESCRIPTIONS, run_wizard
from ese.pipeline import PipelineError, run_pipeline


app = typer.Typer(help="Ensemble Software Engineering (ESE) CLI")


@app.command()
def init(config: str = typer.Option("ese.config.yaml", help="Path to write the generated config")):
    """Create an ESE configuration via an interactive wizard."""
    written = run_wizard(config_path=config)
    typer.echo(f"✅ Wrote {written}")


@app.command("roles")
def list_roles():
    """List selectable ESE roles and their responsibilities."""
    typer.echo("Selectable ESE roles:")
    for role, description in ROLE_DESCRIPTIONS.items():
        typer.echo(f"  - {role}: {description}")


@app.command()
def doctor(config: str = typer.Option("ese.config.yaml", help="Path to ESE config")):
    """Validate configuration and enforce ensemble constraints."""
    ok, violations, role_models = run_doctor(config_path=config)

    typer.echo("Role model assignments:")
    for role, model in role_models.items():
        typer.echo(f"  - {role}: {model}")

    # Ensemble failures should show the violations and exit.
    if not ok:
        typer.echo("❌ ESE doctor failed. Violations:")
        for v in violations:
            typer.echo(f"  - {v}")
        raise typer.Exit(code=2)

    # Solo mode returns violations as messages to display.
    if violations:
        typer.echo("⚠️ Solo mode enabled:")
        for v in violations:
            typer.echo(f"  - {v}")
    else:
        typer.echo("✅ Doctor checks passed")


@app.command()
def run(
    config: str = typer.Option("ese.config.yaml", help="Path to ESE config"),
    artifacts_dir: str = typer.Option("artifacts", help="Directory for pipeline artifacts"),
):
    """Run the full ESE pipeline."""
    ok, violations, _ = run_doctor(config_path=config)
    if not ok:
        typer.echo("❌ ESE doctor failed. Violations:")
        for v in violations:
            typer.echo(f"  - {v}")
        raise typer.Exit(code=2)

    try:
        cfg = load_config(path=config)
        summary_path = run_pipeline(cfg=cfg or {}, artifacts_dir=artifacts_dir)
    except (ConfigValidationError, PipelineError) as err:
        typer.echo(f"❌ ESE run failed: {err}")
        raise typer.Exit(code=2) from err

    typer.echo(f"✅ Pipeline completed. Summary: {summary_path}")


if __name__ == "__main__":
    app()
