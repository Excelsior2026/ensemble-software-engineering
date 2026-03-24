from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

runner = importlib.import_module("apps.contract_intelligence.orchestration.bid_review_runner")

run_bid_review = runner.run_bid_review


def test_bid_review_runner_emits_core_artifacts(tmp_path: Path) -> None:
    project_dir = tmp_path / "riverside-bridge"
    project_dir.mkdir()
    (project_dir / "Prime Contract Agreement.md").write_text(
        "\n".join(
            [
                "Owner may terminate for convenience.",
                "Subcontractor shall be paid on a pay-if-paid basis.",
                "No damages for delay shall be allowed.",
                "Notice of claim must be provided within 7 calendar days.",
            ]
        ),
        encoding="utf-8",
    )
    (project_dir / "General Conditions.txt").write_text(
        "Contractor shall defend, indemnify, and hold harmless the owner.",
        encoding="utf-8",
    )
    (project_dir / "Insurance Requirements.md").write_text(
        "\n".join(
            [
                "Additional insured status is required.",
                "Coverage shall be primary and noncontributory.",
                "Waiver of subrogation applies.",
                "Certificates of insurance are required before starting work.",
            ]
        ),
        encoding="utf-8",
    )
    (project_dir / "Funding Memo.md").write_text(
        "\n".join(
            [
                "This project uses federal aid and Davis-Bacon prevailing wage requirements.",
                "Certified payroll must be submitted weekly.",
                "DBE participation goals apply.",
            ]
        ),
        encoding="utf-8",
    )

    result = run_bid_review(project_dir)

    assert result.decision_summary.recommendation.value == "go_with_conditions"
    assert result.decision_summary.human_review_required is True

    inventory = json.loads((result.artifacts_dir / "document_inventory.json").read_text())
    assert inventory["project_id"] == "riverside-bridge"
    assert inventory["missing_required_documents"] == []
    assert len(inventory["documents"]) == 4

    risk_findings = json.loads((result.artifacts_dir / "risk_findings.json").read_text())
    assert any(item["category"] == "payment_terms" for item in risk_findings)
    assert any(item["category"] == "delay_exposure" for item in risk_findings)

    insurance_findings = json.loads((result.artifacts_dir / "insurance_findings.json").read_text())
    assert any(item["category"] == "additional_insured" for item in insurance_findings)

    compliance_findings = json.loads((result.artifacts_dir / "compliance_findings.json").read_text())
    assert any(item["category"] == "davis_bacon" for item in compliance_findings)

    obligations = json.loads((result.artifacts_dir / "obligations_register.json").read_text())
    assert any(item["obligation_type"] == "notice_deadline" for item in obligations)
    assert any(item["title"] == "Submit certified payroll reports" for item in obligations)


def test_bid_review_runner_flags_missing_required_documents(tmp_path: Path) -> None:
    project_dir = tmp_path / "missing-insurance-package"
    project_dir.mkdir()
    (project_dir / "Prime Contract Agreement.md").write_text(
        "Subcontractor shall be paid on a pay-if-paid basis.",
        encoding="utf-8",
    )

    result = run_bid_review(project_dir)
    decision = json.loads((result.artifacts_dir / "decision_summary.json").read_text())
    challenges = json.loads((result.artifacts_dir / "review_challenges.json").read_text())

    assert decision["human_review_required"] is True
    assert decision["recommendation"] == "no_go"
    assert any("general_conditions" in item for item in challenges["missed_hazards"])
    assert any("insurance_requirements" in item for item in challenges["missed_hazards"])
