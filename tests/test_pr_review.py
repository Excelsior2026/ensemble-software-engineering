from __future__ import annotations

import json

from ese.pr_review import build_pr_review_config, build_pull_request_review_context


def _fake_run_command(args: list[str], *, cwd: str) -> str:  # noqa: ARG001
    if args[:3] == ["git", "rev-parse", "--show-toplevel"]:
        return "/tmp/repo"
    if args[:3] == ["gh", "pr", "view"]:
        return json.dumps(
            {
                "number": 42,
                "title": "Harden billing retries",
                "body": "Adds retry protection and logging.",
                "baseRefName": "main",
                "headRefName": "billing-retries",
                "url": "https://github.com/example/repo/pull/42",
            },
        )
    if args[:4] == ["git", "rev-parse", "--verify", "--quiet"]:
        ref = args[-1]
        if ref in {"origin/main", "billing-retries"}:
            return "abc123"
        raise AssertionError(f"Unexpected ref lookup: {ref}")
    if args[:4] == ["git", "diff", "--stat", "--find-renames"]:
        return " 2 files changed, 10 insertions(+), 3 deletions(-)"
    if args[:4] == ["git", "diff", "--name-status", "--find-renames"]:
        return "M\tbilling.py\nA\ttests/test_billing.py"
    if args[:4] == ["git", "log", "--oneline", "--no-decorate"]:
        return "abc123 Add retry limit"
    if args[:4] == ["git", "diff", "--no-color", "--find-renames"]:
        return "diff --git a/billing.py b/billing.py\n" + ("+" * 200)
    raise AssertionError(f"Unexpected command: {args}")


def test_build_pull_request_review_context_uses_pr_metadata(monkeypatch) -> None:
    monkeypatch.setattr("ese.pr_review._run_command", _fake_run_command)

    context = build_pull_request_review_context(
        repo_path=".",
        pr="42",
        max_diff_chars=80,
    )

    assert context.repo_path == "/tmp/repo"
    assert context.base_ref == "origin/main"
    assert context.head_ref == "billing-retries"
    assert context.pr_number == 42
    assert context.patch_truncated is True
    assert context.included_patch_files == 1
    assert context.total_patch_files == 1
    assert "[diff truncated by ESE]" in context.patch


def test_build_pr_review_config_embeds_diff_context(monkeypatch) -> None:
    monkeypatch.setattr("ese.pr_review._run_command", _fake_run_command)

    context, cfg = build_pr_review_config(
        repo_path=".",
        pr="42",
        provider="openai",
        execution_mode="demo",
        artifacts_dir="artifacts/pr-42",
        focus="retry storms and idempotency",
        max_diff_chars=80,
    )

    assert context.review_title == "Harden billing retries"
    assert cfg["runtime"]["adapter"] == "dry-run"
    assert "release_manager" in cfg["roles"]
    assert "implementer" not in cfg["roles"]
    assert cfg["input"]["review_type"] == "pull_request"
    assert "Unified diff (truncated to 80 chars; included 1 of 1 file patches):" in cfg["input"]["prompt"]
    assert "retry storms and idempotency" in cfg["input"]["prompt"]
