from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from ese.cli import app
from ese.starter_sdk import (
    describe_starter_project,
    scaffold_starter_project,
    smoke_test_starter_project,
)

runner = CliRunner()
RELEASE_STARTER_DIR = Path("starters/release_governance_starter")
ARCHITECTURE_STARTER_DIR = Path("starters/architecture_review_starter")


def test_scaffold_starter_project_creates_valid_bundle_manifest(tmp_path: Path) -> None:
    project_dir = tmp_path / "release_governance_starter"
    project = scaffold_starter_project(
        project_dir,
        starter_key="release-governance",
    )

    manifest_path = project_dir / "src" / "release_governance_starter" / "ese_starter.yaml"
    assert project.manifest_path == manifest_path.resolve()
    assert manifest_path.exists()

    report = describe_starter_project(project_dir)
    assert report["starter_key"] == "release-governance"
    assert report["package_name"] == "release_governance_starter"
    assert report["pack_manifest_path"].endswith("ese_pack.yaml")
    assert report["policy_checks"] == ["release-governance-safety"]


def test_smoke_test_starter_project_loads_bundle_extensions(tmp_path: Path) -> None:
    project_dir = tmp_path / "release_governance_starter"
    scaffold_starter_project(
        project_dir,
        starter_key="release-governance",
    )

    report = smoke_test_starter_project(project_dir)

    assert report["starter_key"] == "release-governance"
    assert report["pack_smoke"]["config"]["install_profile"]["pack"] == "release-governance"
    assert report["loaded"]["policy_checks"] == ["release-governance-safety"]
    assert report["loaded"]["report_exporters"] == ["release-governance-csv"]
    assert report["loaded"]["artifact_views"] == ["release-governance-brief"]
    assert report["loaded"]["integrations"] == ["release-governance-bundle"]


def test_starter_cli_init_validate_and_test_commands(tmp_path: Path) -> None:
    project_dir = tmp_path / "architecture_review_starter"

    init_result = runner.invoke(
        app,
        [
            "starter",
            "init",
            str(project_dir),
            "--key",
            "architecture-review",
        ],
    )
    assert init_result.exit_code == 0
    assert "Scaffolded external starter bundle" in init_result.stdout

    validate_result = runner.invoke(app, ["starter", "validate", str(project_dir), "--json"])
    assert validate_result.exit_code == 0
    validation_payload = json.loads(validate_result.stdout)
    assert validation_payload["starter_key"] == "architecture-review"

    test_result = runner.invoke(app, ["starter", "test", str(project_dir), "--json"])
    assert test_result.exit_code == 0
    smoke_payload = json.loads(test_result.stdout)
    assert smoke_payload["starter_key"] == "architecture-review"
    assert smoke_payload["pack_smoke"]["config"]["install_profile"]["kind"] == "pack"


def test_release_governance_starter_bundle_is_valid_and_smoke_testable() -> None:
    report = describe_starter_project(RELEASE_STARTER_DIR)
    smoke = smoke_test_starter_project(RELEASE_STARTER_DIR)

    assert report["starter_key"] == "release-governance"
    assert report["report_exporters"] == ["release-gate-csv"]
    assert report["integrations"] == ["release-governance-bundle"]
    assert smoke["loaded"]["artifact_views"] == ["go-live-brief"]


def test_architecture_review_starter_bundle_is_valid_and_smoke_testable() -> None:
    report = describe_starter_project(ARCHITECTURE_STARTER_DIR)
    smoke = smoke_test_starter_project(ARCHITECTURE_STARTER_DIR)

    assert report["starter_key"] == "architecture-review"
    assert report["report_exporters"] == ["architecture-risk-csv"]
    assert report["integrations"] == ["architecture-decision-bundle"]
    assert smoke["loaded"]["integrations"] == ["architecture-decision-bundle"]
