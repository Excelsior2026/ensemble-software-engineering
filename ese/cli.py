from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

import typer
import yaml

from ese.artifact_views import discover_artifact_views
from ese.config import ConfigValidationError, load_config, write_config
from ese.config_packs import discover_config_packs
from ese.dashboard import serve_dashboard
from ese.doctor import (
    build_doctor_guidance,
    evaluate_doctor,
    evaluate_doctor_environment,
    render_doctor_environment_text,
    run_doctor,
)
from ese.evidence_state import update_pipeline_evidence_state
from ese.extensions import list_extension_surfaces
from ese.feedback import record_feedback as persist_feedback
from ese.init_wizard import ROLE_DESCRIPTIONS, run_wizard
from ese.integrations import discover_integrations, publish_run_evidence
from ese.pack_sdk import (
    PackProjectError,
    describe_pack_project,
    scaffold_pack_project,
    smoke_test_pack_project,
)
from ese.pipeline import CONFIG_SNAPSHOT_NAME, PipelineError, run_pipeline
from ese.policy_checks import discover_policy_checks
from ese.pr_review import (
    DEFAULT_MAX_DIFF_CHARS,
    PullRequestReviewError,
    build_pr_review_config,
    render_pull_request_review_markdown,
)
from ese.report_exporters import (
    discover_external_report_exporters,
    list_builtin_report_exporters,
    render_report_export,
)
from ese.reports import (
    RunReportError,
    collect_run_report,
    render_code_suggestions_json,
    render_code_suggestions_markdown,
    render_report_text,
    render_status_text,
)
from ese.starter_sdk import (
    StarterProjectError,
    describe_starter_project,
    scaffold_starter_project,
    smoke_test_starter_project,
)
from ese.templates import (
    AUTO_EXECUTION_MODE,
    build_task_config,
    list_task_templates,
    provider_runtime_summary,
    recommend_template_for_scope,
)

app = typer.Typer(help="Ensemble Software Engineering (ESE) CLI")
pack_app = typer.Typer(help="Scaffold and validate external ESE config packs")
starter_app = typer.Typer(help="Scaffold and validate external ESE starter bundles")
app.add_typer(pack_app, name="pack")
app.add_typer(starter_app, name="starter")


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
    """Launch the dashboard interactively, otherwise print CLI help."""
    if ctx.invoked_subcommand is None:
        if sys.stdin.isatty() and sys.stdout.isatty():
            _launch_dashboard()
            return
        typer.echo(ctx.get_help())


def _print_doctor_guidance(cfg: dict[str, Any], violations: list[str]) -> None:
    for item in build_doctor_guidance(cfg, violations):
        typer.echo(f"Hint: {item}")


def _print_doctor_failure(cfg: dict[str, Any], violations: list[str]) -> None:
    typer.echo("❌ ESE doctor failed. Violations:")
    for item in violations:
        typer.echo(f"  - {item}")
    _print_doctor_guidance(cfg, violations)


def _enforce_doctor_or_exit(cfg: dict[str, Any]) -> None:
    ok, violations, _ = evaluate_doctor(cfg)
    if not ok:
        _print_doctor_failure(cfg, violations)
        raise typer.Exit(code=2)
    if violations:
        typer.echo("⚠️ Doctor notes:")
        for item in violations:
            typer.echo(f"  - {item}")


def _print_preflight(kind: str, cfg: dict[str, Any], *, quiet: bool = False) -> None:
    if quiet:
        return
    roles = ", ".join((cfg.get("roles") or {}).keys())
    provider_cfg = cfg.get("provider") or {}
    runtime_cfg = cfg.get("runtime") or {}
    output_cfg = cfg.get("output") or {}
    input_cfg = cfg.get("input") or {}
    provider_runtime = cfg.get("provider_runtime") or provider_runtime_summary(
        str(provider_cfg.get("name") or ""),
        execution_mode=str(cfg.get("execution_mode") or AUTO_EXECUTION_MODE),
        runtime_adapter=str(runtime_cfg.get("adapter") or ""),
    )
    lines = [
        "Preflight:",
        f"  - run type: {kind}",
        f"  - template: {cfg.get('template_key', 'custom')}",
        f"  - provider: {provider_cfg.get('name', 'unknown')}",
        f"  - model: {provider_cfg.get('model', 'unknown')}",
        f"  - adapter: {runtime_cfg.get('adapter', 'dry-run')}",
        f"  - roles: {roles or 'none'}",
        f"  - artifacts dir: {output_cfg.get('artifacts_dir', 'artifacts')}",
        f"  - runtime note: {provider_runtime.get('note', 'n/a')}",
    ]
    scope = str(input_cfg.get("scope") or "").strip()
    if scope:
        lines.append(f"  - scope: {scope}")
    repo_path = str(input_cfg.get("repo_path") or "").strip()
    if repo_path:
        lines.append(f"  - repo context: {repo_path}")
    typer.echo("\n".join(lines))


def _print_run_follow_up(artifacts_dir: str, *, quiet: bool = False) -> None:
    if quiet:
        return
    try:
        report = collect_run_report(artifacts_dir)
    except RunReportError:
        return

    if report.get("blockers"):
        typer.echo(f"Blockers: {report['blocker_count']}")
    consensus = report.get("consensus") or {}
    if consensus.get("agreements"):
        top = consensus["agreements"][0]
        typer.echo(
            "Top consensus: "
            f"{top['title']} across {', '.join(top['roles'])}",
        )
    comparison = report.get("comparison") or {}
    if comparison.get("previous_artifacts_dir"):
        typer.echo(
            "Run delta: "
            f"+{len(comparison.get('new_blockers', []))} new blockers, "
            f"-{len(comparison.get('resolved_blockers', []))} resolved blockers",
        )
    suggestions = report.get("code_suggestions") or []
    if suggestions:
        top = suggestions[0]
        target = str(top.get("path") or "").strip()
        prefix = f"Code suggestion [{target}]" if target else "Code suggestion"
        typer.echo(f"{prefix}: {top['suggestion']}")
    for action in report.get("suggested_actions", [])[:2]:
        typer.echo(f"Next: {action['text']} [{action['command']}]")


def _load_effective_cfg(config: str, scope: str | None) -> dict[str, Any]:
    cfg = load_config(path=config)
    effective_cfg: dict[str, Any] = dict(cfg or {})
    if scope and scope.strip():
        input_cfg = dict(effective_cfg.get("input") or {})
        input_cfg["scope"] = scope.strip()
        effective_cfg["input"] = input_cfg
    return effective_cfg


def _guidance_cfg(config: str) -> dict[str, Any]:
    try:
        return load_config(path=config)
    except ConfigValidationError:
        return {}


def _effective_artifacts_dir(cfg: dict[str, Any], fallback: str | None = None) -> str:
    configured = str((cfg.get("output") or {}).get("artifacts_dir") or "").strip()
    if configured:
        return configured
    return str(fallback or "artifacts")


def _run_with_policy(
    *,
    kind: str,
    cfg: dict[str, Any],
    artifacts_dir: str,
    quiet: bool,
    failure_label: str,
    execute: Callable[[], str],
    success_message: Callable[[str], str],
) -> str:
    _print_preflight(kind, cfg, quiet=quiet)
    _enforce_doctor_or_exit(cfg)

    try:
        summary_path = execute()
    except (PipelineError, RunReportError) as err:
        typer.echo(f"❌ {failure_label}: {err}")
        raise typer.Exit(code=2) from err

    if quiet:
        typer.echo(summary_path)
    else:
        typer.echo(success_message(summary_path))
        _print_run_follow_up(artifacts_dir, quiet=quiet)
    return summary_path


def _filtered_code_suggestions(
    report: dict[str, Any],
    *,
    role: str | None = None,
    path_filter: str | None = None,
) -> list[dict[str, Any]]:
    suggestions = [
        item
        for item in report.get("code_suggestions", [])
        if isinstance(item, dict)
    ]
    clean_role = (role or "").strip()
    clean_path = (path_filter or "").strip()
    if clean_role:
        suggestions = [
            item
            for item in suggestions
            if str(item.get("role") or "").strip() == clean_role
        ]
    if clean_path:
        suggestions = [
            item
            for item in suggestions
            if clean_path in str(item.get("path") or "").strip()
        ]
    return suggestions


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
    """List starter framework role examples and their responsibilities."""
    typer.echo("Built-in starter role examples for framework installs:")
    for role, description in ROLE_DESCRIPTIONS.items():
        typer.echo(f"  - {role}: {description}")


@app.command("packs")
def list_packs():
    """List installed config packs discovered outside the ESE core package."""
    packs, failures = discover_config_packs()
    if not packs and not failures:
        typer.echo("No config packs installed.")
        return

    if packs:
        typer.echo("Installed config packs:")
        for pack in packs:
            typer.echo(f"  - {pack.key}: {pack.title} - {pack.summary}")
    if failures:
        typer.echo("Broken config packs:")
        for failure in failures:
            typer.echo(f"  - {failure.entry_point}: {failure.error}")


@app.command("policies")
def list_policies():
    """List installed external doctor policy checks."""
    checks, failures = discover_policy_checks()
    if not checks and not failures:
        typer.echo("No external policy checks installed.")
        return

    if checks:
        typer.echo("Installed external policy checks:")
        for check in checks:
            typer.echo(f"  - {check.key}: {check.title} - {check.summary}")
    if failures:
        typer.echo("Broken external policy checks:")
        for failure in failures:
            typer.echo(f"  - {failure.entry_point}: {failure.error}")


@app.command("exporters")
def list_exporters():
    """List built-in and external report exporters."""
    typer.echo("Built-in report exporters:")
    for exporter in list_builtin_report_exporters():
        typer.echo(f"  - {exporter.key}: {exporter.title} - {exporter.summary}")

    exporters, failures = discover_external_report_exporters()
    if exporters:
        typer.echo("Installed external report exporters:")
        for exporter in exporters:
            typer.echo(f"  - {exporter.key}: {exporter.title} - {exporter.summary}")
    if failures:
        typer.echo("Broken external report exporters:")
        for failure in failures:
            typer.echo(f"  - {failure.entry_point}: {failure.error}")


@app.command("views")
def list_views():
    """List installed external artifact views."""
    views, failures = discover_artifact_views()
    if not views and not failures:
        typer.echo("No external artifact views installed.")
        return

    if views:
        typer.echo("Installed external artifact views:")
        for view in views:
            typer.echo(f"  - {view.key}: {view.title} - {view.summary}")
    if failures:
        typer.echo("Broken external artifact views:")
        for failure in failures:
            typer.echo(f"  - {failure.entry_point}: {failure.error}")


@app.command("integrations")
def list_installed_integrations():
    """List installed external evidence-publishing integrations."""
    integrations, failures = discover_integrations()
    if not integrations and not failures:
        typer.echo("No external integrations installed.")
        return

    if integrations:
        typer.echo("Installed external integrations:")
        for integration in integrations:
            typer.echo(f"  - {integration.key}: {integration.title} - {integration.summary}")
    if failures:
        typer.echo("Broken external integrations:")
        for failure in failures:
            typer.echo(f"  - {failure.entry_point}: {failure.error}")


@app.command("extensions")
def list_extensions(json_output: bool = typer.Option(False, "--json", help="Emit extension surface metadata as JSON")):
    """List the supported ESE extension surfaces and contract versions."""
    surfaces = [
        {
            "key": surface.key,
            "title": surface.title,
            "entry_point_group": surface.entry_point_group,
            "contract_version": surface.contract_version,
        }
        for surface in list_extension_surfaces()
    ]
    if json_output:
        typer.echo(json.dumps(surfaces, indent=2))
        return

    typer.echo("Supported ESE extension surfaces:")
    for surface in surfaces:
        typer.echo(
            "  - "
            f"{surface['key']}: contract v{surface['contract_version']} "
            f"via {surface['entry_point_group']}"
        )


@pack_app.command("init")
def pack_init(
    path: str = typer.Argument(..., help="Target directory for the external pack project"),
    key: str = typer.Option("", help="Stable pack key. Defaults from the directory name."),
    title: str | None = typer.Option(None, help="Human-friendly pack title"),
    summary: str | None = typer.Option(None, help="One-line description for the pack"),
    package_name: str | None = typer.Option(None, "--package", help="Python package name for the generated scaffold"),
    preset: str = typer.Option("balanced", help="Framework preset used by the pack"),
    goal_profile: str | None = typer.Option(None, help="Goal profile override. Defaults from the preset."),
    force: bool = typer.Option(False, "--force", help="Allow writing into a non-empty target directory"),
):
    """Scaffold a portable external ESE pack project."""
    target = Path(path).expanduser()
    try:
        project = scaffold_pack_project(
            target,
            pack_key=(key or "").strip() or target.name,
            title=title,
            summary=summary,
            package_name=package_name,
            preset=preset,
            goal_profile=goal_profile,
            force=force,
        )
    except PackProjectError as err:
        typer.echo(f"❌ ESE pack init failed: {err}")
        raise typer.Exit(code=2) from err

    typer.echo(
        "✅ Scaffolded external pack "
        f"'{project.pack.key}' at {target.resolve()} using contract v{project.contract_version}."
    )


@pack_app.command("validate")
def pack_validate(
    path: str = typer.Argument(".", help="Pack project directory or ese_pack.yaml manifest"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable validation metadata"),
):
    """Validate an external ESE pack manifest and prompt assets."""
    try:
        report = describe_pack_project(path)
    except PackProjectError as err:
        typer.echo(f"❌ ESE pack validate failed: {err}")
        raise typer.Exit(code=2) from err

    if json_output:
        typer.echo(json.dumps(report, indent=2))
        return

    typer.echo(
        "✅ Pack valid: "
        f"{report['pack_key']} ({report['role_count']} roles, contract v{report['contract_version']})"
    )
    typer.echo(f"Manifest: {report['manifest_path']}")


@pack_app.command("test")
def pack_test(
    path: str = typer.Argument(".", help="Pack project directory or ese_pack.yaml manifest"),
    provider: str = typer.Option("openai", help="Provider preset for the generated smoke-test config"),
    model: str | None = typer.Option(None, help="Optional model override for the generated smoke-test config"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable smoke-test metadata"),
):
    """Generate and validate a smoke-test ESE config from an external pack."""
    try:
        report = smoke_test_pack_project(path, provider=provider, model=model)
    except PackProjectError as err:
        typer.echo(f"❌ ESE pack test failed: {err}")
        raise typer.Exit(code=2) from err

    if json_output:
        typer.echo(json.dumps(report, indent=2))
        return

    typer.echo(
        "✅ Pack smoke test passed: "
        f"{report['pack_key']} via {report['provider']} / {report['model']}"
    )
    typer.echo(f"Manifest: {report['manifest_path']}")


@starter_app.command("init")
def starter_init(
    path: str = typer.Argument(..., help="Target directory for the external starter bundle"),
    key: str = typer.Option("", help="Stable starter key. Defaults from the directory name."),
    title: str | None = typer.Option(None, help="Human-friendly starter title"),
    summary: str | None = typer.Option(None, help="One-line description for the starter"),
    package_name: str | None = typer.Option(None, "--package", help="Python package name for the generated scaffold"),
    preset: str = typer.Option("strict", help="Framework preset used by the starter pack"),
    goal_profile: str | None = typer.Option(None, help="Goal profile override. Defaults to high-quality."),
    force: bool = typer.Option(False, "--force", help="Allow writing into a non-empty target directory"),
):
    """Scaffold a portable external ESE starter bundle."""
    target = Path(path).expanduser()
    try:
        project = scaffold_starter_project(
            target,
            starter_key=(key or "").strip() or target.name,
            title=title,
            summary=summary,
            package_name=package_name,
            preset=preset,
            goal_profile=goal_profile,
            force=force,
        )
    except StarterProjectError as err:
        typer.echo(f"❌ ESE starter init failed: {err}")
        raise typer.Exit(code=2) from err

    typer.echo(
        "✅ Scaffolded external starter bundle "
        f"'{project.key}' at {target.resolve()} using contract v{project.contract_version}."
    )


@starter_app.command("validate")
def starter_validate(
    path: str = typer.Argument(".", help="Starter project directory or ese_starter.yaml manifest"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable validation metadata"),
):
    """Validate an external ESE starter bundle manifest and referenced extension files."""
    try:
        report = describe_starter_project(path)
    except StarterProjectError as err:
        typer.echo(f"❌ ESE starter validate failed: {err}")
        raise typer.Exit(code=2) from err

    if json_output:
        typer.echo(json.dumps(report, indent=2))
        return

    typer.echo(
        "✅ Starter valid: "
        f"{report['starter_key']} (contract v{report['contract_version']})"
    )
    typer.echo(f"Manifest: {report['manifest_path']}")


@starter_app.command("test")
def starter_test(
    path: str = typer.Argument(".", help="Starter project directory or ese_starter.yaml manifest"),
    provider: str = typer.Option("openai", help="Provider preset for the starter pack smoke test"),
    model: str | None = typer.Option(None, help="Optional model override for the starter pack smoke test"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable smoke-test metadata"),
):
    """Load a starter bundle and smoke-test its pack plus extension loaders."""
    try:
        report = smoke_test_starter_project(path, provider=provider, model=model)
    except StarterProjectError as err:
        typer.echo(f"❌ ESE starter test failed: {err}")
        raise typer.Exit(code=2) from err

    if json_output:
        typer.echo(json.dumps(report, indent=2))
        return

    typer.echo(
        "✅ Starter smoke test passed: "
        f"{report['starter_key']} via {report['provider']} / {report['model']}"
    )
    typer.echo(f"Manifest: {report['manifest_path']}")


@app.command()
def doctor(
    config: str = typer.Option("ese.config.yaml", help="Path to ESE config"),
    environment: bool = typer.Option(False, "--environment", help="Validate installed extension environment instead of a config file"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable doctor output"),
):
    """Validate configuration and enforce ensemble constraints."""
    if environment:
        ok, violations, report = evaluate_doctor_environment()
        if json_output:
            payload = dict(report)
            payload["ok"] = ok
            payload["violations"] = violations
            typer.echo(json.dumps(payload, indent=2))
        else:
            typer.echo(render_doctor_environment_text(report))
        if not ok:
            raise typer.Exit(code=2)
        return

    ok, violations, role_models = run_doctor(config_path=config)
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "ok": ok,
                    "violations": violations,
                    "role_models": role_models,
                },
                indent=2,
            )
        )
        if not ok:
            raise typer.Exit(code=2)
        return

    typer.echo("Role model assignments:")
    for role, model in role_models.items():
        typer.echo(f"  - {role}: {model}")

    # Ensemble failures should show the violations and exit.
    if not ok:
        _print_doctor_failure(_guidance_cfg(config), violations)
        raise typer.Exit(code=2)

    # Solo mode returns violations as messages to display.
    if violations:
        typer.echo("⚠️ Solo mode enabled:")
        for v in violations:
            typer.echo(f"  - {v}")
    else:
        typer.echo("✅ Doctor checks passed")


def _start_pipeline(config: str, artifacts_dir: str | None, scope: str | None, *, quiet: bool = False) -> None:
    try:
        effective_cfg = _load_effective_cfg(config=config, scope=scope)
    except ConfigValidationError as err:
        typer.echo(f"❌ ESE start failed: {err}")
        raise typer.Exit(code=2) from err

    effective_artifacts_dir = artifacts_dir or str((effective_cfg.get("output") or {}).get("artifacts_dir") or "artifacts")
    _run_with_policy(
        kind="start",
        cfg=effective_cfg,
        artifacts_dir=effective_artifacts_dir,
        quiet=quiet,
        failure_label="ESE start failed",
        execute=lambda: run_pipeline(cfg=effective_cfg, artifacts_dir=artifacts_dir),
        success_message=lambda summary_path: f"✅ Pipeline completed. Summary: {summary_path}",
    )


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
    quiet: bool = typer.Option(False, "--quiet", help="Suppress preflight and follow-up chatter"),
):
    """Start the full ESE pipeline."""
    _start_pipeline(config=config, artifacts_dir=artifacts_dir, scope=scope, quiet=quiet)


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
    quiet: bool = typer.Option(False, "--quiet", help="Suppress preflight and follow-up chatter"),
):
    """Backward-compatible alias for `ese start`."""
    _start_pipeline(config=config, artifacts_dir=artifacts_dir, scope=scope, quiet=quiet)


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
    template: str = typer.Option("", help="Opinionated task template (defaults from scope if omitted)"),
    pack: str = typer.Option("", "--pack", help="Installed config pack key for pack-driven task runs"),
    provider: str = typer.Option("openai", help="Provider preset"),
    execution_mode: str = typer.Option(AUTO_EXECUTION_MODE, help="auto, demo, or live"),
    artifacts_dir: str = typer.Option("artifacts", help="Directory for run artifacts"),
    model: str | None = typer.Option(None, help="Optional provider model override"),
    runtime_adapter: str | None = typer.Option(None, help="Optional module:function adapter for advanced live runs"),
    provider_name: str | None = typer.Option(None, help="Custom provider name when using custom_api"),
    base_url: str | None = typer.Option(None, help="Base URL for custom_api live runs"),
    api_key_env: str | None = typer.Option(None, help="API key environment variable override"),
    repo_path: str | None = typer.Option(None, help="Optional Git repo path to inject working-tree context into the task"),
    include_repo_status: bool = typer.Option(True, help="Include git status in task repo context"),
    include_repo_diff: bool = typer.Option(True, help="Include working diff in task repo context"),
    max_repo_diff_chars: int = typer.Option(8000, help="Maximum diff characters to inject for task repo context"),
    write_config_path: str | None = typer.Option(None, "--write-config", help="Optional path to save the generated config"),
    show_config: bool = typer.Option(False, help="Print the generated config before running"),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress preflight and follow-up chatter"),
):
    """Run ESE from a task description without hand-authoring config first."""
    chosen_template = (template or "").strip()
    chosen_pack = (pack or "").strip()
    try:
        cfg = build_task_config(
            scope=scope,
            template_key=chosen_template or None,
            pack_key=chosen_pack or None,
            provider=provider,
            execution_mode=execution_mode,
            artifacts_dir=artifacts_dir,
            model=model,
            api_key_env=api_key_env,
            runtime_adapter=runtime_adapter,
            provider_name=provider_name,
            base_url=base_url,
            repo_path=repo_path,
            include_repo_status=include_repo_status,
            include_repo_diff=include_repo_diff,
            max_repo_diff_chars=max_repo_diff_chars,
        )
        if show_config:
            typer.echo(yaml.safe_dump(cfg, sort_keys=False).strip())
        if write_config_path:
            write_config(write_config_path, cfg)
        effective_artifacts_dir = _effective_artifacts_dir(cfg, artifacts_dir)
        config_snapshot_path = str(Path(effective_artifacts_dir) / CONFIG_SNAPSHOT_NAME)
        install_profile = cfg.get("install_profile") or {}
        if install_profile.get("kind") == "pack":
            source_label = f"pack '{install_profile.get('pack')}'"
        else:
            source_label = f"template '{cfg.get('template_key', recommend_template_for_scope(scope))}'"
        _run_with_policy(
            kind="task",
            cfg=cfg,
            artifacts_dir=effective_artifacts_dir,
            quiet=quiet,
            failure_label="ESE task failed",
            execute=lambda: run_pipeline(cfg=cfg, artifacts_dir=effective_artifacts_dir),
            success_message=lambda summary_path: (
                f"✅ Task run completed using {source_label} via "
                f"{str((cfg.get('runtime') or {}).get('adapter') or 'dry-run')}. "
                f"Summary: {summary_path} Config: {config_snapshot_path}"
            ),
        )
    except ConfigValidationError as err:
        typer.echo(f"❌ ESE task failed: {err}")
        raise typer.Exit(code=2) from err


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
    quiet: bool = typer.Option(False, "--quiet", help="Suppress preflight and follow-up chatter"),
):
    """Review a pull request or branch diff and export a GitHub-ready markdown summary."""
    try:
        context, cfg = build_pr_review_config(
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
        )
        if write_config_path:
            write_config(write_config_path, cfg)
        effective_artifacts_dir = _effective_artifacts_dir(cfg, artifacts_dir)
        review_path = str(Path(effective_artifacts_dir) / "pr_review.md")
        config_snapshot_path = str(Path(effective_artifacts_dir) / CONFIG_SNAPSHOT_NAME)

        def _execute_pr() -> str:
            summary_path = run_pipeline(cfg=cfg, artifacts_dir=effective_artifacts_dir)
            report = collect_run_report(effective_artifacts_dir)
            Path(review_path).write_text(
                render_pull_request_review_markdown(context, report),
                encoding="utf-8",
            )
            return summary_path

        _run_with_policy(
            kind="pr-review",
            cfg=cfg,
            artifacts_dir=effective_artifacts_dir,
            quiet=quiet,
            failure_label="ESE PR review failed",
            execute=_execute_pr,
            success_message=lambda summary_path: (
                "✅ PR review completed "
                f"for {context.head_ref} against {context.base_ref} via "
                f"{str((cfg.get('runtime') or {}).get('adapter') or 'dry-run')}. "
                f"Summary: {summary_path} Review: {review_path} Config: {config_snapshot_path}"
            ),
        )
    except (ConfigValidationError, PullRequestReviewError) as err:
        typer.echo(f"❌ ESE PR review failed: {err}")
        raise typer.Exit(code=2) from err


@app.command("status")
def status(
    artifacts_dir: str = typer.Option("artifacts", help="Directory containing pipeline_state.json"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable run status JSON"),
):
    """Print a concise run status summary for an artifacts directory."""
    try:
        report = collect_run_report(artifacts_dir)
    except RunReportError as err:
        typer.echo(f"❌ ESE status failed: {err}")
        raise typer.Exit(code=2) from err

    if json_output:
        typer.echo(json.dumps(report, indent=2))
        return

    typer.echo(render_status_text(report))


@app.command("evidence")
def evidence(
    artifacts_dir: str = typer.Option("artifacts", help="Directory containing run artifacts"),
    set_state: str | None = typer.Option(None, "--set-state", help="Persist a manual evidence state for the run"),
    actor: str | None = typer.Option(None, help="Optional operator or approver name recorded with a state change"),
    note: str | None = typer.Option(None, help="Optional note recorded with a state change"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable evidence state data"),
):
    """Inspect or update the current run evidence state."""
    try:
        previous_report = collect_run_report(artifacts_dir)
    except RunReportError as err:
        typer.echo(f"❌ ESE evidence failed: {err}")
        raise typer.Exit(code=2) from err

    if set_state:
        try:
            update_pipeline_evidence_state(
                artifacts_dir,
                state=set_state,
                previous_state=str(previous_report.get("evidence_state") or "").strip() or None,
                actor=actor,
                note=note,
                reason="Manual evidence state update.",
                source="manual",
            )
        except ValueError as err:
            typer.echo(f"❌ ESE evidence failed: {err}")
            raise typer.Exit(code=2) from err

    try:
        report = collect_run_report(artifacts_dir)
    except RunReportError as err:
        typer.echo(f"❌ ESE evidence failed: {err}")
        raise typer.Exit(code=2) from err

    payload = {
        "artifacts_dir": report.get("artifacts_dir"),
        "status": report.get("status"),
        "assurance_level": report.get("assurance_level"),
        "evidence_state": report.get("evidence_state"),
        "evidence_state_source": report.get("evidence_state_source"),
        "evidence_state_reason": report.get("evidence_state_reason"),
        "history": report.get("evidence_state_history", []),
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo(f"Evidence state: {payload['evidence_state']}")
    typer.echo(f"Source: {payload['evidence_state_source']}")
    typer.echo(f"Reason: {payload['evidence_state_reason']}")
    for entry in payload["history"]:
        updated_at = str(entry.get("updated_at") or "unknown")
        detail = f"{updated_at}: {entry.get('state')}"
        if entry.get("actor"):
            detail += f" by {entry['actor']}"
        if entry.get("note"):
            detail += f" ({entry['note']})"
        typer.echo(f"History: {detail}")
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


@app.command("suggestions")
def suggestions(
    artifacts_dir: str = typer.Option("artifacts", help="Directory containing run artifacts"),
    role: str | None = typer.Option(None, help="Optional role filter"),
    path_filter: str | None = typer.Option(None, "--path", help="Optional substring filter for target file paths"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable code suggestions JSON"),
):
    """Print synthesized programmer-facing code suggestions for a run."""
    try:
        report = collect_run_report(artifacts_dir)
    except RunReportError as err:
        typer.echo(f"❌ ESE suggestions failed: {err}")
        raise typer.Exit(code=2) from err

    filtered = _filtered_code_suggestions(report, role=role, path_filter=path_filter)
    filtered_report = dict(report)
    filtered_report["code_suggestions"] = filtered
    if json_output:
        typer.echo(render_code_suggestions_json(filtered_report))
        return
    typer.echo(render_code_suggestions_markdown(filtered_report))


@app.command("rerun")
def rerun(
    from_role: str = typer.Argument(..., help="Role to rerun from"),
    artifacts_dir: str = typer.Option("artifacts", help="Directory containing the prior run"),
    config: str | None = typer.Option(None, help="Config path. Defaults to the saved config snapshot in artifacts."),
    scope: str | None = typer.Option(None, help="Optional scope override for the rerun"),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress preflight and follow-up chatter"),
):
    """Rerun the pipeline from a specific role using existing prior artifacts as upstream context."""
    config_path = config or str(Path(artifacts_dir) / CONFIG_SNAPSHOT_NAME)
    try:
        effective_cfg = _load_effective_cfg(config=config_path, scope=scope)
        _run_with_policy(
            kind="rerun",
            cfg=effective_cfg,
            artifacts_dir=artifacts_dir,
            quiet=quiet,
            failure_label="ESE rerun failed",
            execute=lambda: run_pipeline(
                cfg=effective_cfg,
                artifacts_dir=artifacts_dir,
                start_role=from_role,
            ),
            success_message=lambda summary_path: (
                f"✅ Reran pipeline from role '{from_role}'. Summary: {summary_path}"
            ),
        )
    except ConfigValidationError as err:
        typer.echo(f"❌ ESE rerun failed: {err}")
        raise typer.Exit(code=2) from err


@app.command("export")
def export(
    artifacts_dir: str = typer.Option("artifacts", help="Directory containing run artifacts"),
    format: str = typer.Option(
        "sarif",
        help="Export format key (built-in: sarif, junit, plus installed plugins)",
    ),
    output_path: str | None = typer.Option(None, help="Optional output path override"),
):
    """Export run findings in CI-friendly formats."""
    try:
        report = collect_run_report(artifacts_dir)
    except RunReportError as err:
        typer.echo(f"❌ ESE export failed: {err}")
        raise typer.Exit(code=2) from err

    clean_format = format.strip().lower()
    try:
        payload, _content_type, default_filename = render_report_export(report, clean_format)
    except ValueError as err:
        typer.echo(f"❌ ESE export failed: {err}")
        raise typer.Exit(code=2) from err
    target = output_path or str(Path(artifacts_dir) / default_filename)

    Path(target).write_text(payload, encoding="utf-8")
    typer.echo(f"✅ Exported {clean_format} report: {target}")


@app.command("publish")
def publish(
    integration: str = typer.Option(..., help="Installed external integration key"),
    artifacts_dir: str = typer.Option("artifacts", help="Directory containing run artifacts"),
    target: str | None = typer.Option(None, help="Optional integration-specific target, such as a path or channel"),
    options: str | None = typer.Option(None, help="Optional JSON object of integration-specific publish options"),
    mark_state: str | None = typer.Option(None, "--mark-state", help="Optional evidence state to persist before publishing"),
    actor: str | None = typer.Option(None, help="Optional operator or approver name recorded with --mark-state"),
    note: str | None = typer.Option(None, help="Optional note recorded with --mark-state"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview the publish request without side effects"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable publish metadata"),
):
    """Publish run evidence through an installed external integration."""
    parsed_options: dict[str, Any] = {}
    if options:
        try:
            parsed = json.loads(options)
        except json.JSONDecodeError as err:
            typer.echo(f"❌ ESE publish failed: options must be valid JSON: {err}")
            raise typer.Exit(code=2) from err
        if not isinstance(parsed, dict):
            typer.echo("❌ ESE publish failed: options must decode to a JSON object.")
            raise typer.Exit(code=2)
        parsed_options = parsed

    if mark_state:
        try:
            report = collect_run_report(artifacts_dir)
            update_pipeline_evidence_state(
                artifacts_dir,
                state=mark_state,
                previous_state=str(report.get("evidence_state") or "").strip() or None,
                actor=actor,
                note=note,
                reason="Evidence state updated during publish.",
                source="publish",
            )
        except (RunReportError, ValueError) as err:
            typer.echo(f"❌ ESE publish failed: {err}")
            raise typer.Exit(code=2) from err

    try:
        result = publish_run_evidence(
            artifacts_dir=artifacts_dir,
            integration_key=integration,
            target=target,
            options=parsed_options,
            dry_run=dry_run,
        )
    except ValueError as err:
        typer.echo(f"❌ ESE publish failed: {err}")
        raise typer.Exit(code=2) from err

    payload = {
        "integration_key": result.integration_key,
        "status": result.status,
        "location": result.location,
        "message": result.message,
        "outputs": list(result.outputs),
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo(
        f"✅ Published run evidence via '{result.integration_key}' with status '{result.status}'."
    )
    if result.location:
        typer.echo(f"Location: {result.location}")
    if result.message:
        typer.echo(f"Message: {result.message}")
    for output in result.outputs:
        typer.echo(f"Output: {output}")


@app.command("feedback")
def feedback(
    role: str = typer.Option(..., help="Role that produced the finding"),
    title: str = typer.Option(..., help="Finding title"),
    rating: str = typer.Option(..., help="Feedback rating: useful, noisy, or wrong"),
    artifacts_dir: str = typer.Option("artifacts", help="Artifacts directory for the run family"),
    details: str | None = typer.Option(None, help="Optional note about why this feedback was recorded"),
):
    """Record operator feedback so future runs can improve signal without collapsing dissent."""
    try:
        entry = persist_feedback(
            artifacts_dir,
            role=role,
            title=title,
            feedback=rating,
            details=details,
        )
    except ValueError as err:
        typer.echo(f"❌ ESE feedback failed: {err}")
        raise typer.Exit(code=2) from err

    typer.echo(
        "✅ Feedback recorded "
        f"for {entry['role']} / {entry['title']} as {entry['feedback']}.",
    )


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
