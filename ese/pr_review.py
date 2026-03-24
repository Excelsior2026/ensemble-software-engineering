"""Pull request review helpers for ESE."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ese.config import validate_config, write_config
from ese.diff_context import build_file_aware_diff_excerpt
from ese.pipeline import run_pipeline
from ese.reports import collect_run_report
from ese.templates import build_task_config

DEFAULT_MAX_DIFF_CHARS = 16000


class PullRequestReviewError(ValueError):
    """Raised when pull request review context cannot be assembled."""


@dataclass(frozen=True)
class PullRequestReviewContext:
    repo_path: str
    base_ref: str
    head_ref: str
    review_title: str
    reviewer_focus: str
    diff_range: str
    diffstat: str
    name_status: str
    commits: str
    patch: str
    patch_truncated: bool
    included_patch_files: int
    total_patch_files: int
    max_diff_chars: int
    pr_url: str | None = None
    pr_number: int | None = None
    pr_body: str = ""


def _run_command(args: list[str], *, cwd: str) -> str:
    try:
        completed = subprocess.run(  # noqa: S603
            args,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as err:
        raise PullRequestReviewError(f"Required command not found: {args[0]}") from err
    except subprocess.CalledProcessError as err:
        stderr = (err.stderr or "").strip()
        stdout = (err.stdout or "").strip()
        detail = stderr or stdout or f"{args[0]} exited with status {err.returncode}"
        raise PullRequestReviewError(detail) from err

    return completed.stdout.strip()


def _git(repo_path: str, *args: str) -> str:
    return _run_command(["git", *args], cwd=repo_path)


def _gh(repo_path: str, *args: str) -> str:
    return _run_command(["gh", *args], cwd=repo_path)


def _resolve_repo_root(repo_path: str) -> str:
    raw = repo_path or "."
    root = _git(raw, "rev-parse", "--show-toplevel")
    return str(Path(root))


def _ref_exists(repo_path: str, ref_name: str) -> bool:
    try:
        _git(repo_path, "rev-parse", "--verify", "--quiet", ref_name)
    except PullRequestReviewError:
        return False
    return True


def _default_base_ref(repo_path: str) -> str:
    try:
        symbolic = _git(repo_path, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
        if symbolic:
            return symbolic
    except PullRequestReviewError:
        pass

    for candidate in ("origin/main", "origin/master", "main", "master"):
        if _ref_exists(repo_path, candidate):
            return candidate

    raise PullRequestReviewError("Could not determine a default base ref. Pass --base explicitly.")


def _prefer_remote_ref(repo_path: str, ref_name: str) -> str:
    clean = (ref_name or "").strip()
    if not clean:
        raise PullRequestReviewError("Base/head ref must be a non-empty string.")
    if _ref_exists(repo_path, clean):
        return clean
    remote_candidate = f"origin/{clean}"
    if _ref_exists(repo_path, remote_candidate):
        return remote_candidate
    return clean


def _prefer_base_ref(repo_path: str, ref_name: str) -> str:
    clean = (ref_name or "").strip()
    if not clean:
        raise PullRequestReviewError("Base ref must be a non-empty string.")
    remote_candidate = clean if clean.startswith("origin/") else f"origin/{clean}"
    if _ref_exists(repo_path, remote_candidate):
        return remote_candidate
    if _ref_exists(repo_path, clean):
        return clean
    return clean


def _load_pr_metadata(repo_path: str, pr: str) -> dict[str, Any]:
    raw = _gh(
        repo_path,
        "pr",
        "view",
        pr,
        "--json",
        "number,title,body,baseRefName,headRefName,url",
    )
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as err:
        raise PullRequestReviewError("gh pr view returned invalid JSON") from err
    if not isinstance(parsed, dict):
        raise PullRequestReviewError("gh pr view returned an unexpected payload")
    return parsed


def _truncate_patch(patch: str, limit: int) -> tuple[str, bool, int, int]:
    if limit <= 0:
        raise PullRequestReviewError("max_diff_chars must be > 0")
    excerpt = build_file_aware_diff_excerpt(
        patch,
        limit=limit,
        truncated_label="diff truncated by ESE",
    )
    return (
        excerpt.text,
        excerpt.truncated,
        excerpt.included_file_patches,
        excerpt.total_file_patches,
    )


def build_pull_request_review_context(
    *,
    repo_path: str = ".",
    pr: str | None = None,
    base: str | None = None,
    head: str | None = None,
    title: str | None = None,
    focus: str | None = None,
    max_diff_chars: int = DEFAULT_MAX_DIFF_CHARS,
) -> PullRequestReviewContext:
    repo_root = _resolve_repo_root(repo_path)
    pr_metadata: dict[str, Any] = {}
    if pr:
        pr_metadata = _load_pr_metadata(repo_root, pr)

    base_ref = base or str(pr_metadata.get("baseRefName") or "")
    head_ref = head or str(pr_metadata.get("headRefName") or "") or "HEAD"

    if not base_ref:
        base_ref = _default_base_ref(repo_root)
    else:
        base_ref = _prefer_base_ref(repo_root, base_ref)

    head_ref = _prefer_remote_ref(repo_root, head_ref) if head_ref != "HEAD" else "HEAD"
    diff_range = f"{base_ref}...{head_ref}"

    diffstat = _git(repo_root, "diff", "--stat", "--find-renames", diff_range)
    name_status = _git(repo_root, "diff", "--name-status", "--find-renames", diff_range)
    commits = _git(repo_root, "log", "--oneline", "--no-decorate", diff_range)
    patch_raw = _git(repo_root, "diff", "--no-color", "--find-renames", diff_range)
    patch, truncated, included_patch_files, total_patch_files = _truncate_patch(patch_raw, max_diff_chars)

    review_title = (title or str(pr_metadata.get("title") or "")).strip()
    if not review_title:
        review_title = f"Review changes from {head_ref} into {base_ref}"

    reviewer_focus = (focus or "").strip()
    pr_body = str(pr_metadata.get("body") or "").strip()

    pr_number_raw = pr_metadata.get("number")
    pr_number = pr_number_raw if isinstance(pr_number_raw, int) else None
    pr_url_raw = pr_metadata.get("url")
    pr_url = pr_url_raw.strip() if isinstance(pr_url_raw, str) and pr_url_raw.strip() else None

    return PullRequestReviewContext(
        repo_path=repo_root,
        base_ref=base_ref,
        head_ref=head_ref,
        review_title=review_title,
        reviewer_focus=reviewer_focus,
        diff_range=diff_range,
        diffstat=diffstat,
        name_status=name_status,
        commits=commits,
        patch=patch,
        patch_truncated=truncated,
        included_patch_files=included_patch_files,
        total_patch_files=total_patch_files,
        max_diff_chars=max_diff_chars,
        pr_url=pr_url,
        pr_number=pr_number,
        pr_body=pr_body,
    )


def _pr_scope_text(context: PullRequestReviewContext) -> str:
    base = f"Review pull request changes from {context.head_ref} into {context.base_ref}."
    focus = " Focus on correctness, security, missing tests, performance risk, and merge readiness."
    if context.reviewer_focus:
        focus += f" Pay extra attention to: {context.reviewer_focus}."
    return base + focus


def _pr_prompt_text(context: PullRequestReviewContext) -> str:
    lines = [
        "Review this pull request as a code review, not as an implementation task.",
        f"Repository: {context.repo_path}",
        f"Diff range: {context.diff_range}",
        f"Review title: {context.review_title}",
    ]
    if context.pr_number is not None:
        lines.append(f"PR number: {context.pr_number}")
    if context.pr_url:
        lines.append(f"PR URL: {context.pr_url}")
    if context.reviewer_focus:
        lines.extend(["", f"Reviewer focus: {context.reviewer_focus}"])
    if context.pr_body:
        lines.extend(["", "PR description:", context.pr_body])
    if context.commits:
        lines.extend(["", "Commits:", context.commits])
    if context.diffstat:
        lines.extend(["", "Diffstat:", context.diffstat])
    if context.name_status:
        lines.extend(["", "Changed files:", context.name_status])

    diff_header = "Unified diff:"
    if context.patch_truncated:
        diff_header = (
            f"Unified diff (truncated to {context.max_diff_chars} chars; "
            f"included {context.included_patch_files} of {context.total_patch_files} file patches):"
        )
    lines.extend(["", diff_header, context.patch or "(empty diff)"])
    return "\n".join(lines).strip()


def build_pr_review_config(
    *,
    repo_path: str = ".",
    pr: str | None = None,
    base: str | None = None,
    head: str | None = None,
    title: str | None = None,
    focus: str | None = None,
    max_diff_chars: int = DEFAULT_MAX_DIFF_CHARS,
    provider: str = "openai",
    execution_mode: str = "auto",
    artifacts_dir: str = "artifacts",
    model: str | None = None,
    api_key_env: str | None = None,
    runtime_adapter: str | None = None,
    provider_name: str | None = None,
    base_url: str | None = None,
) -> tuple[PullRequestReviewContext, dict[str, Any]]:
    context = build_pull_request_review_context(
        repo_path=repo_path,
        pr=pr,
        base=base,
        head=head,
        title=title,
        focus=focus,
        max_diff_chars=max_diff_chars,
    )
    cfg = build_task_config(
        scope=_pr_scope_text(context),
        template_key="pr-review",
        provider=provider,
        execution_mode=execution_mode,
        artifacts_dir=artifacts_dir,
        model=model,
        api_key_env=api_key_env,
        runtime_adapter=runtime_adapter,
        provider_name=provider_name,
        base_url=base_url,
    )
    input_cfg = dict(cfg.get("input") or {})
    input_cfg.update(
        {
            "prompt": _pr_prompt_text(context),
            "review_type": "pull_request",
            "repo_path": context.repo_path,
            "base_ref": context.base_ref,
            "head_ref": context.head_ref,
            "review_title": context.review_title,
        },
    )
    if context.pr_url:
        input_cfg["pr_url"] = context.pr_url
    if context.pr_number is not None:
        input_cfg["pr_number"] = context.pr_number
    cfg["input"] = input_cfg
    return context, validate_config(cfg, source="<pull-request>")


def render_pull_request_review_markdown(
    context: PullRequestReviewContext,
    report: dict[str, Any],
) -> str:
    lines = [
        "# Pull Request Review",
        "",
        f"Title: {context.review_title}",
        f"Repository: {context.repo_path}",
        f"Base: {context.base_ref}",
        f"Head: {context.head_ref}",
        f"Status: {report.get('status', 'unknown')}",
        f"Findings: {report.get('finding_count', 0)}",
        f"Blockers: {report.get('blocker_count', 0)}",
    ]
    if context.pr_url:
        lines.append(f"PR: {context.pr_url}")
    if context.reviewer_focus:
        lines.extend(["", f"Reviewer focus: {context.reviewer_focus}"])

    blockers = report.get("blockers", [])
    if blockers:
        lines.extend(["", "## Blockers", ""])
        for blocker in blockers:
            lines.append(
                f"- {blocker['role']} [{blocker['severity']}]: {blocker['title']}",
            )

    lines.extend(["", "## Role Summaries", ""])
    for role in report.get("roles", []):
        lines.append(f"### {role['role']}")
        lines.append("")
        lines.append(role.get("summary") or "No summary provided.")
        lines.append("")
        for finding in role.get("findings", []):
            if not isinstance(finding, dict):
                continue
            severity = str(finding.get("severity") or "").upper()
            title = str(finding.get("title") or "").strip()
            if not severity and not title:
                continue
            lines.append(f"- {severity}: {title}")
        if role.get("next_steps"):
            lines.append("")
            for step in role["next_steps"]:
                lines.append(f"- Next: {step}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def run_pr_review(
    *,
    repo_path: str = ".",
    pr: str | None = None,
    base: str | None = None,
    head: str | None = None,
    title: str | None = None,
    focus: str | None = None,
    max_diff_chars: int = DEFAULT_MAX_DIFF_CHARS,
    provider: str = "openai",
    execution_mode: str = "auto",
    artifacts_dir: str = "artifacts",
    model: str | None = None,
    api_key_env: str | None = None,
    runtime_adapter: str | None = None,
    provider_name: str | None = None,
    base_url: str | None = None,
    config_path: str | None = None,
) -> tuple[PullRequestReviewContext, dict[str, Any], str, str]:
    context, cfg = build_pr_review_config(
        repo_path=repo_path,
        pr=pr,
        base=base,
        head=head,
        title=title,
        focus=focus,
        max_diff_chars=max_diff_chars,
        provider=provider,
        execution_mode=execution_mode,
        artifacts_dir=artifacts_dir,
        model=model,
        api_key_env=api_key_env,
        runtime_adapter=runtime_adapter,
        provider_name=provider_name,
        base_url=base_url,
    )

    if config_path:
        write_config(config_path, cfg)

    summary_path = run_pipeline(cfg=cfg, artifacts_dir=artifacts_dir)
    report = collect_run_report(artifacts_dir)
    review_path = str(Path(artifacts_dir) / "pr_review.md")
    Path(review_path).write_text(
        render_pull_request_review_markdown(context, report),
        encoding="utf-8",
    )
    return context, cfg, summary_path, review_path
