from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
import yaml

from ese.config import ConfigValidationError, load_config
from ese.dashboard import serve_dashboard
from ese.doctor import evaluate_doctor, run_doctor
from ese.init_wizard import ROLE_DESCRIPTIONS, run_wizard
from ese.pipeline import CONFIG_SNAPSHOT_NAME, PipelineError, run_pipeline
from ese.pr_review import DEFAULT_MAX_DIFF_CHARS, PullRequestReviewError, run_pr_review
from ese.reports import RunReportError, collect_run_report, render_report_text, render_status_text
from ese.templates import (
    AUTO_EXECUTION_MODE,
    build_task_config,
    list_task_templates,
    run_task_pipeline,
)


app = typer.Typer(help="Ensemble Software Engineering (ESE) CLI")


def _launch_dashboard(
    *,
    artifacts_dir: str = "artifacts",
    host: str = "127.0.0.1",
    port: int = 8765,
    config: str | None = None,
    open_browser: bool = True,
) -> None:
    url = f"http://{host}:{port}"
    typer.echo(f"Serving ESE dashboard at {url} (Ctrl-C to stop)")
    serve_dashboard(
        artifacts_dir=artifacts_dir,
        host=host,
        port=port,
        open_browser=open_browser,
        config_path=config,
    )


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Launch the dashboard when no subcommand is provided."""
    if ctx.invoked_subcommand is None:
        _launch_dashboard()


def _print_doctor_guidance(violations: list[str]) -> None:
    if any("No project scope supplied" in item for item in violations):
        typer.echo("Hint: pass `--scope`, run `ese task \"...\"`, or regenerate config with `ese init`.")
    if any("share model" in item for item in violations):
        typer.echo("Hint: use `ese init --advanced` or edit per-role model overrides under roles.")


def _load_effective_cfg(config: str, scope: str | None) -> dict[str, Any]:
    cfg = load_config(path=config)
    effective_cfg: dict[str, Any] = dict(cfg or {})
    if scope and scope.strip():
        input_cfg = dict(effective_cfg.get("input") or {})
        input_cfg["scope"] = scope.strip()
        effective_cfg["input"] = input_cfg
    return effective_cfg


@app.command()
def init(
    config: str = typer.Option("ese.config.yaml", help="Path to write the generated config"),
    simple: bool = typer.Option(
        True,
        "--simple/--advanced",
        help="Use simple setup (default) or advanced role selection with optional per-role model overrides.",
    ),
):
    """Create an ESE configuration via an interactive wizard."""
    written = run_wizard(config_path=config, advanced=not simple)
    if not written:
        typer.echo("⚠️ Setup canceled. Config was not written.")
        raise typer.Exit(code=1)
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
        _print_doctor_guidance(violations)
        raise typer.Exit(code=2)

    # Solo mode returns violations as messages to display.
    if violations:
        typer.echo("⚠️ Solo mode enabled:")
        for v in violations:
            typer.echo(f"  - {v}")
    else:
        typer.echo("✅ Doctor checks passed")


def _start_pipeline(config: str, artifacts_dir: str | None, scope: str | None) -> None:
    try:
        effective_cfg = _load_effective_cfg(config=config, scope=scope)
    except ConfigValidationError as err:
        typer.echo(f"❌ ESE start failed: {err}")
        raise typer.Exit(code=2) from err

    ok, violations, _ = evaluate_doctor(effective_cfg)
    if not ok:
        typer.echo("❌ ESE doctor failed. Violations:")
        for v in violations:
            typer.echo(f"  - {v}")
        _print_doctor_guidance(violations)
        raise typer.Exit(code=2)

    try:
        summary_path = run_pipeline(cfg=effective_cfg, artifacts_dir=artifacts_dir)
    except PipelineError as err:
        typer.echo(f"❌ ESE start failed: {err}")
        raise typer.Exit(code=2) from err

    typer.echo(f"✅ Pipeline completed. Summary: {summary_path}")


@app.command("start")
def start(
    config: str = typer.Option("ese.config.yaml", help="Path to ESE config"),
    artifacts_dir: str | None = typer.Option(
        None,
        help="Directory for pipeline artifacts (overrides output.artifacts_dir in config)",
    ),
    scope: str | None = typer.Option(
        None,
        help="Project scope/task override for this run (overrides input.scope in config)",
    ),
):
    """Start the full ESE pipeline."""
    _start_pipeline(config=config, artifacts_dir=artifacts_dir, scope=scope)


@app.command("run", hidden=True)
def run_alias(
    config: str = typer.Option("ese.config.yaml", help="Path to ESE config"),
    artifacts_dir: str | None = typer.Option(
        None,
        help="Directory for pipeline artifacts (overrides output.artifacts_dir in config)",
    ),
    scope: str | None = typer.Option(
        None,
        help="Project scope/task override for this run (overrides input.scope in config)",
    ),
):
    """Backward-compatible alias for `ese start`."""
    _start_pipeline(config=config, artifacts_dir=artifacts_dir, scope=scope)


@app.command("templates")
def templates(json_output: bool = typer.Option(False, "--json", help="Emit templates as JSON")):
    """List opinionated task templates for quick task-first runs."""
    payload = [
        {
            "key": template.key,
            "title": template.title,
            "summary": template.summary,
            "roles": list(template.roles),
        }
        for template in list_task_templates()
    ]
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo("Available task templates:")
    for item in payload:
        typer.echo(f"  - {item['key']}: {item['title']}")
        typer.echo(f"    {item['summary']}")
        typer.echo(f"    roles: {', '.join(item['roles'])}")


@app.command("task")
def task(
    scope: str = typer.Argument(..., help="Project scope or task to run"),
    template: str = typer.Option("feature-delivery", help="Opinionated task template"),
    provider: str = typer.Option("openai", help="Provider preset"),
    execution_mode: str = typer.Option(AUTO_EXECUTION_MODE, help="auto, demo, or live"),
    artifacts_dir: str = typer.Option("artifacts", help="Directory for run artifacts"),
    model: str | None = typer.Option(None, help="Optional provider model override"),
    runtime_adapter: str | None = typer.Option(None, help="Optional module:function adapter for advanced live runs"),
    provider_name: str | None = typer.Option(None, help="Custom provider name when using custom_api"),
    base_url: str | None = typer.Option(None, help="Base URL for custom_api live runs"),
    api_key_env: str | None = typer.Option(None, help="API key environment variable override"),
    write_config_path: str | None = typer.Option(None, "--write-config", help="Optional path to save the generated config"),
    show_config: bool = typer.Option(False, help="Print the generated config before running"),
):
    """Run ESE from a task description without hand-authoring config first."""
    try:
        cfg = build_task_config(
            scope=scope,
            template_key=template,
            provider=provider,
            execution_mode=execution_mode,
            artifacts_dir=artifacts_dir,
            model=model,
            api_key_env=api_key_env,
            runtime_adapter=runtime_adapter,
            provider_name=provider_name,
            base_url=base_url,
        )
        if show_config:
            typer.echo(yaml.safe_dump(cfg, sort_keys=False).strip())
        if write_config_path:
            Path(write_config_path).parent.mkdir(parents=True, exist_ok=True)
        _, summary_path = run_task_pipeline(
            scope=scope,
            template_key=template,
            provider=provider,
            execution_mode=execution_mode,
            artifacts_dir=artifacts_dir,
            model=model,
            api_key_env=api_key_env,
            runtime_adapter=runtime_adapter,
            provider_name=provider_name,
            base_url=base_url,
            config_path=write_config_path,
        )
    except (ConfigValidationError, PipelineError) as err:
        typer.echo(f"❌ ESE task failed: {err}")
        raise typer.Exit(code=2) from err

    adapter_name = str((cfg.get("runtime") or {}).get("adapter") or "dry-run")
    typer.echo(f"✅ Task run completed using template '{template}' via {adapter_name}. Summary: {summary_path}")


@app.command("pr")
def pr(
    repo_path: str = typer.Option(".", help="Path to the Git repository to review"),
    pr: str | None = typer.Option(None, help="GitHub PR number or URL (requires gh)"),
    base: str | None = typer.Option(None, help="Base ref. Defaults from PR metadata or origin/main"),
    head: str | None = typer.Option(None, help="Head ref. Defaults from PR metadata or HEAD"),
    title: str | None = typer.Option(None, help="Optional review title override"),
    focus: str | None = typer.Option(None, help="Optional reviewer focus guidance"),
    provider: str = typer.Option("openai", help="Provider preset"),
    execution_mode: str = typer.Option(AUTO_EXECUTION_MODE, help="auto, demo, or live"),
    artifacts_dir: str = typer.Option("artifacts", help="Directory for review artifacts"),
    model: str | None = typer.Option(None, help="Optional provider model override"),
    runtime_adapter: str | None = typer.Option(None, help="Optional module:function adapter for advanced live runs"),
    provider_name: str | None = typer.Option(None, help="Custom provider name when using custom_api"),
    base_url: str | None = typer.Option(None, help="Base URL for custom_api live runs"),
    api_key_env: str | None = typer.Option(None, help="API key environment variable override"),
    max_diff_chars: int = typer.Option(DEFAULT_MAX_DIFF_CHARS, help="Maximum unified diff characters to embed in review context"),
    write_config_path: str | None = typer.Option(None, "--write-config", help="Optional path to save the generated config"),
):
    """Review a pull request or branch diff and export a GitHub-ready markdown summary."""
    try:
        context, cfg, summary_path, review_path = run_pr_review(
            repo_path=repo_path,
            pr=pr,
            base=base,
            head=head,
            title=title,
            focus=focus,
            provider=provider,
            execution_mode=execution_mode,
            artifacts_dir=artifacts_dir,
            model=model,
            api_key_env=api_key_env,
            runtime_adapter=runtime_adapter,
            provider_name=provider_name,
            base_url=base_url,
            max_diff_chars=max_diff_chars,
            config_path=write_config_path,
        )
    except (ConfigValidationError, PipelineError, PullRequestReviewError) as err:
        typer.echo(f"❌ ESE PR review failed: {err}")
        raise typer.Exit(code=2) from err

    adapter_name = str((cfg.get("runtime") or {}).get("adapter") or "dry-run")
    typer.echo(
        "✅ PR review completed "
        f"for {context.head_ref} against {context.base_ref} via {adapter_name}. "
        f"Summary: {summary_path} Review: {review_path}",
    )


@app.command("status")
def status(
    artifacts_dir: str = typer.Option("artifacts", help="Directory containing pipeline_state.json"),
):
    """Print a concise run status summary for an artifacts directory."""
    try:
        report = collect_run_report(artifacts_dir)
    except RunReportError as err:
        typer.echo(f"❌ ESE status failed: {err}")
        raise typer.Exit(code=2) from err

    typer.echo(render_status_text(report))
    snapshot = report.get("config_snapshot")
    if snapshot:
        typer.echo(f"Config snapshot: {snapshot}")


@app.command("report")
def report(
    artifacts_dir: str = typer.Option("artifacts", help="Directory containing run artifacts"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable report JSON"),
):
    """Aggregate role outputs into a human-readable run report."""
    try:
        run_report = collect_run_report(artifacts_dir)
    except RunReportError as err:
        typer.echo(f"❌ ESE report failed: {err}")
        raise typer.Exit(code=2) from err

    if json_output:
        typer.echo(json.dumps(run_report, indent=2))
        return
    typer.echo(render_report_text(run_report))


@app.command("rerun")
def rerun(
    from_role: str = typer.Argument(..., help="Role to rerun from"),
    artifacts_dir: str = typer.Option("artifacts", help="Directory containing the prior run"),
    config: str | None = typer.Option(None, help="Config path. Defaults to the saved config snapshot in artifacts."),
    scope: str | None = typer.Option(None, help="Optional scope override for the rerun"),
):
    """Rerun the pipeline from a specific role using existing prior artifacts as upstream context."""
    config_path = config or str(Path(artifacts_dir) / CONFIG_SNAPSHOT_NAME)
    try:
        effective_cfg = _load_effective_cfg(config=config_path, scope=scope)
        summary_path = run_pipeline(
            cfg=effective_cfg,
            artifacts_dir=artifacts_dir,
            start_role=from_role,
        )
    except (ConfigValidationError, PipelineError) as err:
        typer.echo(f"❌ ESE rerun failed: {err}")
        raise typer.Exit(code=2) from err

    typer.echo(f"✅ Reran pipeline from role '{from_role}'. Summary: {summary_path}")


@app.command("dashboard")
def dashboard(
    artifacts_dir: str = typer.Option("artifacts", help="Artifacts directory to inspect by default"),
    host: str = typer.Option("127.0.0.1", help="Host for the local dashboard server"),
    port: int = typer.Option(8765, help="Port for the local dashboard server"),
    config: str | None = typer.Option(None, help="Optional config path to prefill the dashboard"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the dashboard in a browser"),
):
    """Launch the local ESE dashboard for task-first runs and run review."""
    _launch_dashboard(
        artifacts_dir=artifacts_dir,
        host=host,
        port=port,
        config=config,
        open_browser=open_browser,
    )


if __name__ == "__main__":
    app()
