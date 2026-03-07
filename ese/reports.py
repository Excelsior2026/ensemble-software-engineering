"""Helpers for loading and summarizing ESE run artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SEVERITY_ORDER = ("CRITICAL", "HIGH", "MEDIUM", "LOW")


class RunReportError(ValueError):
    """Raised when a run report cannot be loaded from artifacts."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as err:
        raise RunReportError(f"Required run artifact not found: {path}") from err
    except json.JSONDecodeError as err:
        raise RunReportError(f"Run artifact is not valid JSON: {path}") from err

    if not isinstance(parsed, dict):
        raise RunReportError(f"Run artifact must be a JSON object: {path}")
    return parsed


def load_pipeline_state(artifacts_dir: str) -> dict[str, Any]:
    path = Path(artifacts_dir) / "pipeline_state.json"
    return _read_json(path)


def load_role_report(artifact_path: str) -> dict[str, Any] | None:
    path = Path(artifact_path)
    if path.suffix.lower() != ".json":
        return None
    return _read_json(path)


def collect_run_report(artifacts_dir: str) -> dict[str, Any]:
    root = Path(artifacts_dir)
    state = load_pipeline_state(str(root))
    roles: list[dict[str, Any]] = []
    severity_counts = {severity: 0 for severity in SEVERITY_ORDER}
    blockers: list[dict[str, Any]] = []
    next_steps: list[dict[str, str]] = []

    for item in state.get("execution", []):
        if not isinstance(item, dict):
            continue

        role = str(item.get("role") or "").strip()
        artifact = str(item.get("artifact") or "").strip()
        if not role or not artifact:
            continue

        artifact_path = Path(artifact)
        if not artifact_path.is_absolute():
            artifact_path = root / artifact_path

        entry: dict[str, Any] = {
            "role": role,
            "model": str(item.get("model") or ""),
            "artifact": str(artifact_path),
            "summary": "",
            "findings": [],
            "next_steps": [],
            "artifacts": [],
            "report_format": artifact_path.suffix.lower().lstrip("."),
        }

        report = load_role_report(str(artifact_path))
        if report is None:
            entry["summary"] = artifact_path.read_text(encoding="utf-8")[:500].strip()
            roles.append(entry)
            continue

        entry["summary"] = str(report.get("summary") or "").strip()
        entry["findings"] = report.get("findings") if isinstance(report.get("findings"), list) else []
        entry["next_steps"] = [step for step in report.get("next_steps", []) if isinstance(step, str) and step.strip()]
        entry["artifacts"] = [step for step in report.get("artifacts", []) if isinstance(step, str) and step.strip()]

        for finding in entry["findings"]:
            if not isinstance(finding, dict):
                continue
            severity = str(finding.get("severity") or "").upper()
            if severity in severity_counts:
                severity_counts[severity] += 1
            if severity in {"HIGH", "CRITICAL"}:
                blockers.append(
                    {
                        "role": role,
                        "severity": severity,
                        "title": str(finding.get("title") or "").strip(),
                        "details": str(finding.get("details") or "").strip(),
                    },
                )

        for step in entry["next_steps"]:
            next_steps.append({"role": role, "text": step})

        roles.append(entry)

    finding_count = sum(severity_counts.values())
    return {
        "artifacts_dir": str(root),
        "state": state,
        "status": state.get("status", "unknown"),
        "scope": state.get("scope", ""),
        "provider": state.get("provider", ""),
        "adapter": state.get("adapter", ""),
        "config_snapshot": state.get("config_snapshot"),
        "roles": roles,
        "severity_counts": severity_counts,
        "finding_count": finding_count,
        "blocker_count": len(blockers),
        "blockers": blockers,
        "next_steps": next_steps,
    }


def render_status_text(report: dict[str, Any]) -> str:
    executed = len(report.get("roles", []))
    counts = report.get("severity_counts", {})
    severity_line = ", ".join(
        f"{severity.lower()}={counts.get(severity, 0)}"
        for severity in SEVERITY_ORDER
    )
    lines = [
        f"Status: {report.get('status', 'unknown')}",
        f"Provider: {report.get('provider', 'unknown')} ({report.get('adapter', 'unknown')})",
        f"Executed roles: {executed}",
        f"Findings: {report.get('finding_count', 0)} ({severity_line})",
        f"Blockers: {report.get('blocker_count', 0)}",
    ]
    scope = str(report.get("scope") or "").strip()
    if scope:
        lines.insert(1, f"Scope: {scope}")
    return "\n".join(lines)


def render_report_text(report: dict[str, Any]) -> str:
    lines = [
        render_status_text(report),
        "",
        "Roles:",
    ]
    for role in report.get("roles", []):
        lines.append(
            f"- {role['role']} ({role['model']}): {role['summary'] or 'No summary provided.'}",
        )
        for finding in role.get("findings", []):
            if not isinstance(finding, dict):
                continue
            severity = str(finding.get("severity") or "").upper() or "UNKNOWN"
            title = str(finding.get("title") or "").strip() or "Untitled finding"
            lines.append(f"  {severity}: {title}")

    blockers = report.get("blockers", [])
    if blockers:
        lines.extend(["", "Blockers:"])
        for blocker in blockers:
            lines.append(
                f"- {blocker['role']} [{blocker['severity']}]: {blocker['title']}",
            )

    next_steps = report.get("next_steps", [])
    if next_steps:
        lines.extend(["", "Next steps:"])
        for item in next_steps:
            lines.append(f"- {item['role']}: {item['text']}")

    return "\n".join(lines)
