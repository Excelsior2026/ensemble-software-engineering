"""Discovery and execution helpers for external ESE integrations."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any, cast

from ese.extension_contracts import (
    maybe_invoke_entrypoint_loader,
    normalize_contract_version,
    normalize_non_empty,
    title_from_key,
)
from ese.reports import RunReportError, collect_run_report, load_pipeline_state

INTEGRATION_ENTRY_POINT_GROUP = "ese.integrations"
INTEGRATION_CONTRACT_VERSION = 1

PUBLISH_STATUS_DRY_RUN = "dry-run"
PUBLISH_STATUS_PUBLISHED = "published"
PUBLISH_STATUS_SKIPPED = "skipped"
_PUBLISH_STATUSES = {
    PUBLISH_STATUS_DRY_RUN,
    PUBLISH_STATUS_PUBLISHED,
    PUBLISH_STATUS_SKIPPED,
}


class IntegrationPublishError(ValueError):
    """Raised when an external integration cannot publish the current run."""


@dataclass(frozen=True)
class IntegrationContext:
    artifacts_dir: str
    report: Mapping[str, Any]
    pipeline_state: Mapping[str, Any]


@dataclass(frozen=True)
class IntegrationRequest:
    target: str | None = None
    options: Mapping[str, Any] = field(default_factory=dict)
    dry_run: bool = False


@dataclass(frozen=True)
class IntegrationPublishResult:
    integration_key: str
    status: str
    location: str | None = None
    message: str | None = None
    outputs: tuple[str, ...] = ()


@dataclass(frozen=True)
class IntegrationDefinition:
    key: str
    title: str
    summary: str
    publish: Callable[[IntegrationContext, IntegrationRequest], Any]
    contract_version: int = INTEGRATION_CONTRACT_VERSION


@dataclass(frozen=True)
class IntegrationLoadFailure:
    entry_point: str
    error: str


def _integration_entry_points() -> list[Any]:
    discovered = metadata.entry_points()
    if hasattr(discovered, "select"):
        return list(discovered.select(group=INTEGRATION_ENTRY_POINT_GROUP))
    return list(discovered.get(INTEGRATION_ENTRY_POINT_GROUP, []))


def _normalize_publish_status(value: Any) -> str:
    status = normalize_non_empty(value, label="integration publish status").lower()
    if status not in _PUBLISH_STATUSES:
        choices = ", ".join(sorted(_PUBLISH_STATUSES))
        raise ValueError(f"integration publish status must be one of: {choices}")
    return status


def _normalize_outputs(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        return (normalize_non_empty(value, label="integration output"),)
    if not isinstance(value, Iterable):
        raise ValueError("integration outputs must be an iterable of strings")
    outputs: list[str] = []
    for item in value:
        outputs.append(normalize_non_empty(item, label="integration output"))
    return tuple(outputs)


def _normalize_publish_result(value: Any, *, integration_key: str) -> IntegrationPublishResult:
    if isinstance(value, IntegrationPublishResult):
        result = value
    elif isinstance(value, Mapping):
        result = IntegrationPublishResult(
            integration_key=normalize_non_empty(
                value.get("integration_key") or integration_key,
                label="integration key",
            ),
            status=_normalize_publish_status(value.get("status", PUBLISH_STATUS_PUBLISHED)),
            location=str(value.get("location")).strip() if value.get("location") else None,
            message=str(value.get("message")).strip() if value.get("message") else None,
            outputs=_normalize_outputs(value.get("outputs")),
        )
    elif isinstance(value, str):
        publish_location = normalize_non_empty(value, label="integration publish location")
        result = IntegrationPublishResult(
            integration_key=integration_key,
            status=PUBLISH_STATUS_PUBLISHED,
            location=publish_location,
            outputs=(publish_location,),
        )
    else:
        raise TypeError(
            "Integrations must return IntegrationPublishResult, a mapping, or a publish-location string",
        )

    clean_location: str | None = str(result.location).strip() if result.location else None
    clean_message: str | None = str(result.message).strip() if result.message else None
    return IntegrationPublishResult(
        integration_key=normalize_non_empty(result.integration_key, label="integration key"),
        status=_normalize_publish_status(result.status),
        location=clean_location or None,
        message=clean_message or None,
        outputs=_normalize_outputs(result.outputs),
    )


def _normalize_integration_definition(value: Any, *, fallback_key: str) -> IntegrationDefinition:
    value = maybe_invoke_entrypoint_loader(value)
    if isinstance(value, IntegrationDefinition):
        definition = value
    elif isinstance(value, Mapping):
        raw_publish = value.get("publish")
        if not callable(raw_publish):
            raise TypeError("Integration definitions must provide a callable 'publish'")
        definition = IntegrationDefinition(
            key=normalize_non_empty(value.get("key") or fallback_key, label="integration key"),
            title=normalize_non_empty(value.get("title") or title_from_key(fallback_key), label="integration title"),
            summary=normalize_non_empty(value.get("summary"), label="integration summary"),
            publish=cast(Callable[[IntegrationContext, IntegrationRequest], Any], raw_publish),
            contract_version=normalize_contract_version(
                value.get("contract_version"),
                extension_name="integration",
                expected_version=INTEGRATION_CONTRACT_VERSION,
            ),
        )
    elif callable(value):
        definition = IntegrationDefinition(
            key=fallback_key,
            title=title_from_key(fallback_key),
            summary=((value.__doc__ or "").strip() or f"External integration '{fallback_key}'."),
            publish=cast(Callable[[IntegrationContext, IntegrationRequest], Any], value),
        )
    else:
        raise TypeError("Integrations must return IntegrationDefinition, a mapping, or a callable")

    if not callable(definition.publish):
        raise TypeError("Integration definitions must provide a callable 'publish'")

    return IntegrationDefinition(
        key=normalize_non_empty(definition.key, label="integration key"),
        title=normalize_non_empty(definition.title, label="integration title"),
        summary=normalize_non_empty(definition.summary, label="integration summary"),
        publish=definition.publish,
        contract_version=normalize_contract_version(
            definition.contract_version,
            extension_name="integration",
            expected_version=INTEGRATION_CONTRACT_VERSION,
        ),
    )


def discover_integrations() -> tuple[list[IntegrationDefinition], list[IntegrationLoadFailure]]:
    integrations_by_key: dict[str, IntegrationDefinition] = {}
    failures: list[IntegrationLoadFailure] = []
    for entry_point in _integration_entry_points():
        entry_name = normalize_non_empty(
            getattr(entry_point, "name", "integration"),
            label="entry point name",
        )
        fallback_key = entry_name.replace("_", "-").lower()
        try:
            loaded = entry_point.load()
            definition = _normalize_integration_definition(loaded, fallback_key=fallback_key)
        except Exception as err:  # noqa: BLE001
            failures.append(IntegrationLoadFailure(entry_point=entry_name, error=str(err)))
            continue
        integrations_by_key.setdefault(definition.key, definition)
    return [integrations_by_key[key] for key in sorted(integrations_by_key)], failures


def list_integrations() -> list[IntegrationDefinition]:
    integrations, _failures = discover_integrations()
    return integrations


def resolve_integration(key: str) -> IntegrationDefinition:
    clean_key = normalize_non_empty(key, label="integration key").lower()
    for integration in list_integrations():
        if integration.key == clean_key:
            return integration
    supported = ", ".join(integration.key for integration in list_integrations()) or "none"
    raise IntegrationPublishError(f"Unknown integration '{key}'. Installed integrations: {supported}")


def build_integration_context(artifacts_dir: str) -> IntegrationContext:
    try:
        report = collect_run_report(artifacts_dir)
        pipeline_state = load_pipeline_state(artifacts_dir)
    except RunReportError as err:
        raise IntegrationPublishError(str(err)) from err

    resolved_dir = str(Path(artifacts_dir).resolve())
    return IntegrationContext(
        artifacts_dir=resolved_dir,
        report=report,
        pipeline_state=pipeline_state,
    )


def publish_run_evidence(
    *,
    artifacts_dir: str,
    integration_key: str,
    target: str | None = None,
    options: Mapping[str, Any] | None = None,
    dry_run: bool = False,
) -> IntegrationPublishResult:
    integration = resolve_integration(integration_key)
    context = build_integration_context(artifacts_dir)
    request = IntegrationRequest(
        target=target.strip() if isinstance(target, str) and target.strip() else None,
        options=dict(options or {}),
        dry_run=dry_run,
    )
    try:
        raw_result = integration.publish(context, request)
    except Exception as err:  # noqa: BLE001
        raise IntegrationPublishError(f"Integration '{integration.key}' failed: {err}") from err

    try:
        return _normalize_publish_result(raw_result, integration_key=integration.key)
    except (TypeError, ValueError) as err:
        raise IntegrationPublishError(
            f"Integration '{integration.key}' returned an invalid publish result: {err}"
        ) from err
