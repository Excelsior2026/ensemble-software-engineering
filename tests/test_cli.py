from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from ese.artifact_views import ArtifactViewDefinition
from ese.cli import app, main
from ese.config_packs import ConfigPackDefinition, PackRoleDefinition
from ese.integrations import IntegrationDefinition, IntegrationPublishResult
from ese.pack_sdk import load_pack_definition_from_manifest
from ese.policy_checks import PolicyCheckDefinition
from ese.report_exporters import ReportExporterDefinition

runner = CliRunner()


def _write_cfg(path: Path, cfg: dict) -> str:
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return str(path)


def _base_cfg() -> dict:
    return {
        "version": 1,
        "mode": "ensemble",
        "provider": {
            "name": "openai",
            "model": "gpt-5-mini",
            "api_key_env": "OPENAI_API_KEY",
        },
        "roles": {
            "architect": {"model": "gpt-5"},
            "implementer": {"model": "gpt-5-mini"},
        },
        "constraints": {
            "disallow_same_model_pairs": [["architect", "implementer"]],
        },
        "runtime": {
            "adapter": "dry-run",
        },
        "input": {
            "scope": "Ship a CLI improvement safely",
        },
    }


def test_roles_command_lists_known_role() -> None:
    result = runner.invoke(app, ["roles"])

    assert result.exit_code == 0
    assert "architect" in result.stdout


def test_packs_command_reports_when_no_packs_are_installed() -> None:
    result = runner.invoke(app, ["packs"])

    assert result.exit_code == 0
    assert "No config packs installed." in result.stdout


def test_packs_command_lists_installed_packs(monkeypatch) -> None:
    monkeypatch.setattr(
        "ese.cli.discover_config_packs",
        lambda: (
            [
                ConfigPackDefinition(
                    key="release-ops",
                    title="Release Operations",
                    summary="Reusable release-review pack",
                    preset="strict",
                    goal_profile="high-quality",
                    roles=(
                        PackRoleDefinition(
                            key="release_planner",
                            responsibility="Plan the release",
                            prompt="Plan the release.",
                        ),
                    ),
                )
            ],
            [],
        ),
    )

    result = runner.invoke(app, ["packs"])

    assert result.exit_code == 0
    assert "release-ops" in result.stdout


def test_policies_command_reports_when_none_are_installed() -> None:
    result = runner.invoke(app, ["policies"])

    assert result.exit_code == 0
    assert "No external policy checks installed." in result.stdout


def test_policies_command_lists_installed_policies(monkeypatch) -> None:
    monkeypatch.setattr(
        "ese.cli.discover_policy_checks",
        lambda: (
            [
                PolicyCheckDefinition(
                    key="release-safety",
                    title="Release Safety",
                    summary="Require release-focused roles for rollout scopes.",
                    check=lambda context: [],
                )
            ],
            [],
        ),
    )

    result = runner.invoke(app, ["policies"])

    assert result.exit_code == 0
    assert "release-safety" in result.stdout


def test_exporters_command_lists_builtin_and_external_exporters(monkeypatch) -> None:
    monkeypatch.setattr(
        "ese.cli.discover_external_report_exporters",
        lambda: (
            [
                ReportExporterDefinition(
                    key="blocker-csv",
                    title="Blocker CSV",
                    summary="CSV export of blocker findings.",
                    content_type="text/csv; charset=utf-8",
                    default_filename="ese_blockers.csv",
                    render=lambda report: "role,severity\narchitect,HIGH\n",
                )
            ],
            [],
        ),
    )

    result = runner.invoke(app, ["exporters"])

    assert result.exit_code == 0
    assert "sarif" in result.stdout
    assert "blocker-csv" in result.stdout


def test_views_command_lists_installed_artifact_views(monkeypatch) -> None:
    monkeypatch.setattr(
        "ese.cli.discover_artifact_views",
        lambda: (
            [
                ArtifactViewDefinition(
                    key="release-brief",
                    title="Release Brief",
                    summary="Generated release brief for dashboard viewing.",
                    format="md",
                    render=lambda report: "# Release Brief\n",
                )
            ],
            [],
        ),
    )

    result = runner.invoke(app, ["views"])

    assert result.exit_code == 0
    assert "release-brief" in result.stdout


def test_integrations_command_lists_installed_integrations(monkeypatch) -> None:
    monkeypatch.setattr(
        "ese.cli.discover_integrations",
        lambda: (
            [
                IntegrationDefinition(
                    key="filesystem-evidence",
                    title="Filesystem Evidence",
                    summary="Write a portable evidence bundle to disk.",
                    publish=lambda context, request: {"status": "published"},
                )
            ],
            [],
        ),
    )

    result = runner.invoke(app, ["integrations"])

    assert result.exit_code == 0
    assert "filesystem-evidence" in result.stdout


def test_extensions_command_lists_supported_surfaces() -> None:
    result = runner.invoke(app, ["extensions"])

    assert result.exit_code == 0
    assert "config-packs" in result.stdout
    assert "integrations" in result.stdout


def test_no_args_prints_help_when_non_interactive() -> None:
    result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert "Usage:" in result.stdout
    assert "dashboard" in result.stdout


def test_no_args_launches_dashboard_when_interactive(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def _fake_launch_dashboard(**kwargs):  # noqa: ANN003
        calls.append(kwargs)

    class _InteractiveStream:
        @staticmethod
        def isatty() -> bool:
            return True

    class _FakeContext:
        invoked_subcommand = None

        @staticmethod
        def get_help() -> str:
            return "help"

    monkeypatch.setattr("ese.cli._launch_dashboard", _fake_launch_dashboard)
    monkeypatch.setattr("ese.cli.sys.stdin", _InteractiveStream())
    monkeypatch.setattr("ese.cli.sys.stdout", _InteractiveStream())

    main(_FakeContext())  # type: ignore[arg-type]

    assert calls == [{}]


def test_doctor_command_exits_nonzero_on_violation(tmp_path: Path) -> None:
    cfg = _base_cfg()
    cfg["roles"]["implementer"]["model"] = "gpt-5"
    config_path = _write_cfg(tmp_path / "ese.config.yaml", cfg)

    result = runner.invoke(app, ["doctor", "--config", config_path])

    assert result.exit_code == 2
    assert "share model" in result.stdout


def test_start_command_runs_pipeline_and_writes_summary(tmp_path: Path) -> None:
    cfg = _base_cfg()
    config_path = _write_cfg(tmp_path / "ese.config.yaml", cfg)
    artifacts_dir = tmp_path / "artifacts"

    result = runner.invoke(
        app,
        ["start", "--config", config_path, "--artifacts-dir", str(artifacts_dir)],
    )

    assert result.exit_code == 0
    assert (artifacts_dir / "ese_summary.md").exists()
    assert "Pipeline completed" in result.stdout


def test_start_command_uses_config_artifacts_dir_by_default(tmp_path: Path) -> None:
    cfg = _base_cfg()
    configured_dir = tmp_path / "configured-artifacts"
    cfg["output"] = {"artifacts_dir": str(configured_dir), "enforce_json": True}
    config_path = _write_cfg(tmp_path / "ese.config.yaml", cfg)

    result = runner.invoke(app, ["start", "--config", config_path])

    assert result.exit_code == 0
    assert (configured_dir / "ese_summary.md").exists()


def test_run_alias_still_works(tmp_path: Path) -> None:
    cfg = _base_cfg()
    config_path = _write_cfg(tmp_path / "ese.config.yaml", cfg)
    artifacts_dir = tmp_path / "artifacts"

    result = runner.invoke(
        app,
        ["run", "--config", config_path, "--artifacts-dir", str(artifacts_dir)],
    )

    assert result.exit_code == 0
    assert (artifacts_dir / "ese_summary.md").exists()


def test_publish_command_supports_external_integrations(monkeypatch) -> None:
    monkeypatch.setattr(
        "ese.cli.publish_run_evidence",
        lambda **kwargs: IntegrationPublishResult(
            integration_key="filesystem-evidence",
            status="published",
            location="/tmp/evidence",
            message="bundle written",
            outputs=("/tmp/evidence/manifest.json",),
        ),
    )

    result = runner.invoke(
        app,
        [
            "publish",
            "--integration",
            "filesystem-evidence",
            "--artifacts-dir",
            "artifacts",
            "--options",
            '{"copy_documents": true}',
        ],
    )

    assert result.exit_code == 0
    assert "filesystem-evidence" in result.stdout
    assert "/tmp/evidence/manifest.json" in result.stdout


def test_doctor_environment_command_reports_broken_extensions(monkeypatch) -> None:
    monkeypatch.setattr(
        "ese.cli.evaluate_doctor_environment",
        lambda: (
            False,
            ["[environment:config_packs] Failed to load config pack 'broken_pack': missing manifest"],
            {
                "config_packs": {"installed": [], "failures": [{"entry_point": "broken_pack", "error": "missing manifest"}]},
                "policy_checks": {"installed": [], "failures": []},
                "report_exporters": {"installed": [], "failures": []},
                "artifact_views": {"installed": [], "failures": []},
                "integrations": {"installed": [], "failures": []},
            },
        ),
    )

    result = runner.invoke(app, ["doctor", "--environment"])

    assert result.exit_code == 2
    assert "Config Packs: 0 installed, 1 broken" in result.stdout


def test_evidence_command_can_persist_manual_state(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    from ese.pipeline import run_pipeline

    run_pipeline(_base_cfg(), artifacts_dir=str(artifacts_dir))

    result = runner.invoke(
        app,
        [
            "evidence",
            "--artifacts-dir",
            str(artifacts_dir),
            "--set-state",
            "approved",
            "--actor",
            "bill",
            "--note",
            "Approved after release review.",
            "--json",
        ],
    )

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["evidence_state"] == "approved"
    assert payload["history"][-1]["actor"] == "bill"


def test_publish_command_can_mark_evidence_state(monkeypatch, tmp_path: Path) -> None:
    from ese.pipeline import run_pipeline

    artifacts_dir = tmp_path / "artifacts"
    run_pipeline(_base_cfg(), artifacts_dir=str(artifacts_dir))
    monkeypatch.setattr(
        "ese.cli.publish_run_evidence",
        lambda **kwargs: IntegrationPublishResult(
            integration_key="filesystem-evidence",
            status="published",
            location="/tmp/evidence",
            outputs=(),
        ),
    )

    result = runner.invoke(
        app,
        [
            "publish",
            "--integration",
            "filesystem-evidence",
            "--artifacts-dir",
            str(artifacts_dir),
            "--mark-state",
            "released",
            "--actor",
            "bill",
        ],
    )

    state = json.loads((artifacts_dir / "pipeline_state.json").read_text(encoding="utf-8"))
    assert result.exit_code == 0
    assert state["evidence_state"] == "released"
    assert state["evidence_state_history"][-1]["source"] == "publish"
    assert state["evidence_state_history"][-1]["reason"] == "Evidence state updated after a successful publish."


def test_publish_command_does_not_mark_evidence_state_on_failure(monkeypatch, tmp_path: Path) -> None:
    from ese.pipeline import run_pipeline

    artifacts_dir = tmp_path / "artifacts"
    run_pipeline(_base_cfg(), artifacts_dir=str(artifacts_dir))
    original_state = json.loads((artifacts_dir / "pipeline_state.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(
        "ese.cli.publish_run_evidence",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("integration failed")),
    )

    result = runner.invoke(
        app,
        [
            "publish",
            "--integration",
            "filesystem-evidence",
            "--artifacts-dir",
            str(artifacts_dir),
            "--mark-state",
            "released",
        ],
    )

    state = json.loads((artifacts_dir / "pipeline_state.json").read_text(encoding="utf-8"))
    assert result.exit_code == 2
    assert state.get("evidence_state") == original_state.get("evidence_state")
    assert state.get("evidence_state_history") == original_state.get("evidence_state_history")


def test_publish_command_does_not_mark_evidence_state_on_dry_run(monkeypatch, tmp_path: Path) -> None:
    from ese.pipeline import run_pipeline

    artifacts_dir = tmp_path / "artifacts"
    run_pipeline(_base_cfg(), artifacts_dir=str(artifacts_dir))
    original_state = json.loads((artifacts_dir / "pipeline_state.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(
        "ese.cli.publish_run_evidence",
        lambda **kwargs: IntegrationPublishResult(
            integration_key="filesystem-evidence",
            status="dry-run",
            location="/tmp/evidence",
            outputs=(),
        ),
    )

    result = runner.invoke(
        app,
        [
            "publish",
            "--integration",
            "filesystem-evidence",
            "--artifacts-dir",
            str(artifacts_dir),
            "--mark-state",
            "released",
            "--dry-run",
        ],
    )

    state = json.loads((artifacts_dir / "pipeline_state.json").read_text(encoding="utf-8"))
    assert result.exit_code == 0
    assert state.get("evidence_state") == original_state.get("evidence_state")
    assert state.get("evidence_state_history") == original_state.get("evidence_state_history")


def test_start_command_accepts_scope_override(tmp_path: Path) -> None:
    cfg = _base_cfg()
    cfg.pop("input")
    config_path = _write_cfg(tmp_path / "ese.config.yaml", cfg)
    artifacts_dir = tmp_path / "artifacts"

    result = runner.invoke(
        app,
        [
            "start",
            "--config",
            config_path,
            "--artifacts-dir",
            str(artifacts_dir),
            "--scope",
            "Audit the release process for rollout gaps",
        ],
    )

    assert result.exit_code == 0
    assert (artifacts_dir / "ese_summary.md").exists()


def test_task_command_runs_template_without_hand_written_config(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"

    result = runner.invoke(
        app,
        [
            "task",
            "Prepare a safer release workflow",
            "--template",
            "release-readiness",
            "--artifacts-dir",
            str(artifacts_dir),
        ],
    )

    assert result.exit_code == 0
    assert (artifacts_dir / "ese_summary.md").exists()
    assert "Task run completed" in result.stdout


def test_task_command_runs_installed_pack_without_hand_written_config(tmp_path: Path, monkeypatch) -> None:
    artifacts_dir = tmp_path / "artifacts"
    pack = load_pack_definition_from_manifest(
        Path("starters/release_governance_starter/src/release_governance_starter/ese_pack.yaml")
    )
    monkeypatch.setattr("ese.templates.get_config_pack", lambda key: pack)

    result = runner.invoke(
        app,
        [
            "task",
            "Review the staged rollout plan for billing cutover",
            "--pack",
            "release-governance",
            "--artifacts-dir",
            str(artifacts_dir),
        ],
    )

    assert result.exit_code == 0
    assert (artifacts_dir / "ese_summary.md").exists()
    assert "source: pack 'release-governance'" in result.stdout
    assert "pack 'release-governance'" in result.stdout


def test_task_command_fails_on_doctor_violations(monkeypatch) -> None:
    monkeypatch.setattr(
        "ese.cli.build_task_config",
        lambda **kwargs: {
            "version": 1,
            "mode": "ensemble",
            "provider": {"name": "openai", "model": "gpt-5-mini"},
            "roles": {
                "architect": {"model": "gpt-5"},
                "implementer": {"model": "gpt-5"},
            },
            "runtime": {"adapter": "dry-run"},
            "input": {"scope": "Review the rollout"},
        },
    )

    result = runner.invoke(app, ["task", "Review the rollout"])

    assert result.exit_code == 2
    assert "ESE doctor failed" in result.stdout


def test_status_and_report_commands_summarize_artifacts(tmp_path: Path) -> None:
    cfg = _base_cfg()
    config_path = _write_cfg(tmp_path / "ese.config.yaml", cfg)
    artifacts_dir = tmp_path / "artifacts"

    start_result = runner.invoke(
        app,
        ["start", "--config", config_path, "--artifacts-dir", str(artifacts_dir)],
    )
    assert start_result.exit_code == 0

    status_result = runner.invoke(
        app,
        ["status", "--artifacts-dir", str(artifacts_dir)],
    )
    status_json_result = runner.invoke(
        app,
        ["status", "--artifacts-dir", str(artifacts_dir), "--json"],
    )
    report_result = runner.invoke(
        app,
        ["report", "--artifacts-dir", str(artifacts_dir)],
    )

    assert status_result.exit_code == 0
    assert "Status: completed" in status_result.stdout
    assert status_json_result.exit_code == 0
    assert json.loads(status_json_result.stdout)["status"] == "completed"
    assert report_result.exit_code == 0
    assert "Roles:" in report_result.stdout


def test_start_quiet_suppresses_preflight_chatter(tmp_path: Path) -> None:
    cfg = _base_cfg()
    config_path = _write_cfg(tmp_path / "ese.config.yaml", cfg)
    artifacts_dir = tmp_path / "artifacts"

    result = runner.invoke(
        app,
        ["start", "--config", config_path, "--artifacts-dir", str(artifacts_dir), "--quiet"],
    )

    assert result.exit_code == 0
    assert "Preflight:" not in result.stdout
    assert "Top consensus:" not in result.stdout
    assert str(artifacts_dir / "ese_summary.md") in result.stdout


def test_export_and_feedback_commands_write_outputs(tmp_path: Path) -> None:
    cfg = _base_cfg()
    config_path = _write_cfg(tmp_path / "ese.config.yaml", cfg)
    artifacts_dir = tmp_path / "artifacts"

    start_result = runner.invoke(
        app,
        ["start", "--config", config_path, "--artifacts-dir", str(artifacts_dir)],
    )
    assert start_result.exit_code == 0

    export_path = tmp_path / "report.sarif.json"
    export_result = runner.invoke(
        app,
        ["export", "--artifacts-dir", str(artifacts_dir), "--format", "sarif", "--output-path", str(export_path)],
    )
    feedback_result = runner.invoke(
        app,
        [
            "feedback",
            "--artifacts-dir",
            str(artifacts_dir),
            "--role",
            "architect",
            "--title",
            "Architect suggestion",
            "--rating",
            "useful",
        ],
    )

    assert export_result.exit_code == 0
    assert export_path.exists()
    assert feedback_result.exit_code == 0
    assert "Feedback recorded" in feedback_result.stdout


def test_export_command_supports_external_exporters(tmp_path: Path, monkeypatch) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    monkeypatch.setattr("ese.cli.collect_run_report", lambda path: {"artifacts_dir": path, "blockers": []})
    monkeypatch.setattr(
        "ese.cli.render_report_export",
        lambda report, export_format: (
            "role,severity\narchitect,HIGH\n",
            "text/csv; charset=utf-8",
            "ese_blockers.csv",
        ),
    )

    result = runner.invoke(app, ["export", "--artifacts-dir", str(artifacts_dir), "--format", "blocker-csv"])

    assert result.exit_code == 0
    assert (artifacts_dir / "ese_blockers.csv").exists()


def test_suggestions_command_renders_filtered_code_suggestions(monkeypatch) -> None:
    monkeypatch.setattr(
        "ese.cli.collect_run_report",
        lambda artifacts_dir: {
            "artifacts_dir": artifacts_dir,
            "scope": "Review the auth path",
            "code_suggestions": [
                {
                    "role": "security_auditor",
                    "source": "code_suggestion",
                    "severity": "HIGH",
                    "title": "Harden auth middleware",
                    "suggestion": "Validate the tenant token before entering the handler.",
                    "path": "src/auth.py",
                    "kind": "patch",
                    "snippet": "if not token:\n    raise AuthError()",
                },
            ],
        },
    )

    result = runner.invoke(
        app,
        ["suggestions", "--artifacts-dir", "artifacts", "--path", "auth.py"],
    )

    assert result.exit_code == 0
    assert "# Code Suggestions" in result.stdout
    assert "src/auth.py" in result.stdout
    assert "Validate the tenant token" in result.stdout


def test_pr_command_runs_pull_request_review(monkeypatch, tmp_path: Path) -> None:
    context = type("Context", (), {"head_ref": "feature", "base_ref": "origin/main"})()
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()

    def _fake_build_pr_review_config(**kwargs):  # noqa: ANN003
        return (
            context,
            {
                "provider": {"name": "openai", "model": "gpt-5-mini"},
                "roles": {"adversarial_reviewer": {}},
                "input": {"scope": "Review the pull request safely"},
                "runtime": {"adapter": "dry-run"},
                "output": {"artifacts_dir": str(artifacts_dir)},
            },
        )

    monkeypatch.setattr("ese.cli.build_pr_review_config", _fake_build_pr_review_config)
    monkeypatch.setattr("ese.cli.run_pipeline", lambda **kwargs: str(artifacts_dir / "ese_summary.md"))
    monkeypatch.setattr(
        "ese.cli.collect_run_report",
        lambda artifacts_dir: {"roles": [], "blockers": [], "next_steps": []},
    )
    monkeypatch.setattr("ese.cli.render_pull_request_review_markdown", lambda context, report: "# Review\n")

    result = runner.invoke(
        app,
        [
            "pr",
            "--repo-path",
            ".",
            "--base",
            "origin/main",
            "--head",
            "feature",
        ],
    )

    assert result.exit_code == 0
    assert "PR review completed" in result.stdout
    assert "pr_review.md" in result.stdout


def test_pr_command_fails_on_doctor_violations(monkeypatch) -> None:
    context = type("Context", (), {"head_ref": "feature", "base_ref": "origin/main"})()
    monkeypatch.setattr(
        "ese.cli.build_pr_review_config",
        lambda **kwargs: (
            context,
            {
                "version": 1,
                "mode": "ensemble",
                "provider": {"name": "openai", "model": "gpt-5-mini"},
                "roles": {
                    "architect": {"model": "gpt-5"},
                    "implementer": {"model": "gpt-5"},
                },
                "runtime": {"adapter": "dry-run"},
                "input": {"scope": "Review the diff"},
                "output": {"artifacts_dir": "artifacts"},
            },
        ),
    )

    result = runner.invoke(app, ["pr", "--repo-path", ".", "--base", "origin/main", "--head", "feature"])

    assert result.exit_code == 2
    assert "ESE doctor failed" in result.stdout


def test_rerun_command_fails_on_doctor_violations(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    _write_cfg(
        artifacts_dir / "ese_config.snapshot.yaml",
        {
            **_base_cfg(),
            "roles": {
                "architect": {"model": "gpt-5"},
                "implementer": {"model": "gpt-5"},
            },
        },
    )

    result = runner.invoke(app, ["rerun", "implementer", "--artifacts-dir", str(artifacts_dir)])

    assert result.exit_code == 2
    assert "ESE doctor failed" in result.stdout
