"""Discovery and rendering helpers for external ESE report exporters."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib import metadata
from typing import Any, cast

REPORT_EXPORTER_ENTRY_POINT_GROUP = "ese.report_exporters"


@dataclass(frozen=True)
class ReportExporterDefinition:
    key: str
    title: str
    summary: str
    content_type: str
    default_filename: str
    render: Callable[[dict[str, Any]], str]


@dataclass(frozen=True)
class ReportExporterLoadFailure:
    entry_point: str
    error: str


def _report_exporter_entry_points() -> list[Any]:
    discovered = metadata.entry_points()
    if hasattr(discovered, "select"):
        return list(discovered.select(group=REPORT_EXPORTER_ENTRY_POINT_GROUP))
    return list(discovered.get(REPORT_EXPORTER_ENTRY_POINT_GROUP, []))


def _normalize_non_empty(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _title_from_key(key: str) -> str:
    return " ".join(part.capitalize() for part in key.replace("_", "-").split("-"))


def _builtin_report_exporters() -> list[ReportExporterDefinition]:
    from ese.reports import render_junit, render_sarif

    return [
        ReportExporterDefinition(
            key="sarif",
            title="SARIF",
            summary="Static Analysis Results Interchange Format for code scanning ingestion.",
            content_type="application/sarif+json; charset=utf-8",
            default_filename="ese_report.sarif.json",
            render=render_sarif,
        ),
        ReportExporterDefinition(
            key="junit",
            title="JUnit",
            summary="JUnit XML for CI test-report style ingestion.",
            content_type="application/xml; charset=utf-8",
            default_filename="ese_report.junit.xml",
            render=render_junit,
        ),
    ]


def list_builtin_report_exporters() -> list[ReportExporterDefinition]:
    return _builtin_report_exporters()


def _normalize_report_exporter_definition(value: Any, *, fallback_key: str) -> ReportExporterDefinition:
    if isinstance(value, ReportExporterDefinition):
        definition = value
    elif isinstance(value, Mapping):
        raw_render = value.get("render")
        if not callable(raw_render):
            raise TypeError("Report exporter definitions must provide a callable 'render'")
        definition = ReportExporterDefinition(
            key=_normalize_non_empty(value.get("key") or fallback_key, label="report exporter key"),
            title=_normalize_non_empty(
                value.get("title") or _title_from_key(fallback_key),
                label="report exporter title",
            ),
            summary=_normalize_non_empty(value.get("summary"), label="report exporter summary"),
            content_type=_normalize_non_empty(value.get("content_type"), label="report exporter content_type"),
            default_filename=_normalize_non_empty(
                value.get("default_filename"),
                label="report exporter default_filename",
            ),
            render=cast(Callable[[dict[str, Any]], str], raw_render),
        )
    elif callable(value):
        definition = ReportExporterDefinition(
            key=fallback_key,
            title=_title_from_key(fallback_key),
            summary=((value.__doc__ or "").strip() or f"External report exporter '{fallback_key}'."),
            content_type="text/plain; charset=utf-8",
            default_filename=f"ese_report.{fallback_key}.txt",
            render=cast(Callable[[dict[str, Any]], str], value),
        )
    else:
        raise TypeError(
            "Report exporters must return ReportExporterDefinition, a mapping, or a callable",
        )

    if not callable(definition.render):
        raise TypeError("Report exporter definitions must provide a callable 'render'")

    return ReportExporterDefinition(
        key=_normalize_non_empty(definition.key, label="report exporter key"),
        title=_normalize_non_empty(definition.title, label="report exporter title"),
        summary=_normalize_non_empty(definition.summary, label="report exporter summary"),
        content_type=_normalize_non_empty(definition.content_type, label="report exporter content_type"),
        default_filename=_normalize_non_empty(
            definition.default_filename,
            label="report exporter default_filename",
        ),
        render=definition.render,
    )


def discover_external_report_exporters() -> tuple[list[ReportExporterDefinition], list[ReportExporterLoadFailure]]:
    exporters_by_key: dict[str, ReportExporterDefinition] = {}
    failures: list[ReportExporterLoadFailure] = []
    for entry_point in _report_exporter_entry_points():
        entry_name = _normalize_non_empty(
            getattr(entry_point, "name", "report-exporter"),
            label="entry point name",
        )
        fallback_key = entry_name.replace("_", "-").lower()
        try:
            loaded = entry_point.load()
            definition = _normalize_report_exporter_definition(loaded, fallback_key=fallback_key)
        except Exception as err:  # noqa: BLE001
            failures.append(
                ReportExporterLoadFailure(
                    entry_point=entry_name,
                    error=str(err),
                )
            )
            continue
        exporters_by_key.setdefault(definition.key, definition)
    return [exporters_by_key[key] for key in sorted(exporters_by_key)], failures


def list_report_exporters() -> list[ReportExporterDefinition]:
    exporters = {exporter.key: exporter for exporter in _builtin_report_exporters()}
    external_exporters, _failures = discover_external_report_exporters()
    for exporter in external_exporters:
        exporters.setdefault(exporter.key, exporter)
    return [exporters[key] for key in sorted(exporters)]


def resolve_report_exporter(key: str) -> ReportExporterDefinition:
    clean_key = _normalize_non_empty(key, label="report exporter format").lower()
    for exporter in list_report_exporters():
        if exporter.key == clean_key:
            return exporter
    supported = ", ".join(exporter.key for exporter in list_report_exporters())
    raise ValueError(f"Unknown export format '{key}'. Supported formats: {supported}")


def render_report_export(report: dict[str, Any], key: str) -> tuple[str, str, str]:
    exporter = resolve_report_exporter(key)
    body = exporter.render(report)
    if not isinstance(body, str):
        raise ValueError(f"Report exporter '{exporter.key}' must return a string payload")
    return body, exporter.content_type, exporter.default_filename
