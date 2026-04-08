"""Example external ESE integration that publishes release evidence to disk."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from ese.integrations import (
    INTEGRATION_CONTRACT_VERSION,
    PUBLISH_STATUS_DRY_RUN,
    PUBLISH_STATUS_PUBLISHED,
    IntegrationDefinition,
    IntegrationPublishResult,
)


def _resolve_target_directory(artifacts_dir: str, target: str | None) -> Path:
    if target:
        candidate = Path(target).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()
        return (Path(artifacts_dir) / candidate).resolve()
    return (Path(artifacts_dir) / "published-evidence").resolve()


def _manifest_payload(report: dict[str, Any], *, target_dir: Path, options: dict[str, Any]) -> dict[str, Any]:
    documents = [
        {
            "key": str(document.get("key") or ""),
            "title": str(document.get("title") or ""),
            "path": str(document.get("path") or ""),
            "source": str(document.get("source") or "artifact"),
        }
        for document in report.get("documents", [])
        if isinstance(document, dict)
    ]
    return {
        "run_id": str(report.get("run_id") or ""),
        "scope": str(report.get("scope") or ""),
        "status": str(report.get("status") or "unknown"),
        "evidence_state": str(report.get("evidence_state") or "draft"),
        "assurance_level": str(report.get("assurance_level") or "unknown"),
        "blocker_count": len(report.get("blockers", [])),
        "suggested_action_count": len(report.get("suggested_actions", [])),
        "target_directory": str(target_dir),
        "documents": documents,
        "publish_options": options,
    }


def _render_release_overview(report: dict[str, Any]) -> str:
    blockers = report.get("blockers", [])
    next_steps = report.get("next_steps", [])
    lines = [
        "# Release Evidence Overview",
        "",
        f"- Scope: {report.get('scope') or 'No scope recorded.'}",
        f"- Status: {report.get('status') or 'unknown'}",
        f"- Evidence state: {report.get('evidence_state') or 'draft'}",
        f"- Assurance: {report.get('assurance_level') or 'unknown'}",
        f"- Blockers: {len(blockers)}",
        f"- Suggested actions: {len(report.get('suggested_actions', []))}",
    ]
    if blockers:
        lines.extend(["", "## Blockers", ""])
        for blocker in blockers[:5]:
            lines.append(f"- {blocker.get('role')}: {blocker.get('title')}")
    if next_steps:
        lines.extend(["", "## Next Steps", ""])
        for step in next_steps[:5]:
            lines.append(f"- {step.get('role')}: {step.get('text')}")
    return "\n".join(lines) + "\n"


def _copy_documents(report: dict[str, Any], *, target_dir: Path, max_documents: int) -> list[str]:
    copied: list[str] = []
    documents_dir = target_dir / "documents"
    for document in report.get("documents", []):
        if not isinstance(document, dict):
            continue
        source_path = str(document.get("path") or "").strip()
        if not source_path or source_path.startswith("view:"):
            continue
        source = Path(source_path)
        if not source.is_file():
            continue
        documents_dir.mkdir(parents=True, exist_ok=True)
        destination = documents_dir / source.name
        shutil.copy2(source, destination)
        copied.append(str(destination))
        if len(copied) >= max_documents:
            break
    return copied


def _publish_release_evidence(context, request):
    report = dict(context.report)
    target_dir = _resolve_target_directory(context.artifacts_dir, request.target)
    options = dict(request.options)
    manifest_path = target_dir / "evidence_manifest.json"
    overview_path = target_dir / "release_overview.md"
    document_limit = int(options.get("max_documents", 3))
    copy_documents = bool(options.get("copy_documents", True))

    outputs = [str(manifest_path), str(overview_path)]
    if request.dry_run:
        if copy_documents:
            outputs.append(str(target_dir / "documents"))
        return IntegrationPublishResult(
            integration_key="filesystem-evidence",
            status=PUBLISH_STATUS_DRY_RUN,
            location=str(target_dir),
            message="Previewed filesystem evidence bundle without writing files.",
            outputs=tuple(outputs),
        )

    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            _manifest_payload(report, target_dir=target_dir, options=options),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    overview_path.write_text(_render_release_overview(report), encoding="utf-8")
    if copy_documents:
        outputs.extend(
            _copy_documents(report, target_dir=target_dir, max_documents=max(document_limit, 0))
        )

    return IntegrationPublishResult(
        integration_key="filesystem-evidence",
        status=PUBLISH_STATUS_PUBLISHED,
        location=str(target_dir),
        message="Published portable filesystem evidence bundle.",
        outputs=tuple(outputs),
    )


def _parse_github_target(target: str | None) -> tuple[str, str, str]:
    clean_target = str(target or "").strip()
    if "#" not in clean_target:
        raise ValueError("GitHub integration target must use owner/repo#issue_or_pr_number.")
    repo_text, issue_number = clean_target.rsplit("#", 1)
    owner, separator, repo = repo_text.partition("/")
    if not owner or not separator or not repo or not issue_number.strip().isdigit():
        raise ValueError("GitHub integration target must use owner/repo#issue_or_pr_number.")
    return owner.strip(), repo.strip(), issue_number.strip()


def _render_github_comment(report: dict[str, Any]) -> str:
    blockers = report.get("blockers", [])
    next_steps = report.get("suggested_actions", [])
    lines = [
        "## ESE Evidence Summary",
        "",
        f"- Run ID: {report.get('run_id') or 'unknown'}",
        f"- Scope: {report.get('scope') or 'No scope recorded.'}",
        f"- Status: {report.get('status') or 'unknown'}",
        f"- Evidence state: {report.get('evidence_state') or 'draft'}",
        f"- Assurance: {report.get('assurance_level') or 'unknown'}",
        f"- Blockers: {len(blockers)}",
    ]
    if blockers:
        lines.extend(["", "### Top blockers", ""])
        for blocker in blockers[:5]:
            lines.append(f"- {blocker.get('role')}: {blocker.get('title')}")
    if next_steps:
        lines.extend(["", "### Suggested next actions", ""])
        for action in next_steps[:5]:
            lines.append(f"- {action.get('text')}")
    return "\n".join(lines) + "\n"


def _publish_github_evidence(context, request):
    owner, repo, issue_number = _parse_github_target(request.target)
    report = dict(context.report)
    api_url = str(request.options.get("api_url") or "https://api.github.com").rstrip("/")
    comment_body = _render_github_comment(report)
    location = f"github://{owner}/{repo}#{issue_number}"

    if request.dry_run:
        return IntegrationPublishResult(
            integration_key="github-pr-evidence",
            status=PUBLISH_STATUS_DRY_RUN,
            location=location,
            message="Previewed GitHub evidence comment without publishing.",
            outputs=(location,),
        )

    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        raise ValueError("Set GITHUB_TOKEN before publishing GitHub evidence.")

    payload = json.dumps({"body": comment_body}).encode("utf-8")
    url = f"{api_url}/repos/{owner}/{repo}/issues/{issue_number}/comments"
    req = urllib_request.Request(
        url,
        data=payload,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "ese-github-evidence",
        },
        method="POST",
    )
    try:
        with urllib_request.urlopen(req) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except urllib_error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")
        raise ValueError(f"GitHub API returned HTTP {err.code}: {detail}") from err
    except urllib_error.URLError as err:
        raise ValueError(f"GitHub API request failed: {err.reason}") from err

    html_url = str(response_payload.get("html_url") or location).strip() or location
    return IntegrationPublishResult(
        integration_key="github-pr-evidence",
        status=PUBLISH_STATUS_PUBLISHED,
        location=html_url,
        message=f"Published evidence comment to {owner}/{repo}#{issue_number}.",
        outputs=(html_url,),
    )


def load_integration():
    """Return the example filesystem evidence integration."""
    return IntegrationDefinition(
        key="filesystem-evidence",
        title="Filesystem Evidence",
        summary="Publish a portable evidence bundle to a target directory on disk.",
        publish=_publish_release_evidence,
        contract_version=INTEGRATION_CONTRACT_VERSION,
    )


def load_github_integration():
    """Return the example GitHub evidence-comment integration."""
    return IntegrationDefinition(
        key="github-pr-evidence",
        title="GitHub PR Evidence",
        summary="Publish the run summary as a GitHub issue or pull-request comment.",
        publish=_publish_github_evidence,
        contract_version=INTEGRATION_CONTRACT_VERSION,
    )
