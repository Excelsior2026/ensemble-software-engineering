"""Helpers for loading and summarizing ESE run artifacts."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from ese.artifact_views import (
    ARTIFACT_VIEW_DOCUMENT_PREFIX,
    list_available_artifact_view_documents,
    render_external_artifact_view,
)
from ese.feedback import feedback_summary

SEVERITY_ORDER = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
DEFAULT_HISTORY_LIMIT = 8
MAX_ARTIFACT_VIEW_CHARS = 200_000
SARIF_LEVELS = {
    "CRITICAL": "error",
    "HIGH": "error",
    "MEDIUM": "warning",
    "LOW": "note",
}


class RunReportError(ValueError):
    """Raised when a run report cannot be loaded from artifacts."""


def _state_path(artifacts_dir: str | Path) -> Path:
    return Path(artifacts_dir) / "pipeline_state.json"


def _is_run_dir(path: Path) -> bool:
    return _state_path(path).is_file()


def _history_root(path: Path) -> Path:
    return path.parent if _is_run_dir(path) else path


def _timestamp_for(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")


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
    path = _state_path(artifacts_dir)
    return _read_json(path)


def load_role_report(artifact_path: str) -> dict[str, Any] | None:
    path = Path(artifact_path)
    if path.suffix.lower() != ".json":
        return None
    return _read_json(path)


def _document_entries(root: Path, state: dict[str, Any]) -> list[dict[str, str]]:
    documents: list[dict[str, str]] = []
    config_snapshot = str(state.get("config_snapshot") or "").strip()

    candidates = [
        ("summary", "Summary", root / "ese_summary.md"),
        ("pr_review", "PR Review", root / "pr_review.md"),
        ("code_suggestions_md", "Code Suggestions", root / "code_suggestions.md"),
        ("code_suggestions_json", "Code Suggestions JSON", root / "code_suggestions.json"),
        ("release_simulation", "Release Simulation", root / "release_simulation.json"),
    ]
    if config_snapshot:
        candidates.insert(1, ("config_snapshot", "Config Snapshot", Path(config_snapshot)))
    seen: set[str] = set()
    for key, title, path in candidates:
        if not str(path):
            continue
        resolved = path if path.is_absolute() else root / path
        if not resolved.exists():
            continue
        normalized = str(resolved)
        if normalized in seen:
            continue
        seen.add(normalized)
        documents.append(
            {
                "key": key,
                "title": title,
                "path": normalized,
                "format": resolved.suffix.lower().lstrip(".") or "txt",
            },
        )
    return documents


def _severity_rank(severity: str) -> int:
    try:
        return SEVERITY_ORDER.index(severity)
    except ValueError:
        return len(SEVERITY_ORDER)


def _finding_theme(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in str(value or ""))
    return " ".join(cleaned.split())


def _short_run_id(value: str) -> str:
    cleaned = str(value or "").strip()
    if len(cleaned) <= 12:
        return cleaned
    return cleaned[:12]


def _recurring_unknowns(roles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for role in roles:
        role_name = str(role.get("role") or "").strip()
        for unknown in role.get("unknowns", []):
            if not isinstance(unknown, str) or not unknown.strip():
                continue
            theme = _finding_theme(unknown)
            entry = grouped.setdefault(
                theme,
                {
                    "text": unknown.strip(),
                    "roles": [],
                    "count": 0,
                },
            )
            if role_name and role_name not in entry["roles"]:
                entry["roles"].append(role_name)
                entry["count"] += 1
    recurring = [item for item in grouped.values() if item["count"] >= 2]
    recurring.sort(key=lambda item: (-item["count"], item["text"]))
    return recurring


def _is_placeholder_suggestion(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    return lowered.startswith("replace dry-run with a real adapter")


def _consensus_summary(roles: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for role in roles:
        role_name = str(role.get("role") or "").strip()
        for finding in role.get("findings", []):
            if not isinstance(finding, dict):
                continue
            title = str(finding.get("title") or "").strip()
            severity = str(finding.get("severity") or "").upper().strip()
            if not role_name or not title:
                continue
            theme = _finding_theme(title)
            grouped.setdefault(theme, []).append(
                {
                    "role": role_name,
                    "severity": severity,
                    "title": title,
                    "details": str(finding.get("details") or "").strip(),
                },
            )

    agreements: list[dict[str, Any]] = []
    disagreements: list[dict[str, Any]] = []
    solo_blockers: list[dict[str, Any]] = []
    for theme, items in grouped.items():
        roles_for_theme = sorted({item["role"] for item in items})
        severities = sorted({item["severity"] for item in items}, key=_severity_rank)
        entry = {
            "theme": theme,
            "title": items[0]["title"],
            "roles": roles_for_theme,
            "count": len(roles_for_theme),
            "highest_severity": min(severities, key=_severity_rank) if severities else "LOW",
            "severities": severities,
        }
        if len(roles_for_theme) >= 2:
            agreements.append(entry)
            if len(severities) >= 2:
                disagreements.append(
                    {
                        **entry,
                        "note": "Multiple roles raised the same concern with different severities.",
                    },
                )
        elif entry["highest_severity"] in {"HIGH", "CRITICAL"}:
            solo_blockers.append(
                {
                    **entry,
                    "note": "Only one role flagged this as a blocker.",
                },
            )

    agreements.sort(key=lambda item: (-item["count"], _severity_rank(item["highest_severity"]), item["title"]))
    disagreements.sort(key=lambda item: (_severity_rank(item["highest_severity"]), item["title"]))
    solo_blockers.sort(key=lambda item: (_severity_rank(item["highest_severity"]), item["title"]))
    return {
        "agreements": agreements,
        "disagreements": disagreements,
        "solo_blockers": solo_blockers,
    }


def _candidate_run_dirs(path: Path) -> list[Path]:
    requested = path
    root = _history_root(requested)
    candidates: set[Path] = set()
    if _is_run_dir(requested):
        candidates.add(requested)
    if _is_run_dir(root):
        candidates.add(root)
    if root.exists():
        for child in root.iterdir():
            if child.is_dir() and _is_run_dir(child):
                candidates.add(child)
    return sorted(candidates, key=lambda item: _state_path(item).stat().st_mtime, reverse=True)


def _previous_run_dir(path: Path) -> Path | None:
    current = path.resolve()
    for candidate in _candidate_run_dirs(path):
        if candidate.resolve() == current:
            continue
        return candidate
    return None


def _blocker_key(blocker: dict[str, Any]) -> tuple[str, str]:
    return (
        str(blocker.get("role") or "").strip(),
        _finding_theme(str(blocker.get("title") or "").strip()),
    )


def _comparison_summary(path: Path, blockers: list[dict[str, Any]]) -> dict[str, Any]:
    previous = _previous_run_dir(path)
    if previous is None:
        return {
            "previous_artifacts_dir": None,
            "new_blockers": [],
            "resolved_blockers": [],
            "persistent_blockers": [],
        }

    previous_report = collect_run_report(str(previous), include_comparison=False)
    current_map = {_blocker_key(blocker): blocker for blocker in blockers}
    previous_blockers = previous_report.get("blockers", [])
    previous_map = {
        _blocker_key(blocker): blocker
        for blocker in previous_blockers
        if isinstance(blocker, dict)
    }

    new_keys = current_map.keys() - previous_map.keys()
    resolved_keys = previous_map.keys() - current_map.keys()
    persistent_keys = current_map.keys() & previous_map.keys()
    return {
        "previous_artifacts_dir": str(previous),
        "new_blockers": [current_map[key] for key in sorted(new_keys)],
        "resolved_blockers": [previous_map[key] for key in sorted(resolved_keys)],
        "persistent_blockers": [current_map[key] for key in sorted(persistent_keys)],
    }


def build_release_simulation(report: dict[str, Any]) -> dict[str, Any]:
    """Heuristically synthesize a structured rollout artifact from role outputs."""
    role_map = {
        str(role.get("role") or ""): role
        for role in report.get("roles", [])
        if isinstance(role, dict)
    }
    relevant_roles = {"devops_sre", "release_manager", "documentation_writer", "test_generator", "security_auditor"}
    enabled = bool(relevant_roles & set(role_map))
    blockers = [
        f"{blocker['role']}: {blocker['title']}"
        for blocker in report.get("blockers", [])
        if isinstance(blocker, dict)
    ]

    def _steps_for(*roles: str) -> list[str]:
        items: list[str] = []
        for role_name in roles:
            role = role_map.get(role_name) or {}
            for step in role.get("next_steps", []):
                if isinstance(step, str) and step.strip():
                    items.append(step.strip())
        return items

    rollout_stages = [
        {"stage": "preflight", "tasks": _steps_for("architect", "security_auditor", "test_generator")},
        {"stage": "rollout", "tasks": _steps_for("release_manager", "devops_sre")},
        {"stage": "verification", "tasks": _steps_for("performance_analyst", "test_generator", "release_manager")},
    ]
    rollback_criteria = [
        *blockers,
        *[step for step in _steps_for("release_manager", "devops_sre") if "rollback" in step.lower()],
    ]
    observability_checks = [
        step
        for step in _steps_for("devops_sre", "performance_analyst")
        if any(token in step.lower() for token in ("metric", "observe", "telemetry", "monitor", "alert", "latency"))
    ]
    required_sign_off = [role for role in ("release_manager", "devops_sre", "security_auditor") if role in role_map]
    assurance_level = str(report.get("assurance_level") or "standard").strip().lower()
    ready = (
        report.get("blocker_count", 0) == 0
        and report.get("status") == "completed"
        and assurance_level != "degraded"
    )
    summary = "Release-ready" if ready else "Hold release until blockers and rollout checks are resolved."
    if assurance_level == "degraded":
        summary = "Hold release: degraded assurance runs are not sufficient release evidence."

    return {
        "enabled": enabled,
        "status": report.get("status", "unknown"),
        "ready_for_release": ready,
        "rollout_stages": rollout_stages,
        "rollback_criteria": rollback_criteria,
        "observability_checks": observability_checks,
        "required_sign_off_roles": required_sign_off,
        "blocker_count": report.get("blocker_count", 0),
        "summary": summary,
    }


def _suggested_actions(report: dict[str, Any]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    blockers = report.get("blockers", [])
    if blockers:
        role = str(blockers[0].get("role") or "").strip()
        if role:
            actions.append(
                {
                    "kind": "rerun",
                    "role": role,
                    "text": f"Rerun from {role} after addressing the highest-severity blocker.",
                    "command": f"ese rerun {role} --artifacts-dir {report.get('artifacts_dir')}",
                },
            )
    comparison = report.get("comparison") or {}
    if comparison.get("new_blockers"):
        actions.append(
            {
                "kind": "comparison",
                "role": "comparison",
                "text": "Review blockers that are new relative to the previous run before merging.",
                "command": f"ese report --artifacts-dir {report.get('artifacts_dir')}",
            },
        )
    if report.get("next_steps"):
        actions.append(
            {
                "kind": "next-step",
                "role": str(report["next_steps"][0].get("role") or "run"),
                "text": str(report["next_steps"][0].get("text") or "").strip(),
                "command": f"ese status --artifacts-dir {report.get('artifacts_dir')}",
            },
        )
    if report.get("code_suggestions"):
        actions.append(
            {
                "kind": "code-suggestions",
                "role": "code_suggestions",
                "text": "Review the synthesized code suggestions before implementing the next round of edits.",
                "command": f"ese suggestions --artifacts-dir {report.get('artifacts_dir')}",
            },
        )
    return actions


def _code_suggestions(roles: list[dict[str, Any]]) -> list[dict[str, str]]:
    suggestions: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for role in roles:
        role_name = str(role.get("role") or "").strip()
        explicit_code_suggestions = role.get("code_suggestions", [])
        has_explicit_suggestions = False
        if isinstance(explicit_code_suggestions, list):
            for item in explicit_code_suggestions:
                if not isinstance(item, dict):
                    continue
                summary = str(item.get("summary") or item.get("suggestion") or "").strip()
                suggestion = str(item.get("suggestion") or "").strip()
                if _is_placeholder_suggestion(suggestion):
                    continue
                key = (
                    role_name,
                    str(item.get("path") or "").strip(),
                    _finding_theme(summary or suggestion),
                )
                if not role_name or not suggestion or key in seen:
                    continue
                seen.add(key)
                has_explicit_suggestions = True
                suggestions.append(
                    {
                        "role": role_name,
                        "source": "code_suggestion",
                        "severity": str(item.get("severity") or "").upper().strip(),
                        "title": summary or suggestion[:120],
                        "suggestion": suggestion,
                        "path": str(item.get("path") or "").strip(),
                        "kind": str(item.get("kind") or "edit").strip().lower(),
                        "snippet": str(item.get("snippet") or "").rstrip(),
                    },
                )

        if has_explicit_suggestions:
            continue

        for finding in role.get("findings", []):
            if not isinstance(finding, dict):
                continue
            title = str(finding.get("title") or "").strip()
            severity = str(finding.get("severity") or "LOW").upper().strip() or "LOW"
            suggestion = str(finding.get("details") or title).strip()
            key = (role_name, _finding_theme(title), _finding_theme(suggestion))
            if not role_name or not title or not suggestion or _is_placeholder_suggestion(suggestion) or key in seen:
                continue
            seen.add(key)
            suggestions.append(
                {
                    "role": role_name,
                    "source": "finding",
                    "severity": severity,
                    "title": title,
                    "suggestion": suggestion,
                    "path": "",
                    "kind": "edit",
                    "snippet": "",
                },
            )

        for step in role.get("next_steps", []):
            suggestion = str(step or "").strip()
            key = (role_name, "next-step", _finding_theme(suggestion))
            if not role_name or not suggestion or _is_placeholder_suggestion(suggestion) or key in seen:
                continue
            seen.add(key)
            suggestions.append(
                {
                    "role": role_name,
                    "source": "next_step",
                    "severity": "INFO",
                    "title": f"{role_name} next step",
                    "suggestion": suggestion,
                    "path": "",
                    "kind": "test" if "test" in suggestion.lower() else "edit",
                    "snippet": "",
                },
            )

    suggestions.sort(
        key=lambda item: (
            0 if item.get("path") else 1,
            0 if item["source"] == "finding" else 1,
            _severity_rank(item["severity"]) if item["severity"] in SEVERITY_ORDER else len(SEVERITY_ORDER),
            item["role"],
            item["title"],
        ),
    )
    return suggestions


def _group_code_suggestions(code_suggestions: list[dict[str, Any]]) -> dict[str, Any]:
    by_path: dict[str, list[dict[str, Any]]] = {}
    unscoped: list[dict[str, Any]] = []
    for item in code_suggestions:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if path:
            by_path.setdefault(path, []).append(item)
        else:
            unscoped.append(item)

    grouped_paths = [
        {"path": path, "items": by_path[path]}
        for path in sorted(by_path)
    ]
    return {
        "paths": [entry["path"] for entry in grouped_paths],
        "by_path": grouped_paths,
        "unscoped": unscoped,
        "file_targeted_count": sum(len(entry["items"]) for entry in grouped_paths),
        "unscoped_count": len(unscoped),
    }


def render_code_suggestions_markdown(report: dict[str, Any]) -> str:
    suggestions = [
        item
        for item in report.get("code_suggestions", [])
        if isinstance(item, dict)
    ]
    groups = _group_code_suggestions(suggestions)
    lines = [
        "# Code Suggestions",
        "",
        f"Artifacts directory: {report.get('artifacts_dir', '')}",
        f"Total suggestions: {len(suggestions)}",
        f"File-targeted suggestions: {groups.get('file_targeted_count', 0)}",
        f"Unscoped suggestions: {groups.get('unscoped_count', 0)}",
    ]
    for group in groups.get("by_path", []):
        path = str(group.get("path") or "").strip()
        if not path:
            continue
        lines.extend(["", f"## {path}"])
        for item in group.get("items", []):
            label = f"{item.get('role', 'role')} [{item.get('kind', 'edit')}]"
            severity = str(item.get("severity") or "").strip()
            if severity:
                label += f" {severity}"
            lines.append(f"- {label}: {item.get('suggestion', '')}")
            snippet = str(item.get("snippet") or "").strip()
            if snippet:
                lines.extend(["", "```text", snippet, "```"])
    unscoped = groups.get("unscoped", [])
    if unscoped:
        lines.extend(["", "## Unscoped"])
        for item in unscoped:
            label = f"{item.get('role', 'role')} [{item.get('kind', 'edit')}]"
            severity = str(item.get("severity") or "").strip()
            if severity:
                label += f" {severity}"
            lines.append(f"- {label}: {item.get('suggestion', '')}")
    return "\n".join(lines).strip() + "\n"


def render_code_suggestions_json(report: dict[str, Any]) -> str:
    suggestions = [
        item
        for item in report.get("code_suggestions", [])
        if isinstance(item, dict)
    ]
    groups = _group_code_suggestions(suggestions)
    payload = {
        "artifacts_dir": report.get("artifacts_dir"),
        "scope": report.get("scope"),
        "count": len(suggestions),
        "file_targeted_count": groups.get("file_targeted_count", 0),
        "unscoped_count": groups.get("unscoped_count", 0),
        "paths": groups.get("paths", []),
        "suggestions": suggestions,
    }
    return json.dumps(payload, indent=2) + "\n"


def collect_run_report(artifacts_dir: str, *, include_comparison: bool = True) -> dict[str, Any]:
    root = Path(artifacts_dir)
    state = load_pipeline_state(str(root))
    roles: list[dict[str, Any]] = []
    severity_counts = {severity: 0 for severity in SEVERITY_ORDER}
    blockers: list[dict[str, Any]] = []
    low_confidence_blockers: list[dict[str, Any]] = []
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
            "confidence": "",
            "assumptions": [],
            "unknowns": [],
            "evidence_basis": [],
            "report_format": artifact_path.suffix.lower().lstrip("."),
        }

        report = load_role_report(str(artifact_path))
        if report is None:
            entry["summary"] = artifact_path.read_text(encoding="utf-8")[:500].strip()
            roles.append(entry)
            continue

        entry["summary"] = str(report.get("summary") or "").strip()
        entry["confidence"] = str(report.get("confidence") or "").strip().upper()
        entry["findings"] = report.get("findings") if isinstance(report.get("findings"), list) else []
        entry["next_steps"] = [step for step in report.get("next_steps", []) if isinstance(step, str) and step.strip()]
        entry["artifacts"] = [step for step in report.get("artifacts", []) if isinstance(step, str) and step.strip()]
        entry["assumptions"] = [item for item in report.get("assumptions", []) if isinstance(item, str) and item.strip()]
        entry["unknowns"] = [item for item in report.get("unknowns", []) if isinstance(item, str) and item.strip()]
        entry["evidence_basis"] = [item for item in report.get("evidence_basis", []) if isinstance(item, str) and item.strip()]
        entry["code_suggestions"] = [
            item
            for item in report.get("code_suggestions", [])
            if isinstance(item, dict)
        ]

        for finding in entry["findings"]:
            if not isinstance(finding, dict):
                continue
            severity = str(finding.get("severity") or "").upper()
            if severity in severity_counts:
                severity_counts[severity] += 1
            if severity in {"HIGH", "CRITICAL"}:
                blocker = {
                    "role": role,
                    "severity": severity,
                    "title": str(finding.get("title") or "").strip(),
                    "details": str(finding.get("details") or "").strip(),
                    "confidence": entry["confidence"],
                }
                blockers.append(blocker)
                if entry["confidence"] == "LOW":
                    low_confidence_blockers.append(blocker)

        for step in entry["next_steps"]:
            next_steps.append({"role": role, "text": step})

        roles.append(entry)

    finding_count = sum(severity_counts.values())
    documents = _document_entries(root, state)
    state_path = _state_path(root)
    run_id = str(state.get("run_id") or "").strip()
    assurance_level = str(state.get("assurance_level") or "").strip() or "standard"
    recurring_unknowns = _recurring_unknowns(roles)
    report = {
        "artifacts_dir": str(root),
        "state": state,
        "run_id": run_id,
        "assurance_level": assurance_level,
        "parent_run_id": state.get("parent_run_id"),
        "state_contract_version": state.get("state_contract_version"),
        "report_contract_version": state.get("report_contract_version"),
        "status": state.get("status", "unknown"),
        "scope": state.get("scope", ""),
        "provider": state.get("provider", ""),
        "adapter": state.get("adapter", ""),
        "config_snapshot": state.get("config_snapshot"),
        "updated_at": _timestamp_for(state_path),
        "summary_path": str(root / "ese_summary.md"),
        "documents": documents,
        "failure": state.get("failure"),
        "failed_roles": state.get("failed_roles", []),
        "start_role": state.get("start_role"),
        "roles": roles,
        "severity_counts": severity_counts,
        "finding_count": finding_count,
        "blocker_count": len(blockers),
        "blockers": blockers,
        "low_confidence_blockers": low_confidence_blockers,
        "recurring_unknowns": recurring_unknowns,
        "next_steps": next_steps,
    }
    report["code_suggestions"] = _code_suggestions(roles)
    report["code_suggestion_groups"] = _group_code_suggestions(report["code_suggestions"])
    report["consensus"] = _consensus_summary(roles)
    report["feedback"] = feedback_summary(root)
    if include_comparison:
        report["comparison"] = _comparison_summary(root, blockers)
    else:
        report["comparison"] = {
            "previous_artifacts_dir": None,
            "new_blockers": [],
            "resolved_blockers": [],
            "persistent_blockers": [],
        }
    report["release_simulation"] = build_release_simulation(report)
    report["suggested_actions"] = _suggested_actions(report)
    assurance_note = (
        "Degraded assurance: solo or reduced-independence evidence should not be treated as equivalent to a full ensemble run."
        if assurance_level == "degraded"
        else "Standard assurance: ensemble independence checks passed for this run configuration."
    )
    report["assurance_note"] = assurance_note
    report["top_blocker"] = blockers[0] if blockers else None
    report["next_recommended_action"] = report["suggested_actions"][0] if report["suggested_actions"] else None
    report["documents"] = [
        *report["documents"],
        *list_available_artifact_view_documents(report),
    ]
    return report


def list_recent_runs(artifacts_dir: str, limit: int = DEFAULT_HISTORY_LIMIT) -> list[dict[str, Any]]:
    requested = Path(artifacts_dir)
    root = _history_root(requested)
    candidates: set[Path] = set()

    if _is_run_dir(requested):
        candidates.add(requested)
    if _is_run_dir(root):
        candidates.add(root)
    if root.exists():
        for child in root.iterdir():
            if child.is_dir() and _is_run_dir(child):
                candidates.add(child)

    runs: list[dict[str, Any]] = []
    for candidate in candidates:
        try:
            report = collect_run_report(str(candidate))
        except RunReportError:
            continue
        runs.append(
            {
                "artifacts_dir": str(candidate),
                "status": report.get("status", "unknown"),
                "scope": report.get("scope", ""),
                "provider": report.get("provider", ""),
                "adapter": report.get("adapter", ""),
                "updated_at": report.get("updated_at"),
                "finding_count": report.get("finding_count", 0),
                "blocker_count": report.get("blocker_count", 0),
                "role_count": len(report.get("roles", [])),
                "documents": report.get("documents", []),
                "failure": report.get("failure"),
            },
        )

    runs.sort(
        key=lambda item: _state_path(item["artifacts_dir"]).stat().st_mtime,
        reverse=True,
    )
    return runs[:limit]


def load_artifact_view(
    artifacts_dir: str,
    *,
    role: str | None = None,
    document: str | None = None,
    max_chars: int = MAX_ARTIFACT_VIEW_CHARS,
) -> dict[str, Any]:
    if bool(role) == bool(document):
        raise RunReportError("Select exactly one artifact target: role or document.")

    report = collect_run_report(artifacts_dir)
    if role:
        match = next((item for item in report.get("roles", []) if item.get("role") == role), None)
        if match is None:
            raise RunReportError(f"No artifact found for role '{role}'.")
        path = Path(str(match["artifact"]))
        content = path.read_text(encoding="utf-8")
        truncated = len(content) > max_chars
        return {
            "kind": "role",
            "key": role,
            "title": f"{role} Artifact",
            "path": str(path),
            "format": match.get("report_format", path.suffix.lower().lstrip(".")),
            "content": content[:max_chars],
            "truncated": truncated,
            "summary": match.get("summary", ""),
            "findings": match.get("findings", []),
            "next_steps": match.get("next_steps", []),
        }

    doc = next((item for item in report.get("documents", []) if item.get("key") == document), None)
    if doc is None and document and document.startswith(ARTIFACT_VIEW_DOCUMENT_PREFIX):
        try:
            return render_external_artifact_view(report, document=document, max_chars=max_chars)
        except ValueError as err:
            raise RunReportError(str(err)) from err
    if doc is None:
        raise RunReportError(f"No document found for key '{document}'.")
    if doc.get("source") == "external_view":
        try:
            return render_external_artifact_view(report, document=document or "", max_chars=max_chars)
        except ValueError as err:
            raise RunReportError(str(err)) from err
    path = Path(str(doc["path"]))
    content = path.read_text(encoding="utf-8")
    truncated = len(content) > max_chars
    return {
        "kind": "document",
        "key": document,
        "title": doc.get("title", document or "Artifact"),
        "path": str(path),
        "format": doc.get("format", path.suffix.lower().lstrip(".")),
        "content": content[:max_chars],
        "truncated": truncated,
    }


def render_status_text(report: dict[str, Any]) -> str:
    executed = len(report.get("roles", []))
    counts = report.get("severity_counts", {})
    severity_line = ", ".join(
        f"{severity.lower()}={counts.get(severity, 0)}"
        for severity in SEVERITY_ORDER
    )
    run_id = _short_run_id(str(report.get("run_id") or ""))
    lines = [
        f"Status: {report.get('status', 'unknown')}",
        f"Assurance: {report.get('assurance_level', 'standard')}",
        f"Provider: {report.get('provider', 'unknown')} ({report.get('adapter', 'unknown')})",
        f"Executed roles: {executed}",
        f"Findings: {report.get('finding_count', 0)} ({severity_line})",
        f"Blockers: {report.get('blocker_count', 0)}",
    ]
    if run_id:
        lines.insert(1, f"Run ID: {run_id}")
    scope = str(report.get("scope") or "").strip()
    if scope:
        lines.insert(2 if run_id else 1, f"Scope: {scope}")
    assurance_note = str(report.get("assurance_note") or "").strip()
    if assurance_note:
        lines.append(f"Assurance note: {assurance_note}")
    top_blocker = report.get("top_blocker") or {}
    if top_blocker:
        lines.append(f"Top blocker: {top_blocker.get('role')}: {top_blocker.get('title')}")
    next_action = report.get("next_recommended_action") or {}
    if next_action:
        lines.append(f"Next action: {next_action.get('text')}")
    comparison = report.get("comparison") or {}
    if comparison.get("previous_artifacts_dir"):
        lines.append(
            "Run delta: "
            f"+{len(comparison.get('new_blockers', []))} new blockers, "
            f"-{len(comparison.get('resolved_blockers', []))} resolved blockers",
        )
    return "\n".join(lines)


def render_report_text(report: dict[str, Any]) -> str:
    lines = [
        render_status_text(report),
        "",
    ]
    failure = str(report.get("failure") or "").strip()
    failed_roles = [role for role in report.get("failed_roles", []) if isinstance(role, str) and role.strip()]
    if failure:
        lines.extend(["Failure:", failure, ""])
    if failed_roles:
        lines.extend([f"Failed roles: {', '.join(failed_roles)}", ""])
    lines.append("Roles:")
    for role in report.get("roles", []):
        confidence = str(role.get("confidence") or "").strip()
        confidence_suffix = f" confidence={confidence}" if confidence else ""
        lines.append(
            f"- {role['role']} ({role['model']}){confidence_suffix}: {role['summary'] or 'No summary provided.'}",
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
            confidence = str(blocker.get("confidence") or "").strip()
            confidence_suffix = f" confidence={confidence}" if confidence else ""
            lines.append(
                f"- {blocker['role']} [{blocker['severity']}]{confidence_suffix}: {blocker['title']}",
            )

    low_confidence_blockers = report.get("low_confidence_blockers", [])
    if low_confidence_blockers:
        lines.extend(["", "Low-confidence blockers:"])
        for blocker in low_confidence_blockers:
            lines.append(
                f"- {blocker['role']} [{blocker['severity']}] confidence=LOW: {blocker['title']}",
            )

    recurring_unknowns = report.get("recurring_unknowns", [])
    if recurring_unknowns:
        lines.extend(["", "Recurring unknowns:"])
        for item in recurring_unknowns[:8]:
            lines.append(f"- {item['text']} ({item['count']} roles: {', '.join(item['roles'])})")

    next_steps = report.get("next_steps", [])
    if next_steps:
        lines.extend(["", "Next steps:"])
        for item in next_steps:
            lines.append(f"- {item['role']}: {item['text']}")

    code_suggestions = report.get("code_suggestions") or []
    if code_suggestions:
        lines.extend(["", "Code suggestions:"])
        for item in code_suggestions[:8]:
            label = f"{item['role']} [{item['source']}]"
            if item.get("severity") and item["severity"] != "INFO":
                label += f" {item['severity']}"
            path = str(item.get("path") or "").strip()
            kind = str(item.get("kind") or "").strip()
            if path:
                label += f" {path}"
            if kind:
                label += f" ({kind})"
            lines.append(f"- {label}: {item['suggestion']}")
            snippet = str(item.get("snippet") or "").strip()
            if snippet:
                lines.append(f"  snippet: {snippet}")

    consensus = report.get("consensus") or {}
    agreements = consensus.get("agreements") or []
    if agreements:
        lines.extend(["", "Consensus:"])
        for item in agreements[:5]:
            lines.append(
                f"- {item['title']} ({item['highest_severity']}) across {', '.join(item['roles'])}",
            )

    disagreements = consensus.get("disagreements") or []
    if disagreements:
        lines.extend(["", "Disagreements:"])
        for item in disagreements[:5]:
            lines.append(
                f"- {item['title']}: severities {', '.join(item['severities'])}",
            )

    comparison = report.get("comparison") or {}
    if comparison.get("previous_artifacts_dir"):
        lines.extend(["", "Compared with previous run:"])
        for key, label in (
            ("new_blockers", "New blockers"),
            ("resolved_blockers", "Resolved blockers"),
            ("persistent_blockers", "Persistent blockers"),
        ):
            lines.append(f"- {label}: {len(comparison.get(key, []))}")

    actions = report.get("suggested_actions") or []
    if actions:
        lines.extend(["", "Suggested actions:"])
        for item in actions:
            lines.append(f"- {item['text']} [{item['command']}]")

    return "\n".join(lines)


def render_sarif(report: dict[str, Any]) -> str:
    """Render findings as SARIF for CI and code scanning ingestion."""
    results: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    seen_rules: set[str] = set()
    for role in report.get("roles", []):
        role_name = str(role.get("role") or "").strip()
        artifact = str(role.get("artifact") or "").strip()
        for finding in role.get("findings", []):
            if not isinstance(finding, dict):
                continue
            title = str(finding.get("title") or "").strip() or "Untitled finding"
            severity = str(finding.get("severity") or "LOW").upper().strip()
            rule_id = f"{role_name}:{_finding_theme(title).replace(' ', '-') or 'finding'}"
            if rule_id not in seen_rules:
                rules.append(
                    {
                        "id": rule_id,
                        "shortDescription": {"text": title},
                        "properties": {"role": role_name, "severity": severity},
                    },
                )
                seen_rules.add(rule_id)
            result: dict[str, Any] = {
                "ruleId": rule_id,
                "level": SARIF_LEVELS.get(severity, "warning"),
                "message": {"text": str(finding.get("details") or title)},
                "properties": {"role": role_name, "severity": severity},
            }
            if artifact:
                result["locations"] = [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": artifact},
                        },
                    }
                ]
            results.append(result)

    payload = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "ESE",
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }
    return json.dumps(payload, indent=2) + "\n"


def render_junit(report: dict[str, Any]) -> str:
    """Render findings as a JUnit-style XML test report."""
    total_findings = 0
    failures = 0
    suite = ET.Element(
        "testsuite",
        name="ese",
        tests="0",
        failures="0",
        errors="0",
        skipped="0",
    )
    for role in report.get("roles", []):
        role_name = str(role.get("role") or "role")
        findings = [finding for finding in role.get("findings", []) if isinstance(finding, dict)]
        if not findings:
            case = ET.SubElement(suite, "testcase", classname="ese", name=role_name)
            ET.SubElement(case, "system-out").text = str(role.get("summary") or "No findings.")
            total_findings += 1
            continue
        for index, finding in enumerate(findings, start=1):
            total_findings += 1
            case = ET.SubElement(suite, "testcase", classname=role_name, name=f"{role_name}-{index}")
            severity = str(finding.get("severity") or "LOW").upper()
            title = str(finding.get("title") or "Untitled finding")
            details = str(finding.get("details") or "").strip()
            if severity in {"HIGH", "CRITICAL"}:
                failures += 1
                failure = ET.SubElement(case, "failure", message=title, type=severity)
                failure.text = details or title
            else:
                ET.SubElement(case, "system-out").text = f"{severity}: {title}\n{details}".strip()
    suite.set("tests", str(total_findings))
    suite.set("failures", str(failures))
    xml = ET.tostring(suite, encoding="unicode")
    return xml + "\n"
