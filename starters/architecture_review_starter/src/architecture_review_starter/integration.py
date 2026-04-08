"""Evidence integration for the architecture-review starter."""

from __future__ import annotations

import json
from pathlib import Path

from ese.integrations import (
    INTEGRATION_CONTRACT_VERSION,
    PUBLISH_STATUS_DRY_RUN,
    PUBLISH_STATUS_PUBLISHED,
    IntegrationDefinition,
    IntegrationPublishResult,
)


def _resolve_target(artifacts_dir: str, target: str | None) -> Path:
    if target:
        destination = Path(target).expanduser()
        if destination.is_absolute():
            return destination.resolve()
        return (Path(artifacts_dir) / destination).resolve()
    return (Path(artifacts_dir) / "architecture-decision-evidence").resolve()


def _publish_packet(context, request):
    target_dir = _resolve_target(context.artifacts_dir, request.target)
    packet_path = target_dir / "decision_packet.json"
    brief_path = target_dir / "decision_brief.md"
    outputs = (str(packet_path), str(brief_path))

    if request.dry_run:
        return IntegrationPublishResult(
            integration_key="architecture-decision-bundle",
            status=PUBLISH_STATUS_DRY_RUN,
            location=str(target_dir),
            message="Previewed architecture decision bundle.",
            outputs=outputs,
        )

    report = dict(context.report)
    target_dir.mkdir(parents=True, exist_ok=True)
    packet_path.write_text(
        json.dumps(
            {
                "run_id": report.get("run_id"),
                "scope": report.get("scope"),
                "status": report.get("status"),
                "evidence_state": report.get("evidence_state"),
                "assurance_level": report.get("assurance_level"),
                "blockers": report.get("blockers", []),
                "recurring_unknowns": report.get("recurring_unknowns", []),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    brief_path.write_text(
        "\n".join(
            [
                "# Architecture Decision Bundle",
                "",
                f"- Scope: {report.get('scope') or 'No scope recorded.'}",
                f"- Status: {report.get('status') or 'unknown'}",
                f"- Evidence state: {report.get('evidence_state') or 'draft'}",
                f"- Assurance: {report.get('assurance_level') or 'unknown'}",
                f"- Blockers: {len(report.get('blockers', []))}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return IntegrationPublishResult(
        integration_key="architecture-decision-bundle",
        status=PUBLISH_STATUS_PUBLISHED,
        location=str(target_dir),
        message="Published architecture decision bundle.",
        outputs=outputs,
    )


def load_integration():
    """Return the architecture decision evidence integration."""
    return IntegrationDefinition(
        key="architecture-decision-bundle",
        title="Architecture Decision Bundle",
        summary="Write a portable architecture decision packet and brief to disk.",
        publish=_publish_packet,
        contract_version=INTEGRATION_CONTRACT_VERSION,
    )
