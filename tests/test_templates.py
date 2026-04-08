from __future__ import annotations

import subprocess
from pathlib import Path

from ese.pack_sdk import load_pack_definition_from_manifest
from ese.templates import (
    build_task_config,
    provider_runtime_summary,
    recommend_template_for_scope,
)


def _run(args: list[str], *, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)  # noqa: S603


def _init_repo(path: Path) -> None:
    _run(["git", "init"], cwd=path)
    _run(["git", "config", "user.email", "ese@example.com"], cwd=path)
    _run(["git", "config", "user.name", "ESE Test"], cwd=path)
    (path / "app.py").write_text("print('hello')\n", encoding="utf-8")
    _run(["git", "add", "app.py"], cwd=path)
    _run(["git", "commit", "-m", "init"], cwd=path)


def test_build_task_config_uses_template_defaults() -> None:
    cfg = build_task_config(
        scope="Prepare a safer staged rollout",
        template_key="release-readiness",
        provider="openai",
        execution_mode="demo",
        artifacts_dir="custom-artifacts",
    )

    assert cfg["runtime"]["adapter"] == "dry-run"
    assert cfg["output"]["artifacts_dir"] == "custom-artifacts"
    assert "release_manager" in cfg["roles"]
    assert cfg["gating"]["fail_on_high"] is True


def test_build_task_config_supports_local_live_runs() -> None:
    cfg = build_task_config(
        scope="Review a local-only codegen workflow",
        template_key="feature-delivery",
        provider="local",
        execution_mode="auto",
        artifacts_dir="artifacts-local",
    )

    assert cfg["runtime"]["adapter"] == "local"
    assert cfg["runtime"]["local"]["base_url"] == "http://localhost:11434/v1"


def test_provider_runtime_summary_labels_builtin_adapters_clearly() -> None:
    summary = provider_runtime_summary(
        "local",
        execution_mode="live",
        runtime_adapter="local",
    )

    assert "built-in live adapter 'local'" in summary["note"]
    assert "custom runtime adapter" not in summary["note"]


def test_provider_runtime_summary_treats_dry_run_as_demo() -> None:
    summary = provider_runtime_summary(
        "openai",
        execution_mode="auto",
        runtime_adapter="dry-run",
    )

    assert "demo mode via dry-run artifacts" in summary["note"]


def test_recommend_template_for_scope_matches_common_intent() -> None:
    assert recommend_template_for_scope("Prepare release rollout and deploy plan") == "release-readiness"
    assert recommend_template_for_scope("Harden auth and close security gaps") == "security-hardening"
    assert recommend_template_for_scope("Improve latency on the hot path") == "performance-pass"
    assert recommend_template_for_scope("Refresh the README and migration guide") == "documentation-refresh"


def test_build_task_config_includes_repo_context(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "app.py").write_text("print('hello pluralism')\n", encoding="utf-8")
    (repo / "notes.md").write_text("deployment checklist draft\n", encoding="utf-8")

    cfg = build_task_config(
        scope="Review a local worktree change",
        template_key="feature-delivery",
        provider="openai",
        execution_mode="demo",
        repo_path=str(repo),
    )

    repo_context = cfg["input"]["repo_context"]
    assert cfg["input"]["repo_path"] == str(repo.resolve())
    assert "Repository context for this task run" in cfg["input"]["prompt"]
    assert repo_context["repo_path"] == str(repo.resolve())
    assert "app.py" in repo_context["changed_files"]
    assert "notes.md" in repo_context["untracked_files"]
    assert "deployment checklist draft" in cfg["input"]["prompt"]


def test_build_task_config_supports_installed_pack(monkeypatch) -> None:
    pack = load_pack_definition_from_manifest(
        Path("starters/release_governance_starter/src/release_governance_starter/ese_pack.yaml")
    )
    monkeypatch.setattr("ese.templates.get_config_pack", lambda key: pack)

    cfg = build_task_config(
        scope="Review the staged rollout plan for billing cutover",
        pack_key="release-governance",
        provider="openai",
        execution_mode="demo",
        artifacts_dir="starter-artifacts",
    )

    assert cfg["install_profile"]["kind"] == "pack"
    assert cfg["install_profile"]["pack"] == "release-governance"
    assert cfg["preset"] == "strict"
    assert cfg["role_order"] == ["release_planner", "release_gatekeeper"]
    assert "release_planner" in cfg["roles"]
    assert cfg["runtime"]["adapter"] == "dry-run"
