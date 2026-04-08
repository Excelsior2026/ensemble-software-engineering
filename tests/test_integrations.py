from __future__ import annotations

from ese.integrations import (
    IntegrationContext,
    IntegrationDefinition,
    IntegrationRequest,
    discover_integrations,
    list_integrations,
    publish_run_evidence,
)


class _FakeEntryPoint:
    def __init__(self, name: str, payload) -> None:  # noqa: ANN001
        self.name = name
        self._payload = payload

    def load(self):  # noqa: ANN201
        return self._payload


def _context() -> IntegrationContext:
    return IntegrationContext(
        artifacts_dir="/tmp/artifacts",
        report={
            "run_id": "run-123",
            "status": "completed",
            "scope": "Review the release rollout",
        },
        pipeline_state={"status": "completed"},
    )


def test_list_integrations_returns_empty_when_none_are_installed(monkeypatch) -> None:
    monkeypatch.setattr("ese.integrations._integration_entry_points", lambda: [])

    assert list_integrations() == []


def test_discover_integrations_loads_entry_points(monkeypatch) -> None:
    definition = IntegrationDefinition(
        key="filesystem-evidence",
        title="Filesystem Evidence",
        summary="Write a portable evidence bundle to disk.",
        publish=lambda context, request: {"status": "published", "location": "/tmp/evidence"},
    )
    monkeypatch.setattr(
        "ese.integrations._integration_entry_points",
        lambda: [_FakeEntryPoint("filesystem_evidence", definition)],
    )

    integrations, failures = discover_integrations()

    assert failures == []
    assert len(integrations) == 1
    assert integrations[0].key == "filesystem-evidence"


def test_discover_integrations_loads_loader_callables(monkeypatch) -> None:
    monkeypatch.setattr(
        "ese.integrations._integration_entry_points",
        lambda: [
            _FakeEntryPoint(
                "filesystem_evidence",
                lambda: IntegrationDefinition(
                    key="filesystem-evidence",
                    title="Filesystem Evidence",
                    summary="Write a portable evidence bundle to disk.",
                    publish=lambda context, request: {"status": "published"},
                ),
            )
        ],
    )

    integrations, failures = discover_integrations()

    assert failures == []
    assert len(integrations) == 1
    assert integrations[0].key == "filesystem-evidence"


def test_discover_integrations_reports_invalid_contract_version(monkeypatch) -> None:
    monkeypatch.setattr(
        "ese.integrations._integration_entry_points",
        lambda: [
            _FakeEntryPoint(
                "filesystem_evidence",
                {
                    "key": "filesystem-evidence",
                    "title": "Filesystem Evidence",
                    "summary": "Write a portable evidence bundle to disk.",
                    "contract_version": 99,
                    "publish": lambda context, request: {"status": "published"},
                },
            )
        ],
    )

    integrations, failures = discover_integrations()

    assert integrations == []
    assert len(failures) == 1
    assert "not supported" in failures[0].error


def test_publish_run_evidence_normalizes_mapping_results(monkeypatch) -> None:
    monkeypatch.setattr(
        "ese.integrations.resolve_integration",
        lambda key: IntegrationDefinition(
            key="filesystem-evidence",
            title="Filesystem Evidence",
            summary="Write a portable evidence bundle to disk.",
            publish=lambda context, request: {
                "status": "published",
                "location": "/tmp/evidence",
                "message": f"published for {request.target}",
                "outputs": ["/tmp/evidence/manifest.json"],
            },
        ),
    )
    monkeypatch.setattr("ese.integrations.build_integration_context", lambda artifacts_dir: _context())

    result = publish_run_evidence(
        artifacts_dir="/tmp/artifacts",
        integration_key="filesystem-evidence",
        target="release-bundle",
        options={"copy_documents": True},
    )

    assert result.integration_key == "filesystem-evidence"
    assert result.status == "published"
    assert result.location == "/tmp/evidence"
    assert result.outputs == ("/tmp/evidence/manifest.json",)


def test_publish_run_evidence_accepts_callable_integrations(monkeypatch) -> None:
    def _publish(context: IntegrationContext, request: IntegrationRequest) -> str:
        assert context.report["status"] == "completed"
        assert request.dry_run is True
        return "/tmp/evidence"

    monkeypatch.setattr(
        "ese.integrations.resolve_integration",
        lambda key: IntegrationDefinition(
            key="filesystem-evidence",
            title="Filesystem Evidence",
            summary="Write a portable evidence bundle to disk.",
            publish=_publish,
        ),
    )
    monkeypatch.setattr("ese.integrations.build_integration_context", lambda artifacts_dir: _context())

    result = publish_run_evidence(
        artifacts_dir="/tmp/artifacts",
        integration_key="filesystem-evidence",
        dry_run=True,
    )

    assert result.status == "published"
    assert result.location == "/tmp/evidence"
