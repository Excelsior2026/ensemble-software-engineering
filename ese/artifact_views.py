"""Discovery and rendering helpers for external ESE artifact views."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib import metadata
from typing import Any, cast

from ese.extension_contracts import (
    maybe_invoke_entrypoint_loader,
    normalize_contract_version,
    normalize_non_empty,
    title_from_key,
)

ARTIFACT_VIEW_ENTRY_POINT_GROUP = "ese.artifact_views"
ARTIFACT_VIEW_DOCUMENT_PREFIX = "view:"
ARTIFACT_VIEW_CONTRACT_VERSION = 1


@dataclass(frozen=True)
class ArtifactViewDefinition:
    key: str
    title: str
    summary: str
    format: str
    render: Callable[[dict[str, Any]], Any]
    available: Callable[[dict[str, Any]], bool] | None = None
    contract_version: int = ARTIFACT_VIEW_CONTRACT_VERSION


@dataclass(frozen=True)
class ArtifactViewLoadFailure:
    entry_point: str
    error: str


def _artifact_view_entry_points() -> list[Any]:
    discovered = metadata.entry_points()
    if hasattr(discovered, "select"):
        return list(discovered.select(group=ARTIFACT_VIEW_ENTRY_POINT_GROUP))
    return list(discovered.get(ARTIFACT_VIEW_ENTRY_POINT_GROUP, []))


def _document_key(key: str) -> str:
    clean_key = normalize_non_empty(key, label="artifact view key")
    return f"{ARTIFACT_VIEW_DOCUMENT_PREFIX}{clean_key}"


def _normalize_artifact_view_definition(value: Any, *, fallback_key: str) -> ArtifactViewDefinition:
    value = maybe_invoke_entrypoint_loader(value)
    if isinstance(value, ArtifactViewDefinition):
        definition = value
    elif isinstance(value, Mapping):
        raw_render = value.get("render")
        raw_available = value.get("available")
        if not callable(raw_render):
            raise TypeError("Artifact view definitions must provide a callable 'render'")
        if raw_available is not None and not callable(raw_available):
            raise TypeError("Artifact view definitions must provide a callable 'available' when set")
        definition = ArtifactViewDefinition(
            key=normalize_non_empty(value.get("key") or fallback_key, label="artifact view key"),
            title=normalize_non_empty(
                value.get("title") or title_from_key(fallback_key),
                label="artifact view title",
            ),
            summary=normalize_non_empty(value.get("summary"), label="artifact view summary"),
            format=normalize_non_empty(value.get("format", "md"), label="artifact view format"),
            render=cast(Callable[[dict[str, Any]], Any], raw_render),
            available=cast(Callable[[dict[str, Any]], bool] | None, raw_available),
            contract_version=normalize_contract_version(
                value.get("contract_version"),
                extension_name="artifact view",
                expected_version=ARTIFACT_VIEW_CONTRACT_VERSION,
            ),
        )
    elif callable(value):
        definition = ArtifactViewDefinition(
            key=fallback_key,
            title=title_from_key(fallback_key),
            summary=((value.__doc__ or "").strip() or f"External artifact view '{fallback_key}'."),
            format="md",
            render=cast(Callable[[dict[str, Any]], Any], value),
        )
    else:
        raise TypeError(
            "Artifact views must return ArtifactViewDefinition, a mapping, or a callable",
        )

    if not callable(definition.render):
        raise TypeError("Artifact view definitions must provide a callable 'render'")

    return ArtifactViewDefinition(
        key=normalize_non_empty(definition.key, label="artifact view key"),
        title=normalize_non_empty(definition.title, label="artifact view title"),
        summary=normalize_non_empty(definition.summary, label="artifact view summary"),
        format=normalize_non_empty(definition.format, label="artifact view format"),
        render=definition.render,
        available=definition.available,
        contract_version=normalize_contract_version(
            definition.contract_version,
            extension_name="artifact view",
            expected_version=ARTIFACT_VIEW_CONTRACT_VERSION,
        ),
    )


def discover_artifact_views() -> tuple[list[ArtifactViewDefinition], list[ArtifactViewLoadFailure]]:
    views_by_key: dict[str, ArtifactViewDefinition] = {}
    failures: list[ArtifactViewLoadFailure] = []
    for entry_point in _artifact_view_entry_points():
        entry_name = normalize_non_empty(
            getattr(entry_point, "name", "artifact-view"),
            label="entry point name",
        )
        fallback_key = entry_name.replace("_", "-").lower()
        try:
            loaded = entry_point.load()
            definition = _normalize_artifact_view_definition(loaded, fallback_key=fallback_key)
        except Exception as err:  # noqa: BLE001
            failures.append(
                ArtifactViewLoadFailure(
                    entry_point=entry_name,
                    error=str(err),
                )
            )
            continue
        views_by_key.setdefault(definition.key, definition)
    return [views_by_key[key] for key in sorted(views_by_key)], failures


def list_artifact_views() -> list[ArtifactViewDefinition]:
    views, _failures = discover_artifact_views()
    return views


def list_available_artifact_view_documents(report: dict[str, Any]) -> list[dict[str, str]]:
    documents: list[dict[str, str]] = []
    for definition in list_artifact_views():
        if definition.available is not None and not definition.available(report):
            continue
        documents.append(
            {
                "key": _document_key(definition.key),
                "title": definition.title,
                "path": _document_key(definition.key),
                "format": definition.format,
                "source": "external_view",
            }
        )
    return documents


def _resolve_artifact_view(document: str) -> ArtifactViewDefinition:
    clean_key = normalize_non_empty(document, label="artifact view document")
    raw_key = (
        clean_key[len(ARTIFACT_VIEW_DOCUMENT_PREFIX) :]
        if clean_key.startswith(ARTIFACT_VIEW_DOCUMENT_PREFIX)
        else clean_key
    )
    for definition in list_artifact_views():
        if definition.key == raw_key:
            return definition
    raise ValueError(f"Unknown artifact view '{document}'.")


def render_external_artifact_view(
    report: dict[str, Any],
    *,
    document: str,
    max_chars: int,
) -> dict[str, Any]:
    definition = _resolve_artifact_view(document)
    if definition.available is not None and not definition.available(report):
        raise ValueError(f"Artifact view '{definition.key}' is not available for this run.")

    rendered = definition.render(report)
    if isinstance(rendered, str):
        title = definition.title
        doc_format = definition.format
        content = rendered
    elif isinstance(rendered, Mapping):
        title = normalize_non_empty(
            rendered.get("title") or definition.title,
            label="artifact view title",
        )
        doc_format = normalize_non_empty(
            rendered.get("format") or definition.format,
            label="artifact view format",
        )
        content = normalize_non_empty(rendered.get("content"), label="artifact view content")
    else:
        raise ValueError(f"Artifact view '{definition.key}' must return a string or mapping payload")

    truncated = len(content) > max_chars
    document_key = _document_key(definition.key)
    return {
        "kind": "document",
        "key": document_key,
        "title": title,
        "path": document_key,
        "format": doc_format,
        "content": content[:max_chars],
        "truncated": truncated,
        "source": "external_view",
    }
