from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from ese.integrations import IntegrationContext, IntegrationRequest


def _load_module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path("examples/release_integration_plugin/src").resolve()))
    return importlib.import_module("release_integration_plugin.integration")


def _context() -> IntegrationContext:
    return IntegrationContext(
        artifacts_dir="/tmp/artifacts",
        report={
            "run_id": "run-123",
            "scope": "Review the release rollout",
            "status": "completed",
            "evidence_state": "ready",
            "assurance_level": "standard",
            "blockers": [],
            "suggested_actions": [{"text": "Publish release notes."}],
        },
        pipeline_state={"status": "completed"},
    )


def test_github_integration_supports_dry_run(monkeypatch) -> None:
    module = _load_module(monkeypatch)
    integration = module.load_github_integration()

    result = integration.publish(
        _context(),
        IntegrationRequest(target="openai/ese#42", dry_run=True),
    )

    assert result.status == "dry-run"
    assert result.location == "github://openai/ese#42"
    assert result.outputs == ("github://openai/ese#42",)


def test_github_integration_posts_comment(monkeypatch) -> None:
    module = _load_module(monkeypatch)
    integration = module.load_github_integration()
    requests: list[Any] = []

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        @staticmethod
        def read() -> bytes:
            return json.dumps({"html_url": "https://github.com/openai/ese/pull/42#issuecomment-1"}).encode("utf-8")

    def _fake_urlopen(request):  # noqa: ANN001
        requests.append(request)
        return _FakeResponse()

    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(module.urllib_request, "urlopen", _fake_urlopen)

    result = integration.publish(
        _context(),
        IntegrationRequest(target="openai/ese#42"),
    )

    assert result.status == "published"
    assert result.location == "https://github.com/openai/ese/pull/42#issuecomment-1"
    assert len(requests) == 1
    request = requests[0]
    assert request.full_url == "https://api.github.com/repos/openai/ese/issues/42/comments"
    assert request.get_method() == "POST"
    assert request.headers["Authorization"] == "Bearer test-token"
    assert b'"body":' in request.data
