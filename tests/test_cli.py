from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from ese.cli import app, main
from ese.config_packs import ConfigPackDefinition, PackRoleDefinition
from ese.policy_checks import PolicyCheckDefinition

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
        "ese.cli.list_config_packs",
        lambda: [
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
