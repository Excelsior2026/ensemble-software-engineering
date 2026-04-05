from __future__ import annotations

from ese.artifact_views import (
    ARTIFACT_VIEW_DOCUMENT_PREFIX,
    ArtifactViewDefinition,
    discover_artifact_views,
    list_available_artifact_view_documents,
    render_external_artifact_view,
)


class _FakeEntryPoint:
    def __init__(self, name: str, payload) -> None:  # noqa: ANN001
        self.name = name
        self._payload = payload

    def load(self):  # noqa: ANN201
        return self._payload


def _report() -> dict:
    return {
        "scope": "Review the release rollout",
        "status": "completed",
        "blockers": [{"role": "architect", "severity": "HIGH", "title": "Missing rollback path"}],
        "next_steps": [{"role": "architect", "text": "Add rollback notes."}],
    }


def test_discover_artifact_views_loads_entry_points(monkeypatch) -> None:
    definition = ArtifactViewDefinition(
        key="release-brief",
        title="Release Brief",
        summary="Generated release brief for dashboard viewing.",
        format="md",
        render=lambda report: "# Release Brief\n",
    )
    monkeypatch.setattr(
        "ese.artifact_views._artifact_view_entry_points",
        lambda: [_FakeEntryPoint("release_brief", definition)],
    )

    views, failures = discover_artifact_views()

    assert failures == []
    assert len(views) == 1
    assert views[0].key == "release-brief"


def test_list_available_artifact_view_documents_prefixes_keys(monkeypatch) -> None:
    monkeypatch.setattr(
        "ese.artifact_views.list_artifact_views",
        lambda: [
            ArtifactViewDefinition(
                key="release-brief",
                title="Release Brief",
                summary="Generated release brief for dashboard viewing.",
                format="md",
                render=lambda report: "# Release Brief\n",
            )
        ],
    )

    documents = list_available_artifact_view_documents(_report())

    assert documents == [
        {
            "key": f"{ARTIFACT_VIEW_DOCUMENT_PREFIX}release-brief",
            "title": "Release Brief",
            "path": f"{ARTIFACT_VIEW_DOCUMENT_PREFIX}release-brief",
            "format": "md",
            "source": "external_view",
        }
    ]


def test_render_external_artifact_view_returns_document_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "ese.artifact_views.list_artifact_views",
        lambda: [
            ArtifactViewDefinition(
                key="release-brief",
                title="Release Brief",
                summary="Generated release brief for dashboard viewing.",
                format="md",
                render=lambda report: "# Release Brief\n\n- Missing rollback path\n",
            )
        ],
    )

    view = render_external_artifact_view(
        _report(),
        document=f"{ARTIFACT_VIEW_DOCUMENT_PREFIX}release-brief",
        max_chars=200_000,
    )

    assert view["kind"] == "document"
    assert view["key"] == f"{ARTIFACT_VIEW_DOCUMENT_PREFIX}release-brief"
    assert "Missing rollback path" in view["content"]
