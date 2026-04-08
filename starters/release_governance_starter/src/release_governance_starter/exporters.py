"""Exporters for the release-governance starter."""

from __future__ import annotations

import csv
import io

from ese.report_exporters import ReportExporterDefinition


def _render_release_gate_csv(report: dict) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["state", "role", "title", "details"])
    for blocker in report.get("blockers", []):
        writer.writerow(
            [
                report.get("evidence_state", ""),
                blocker.get("role", ""),
                blocker.get("title", ""),
                blocker.get("details", ""),
            ]
        )
    if not report.get("blockers"):
        writer.writerow(
            [
                report.get("evidence_state", ""),
                "release-governance",
                "No blockers recorded",
                "The run completed without high-severity blockers.",
            ]
        )
    return buffer.getvalue()


def load_exporter():
    """Return the release gate review CSV exporter."""
    return ReportExporterDefinition(
        key="release-gate-csv",
        title="Release Gate CSV",
        summary="CSV export of release blockers and current evidence state.",
        content_type="text/csv; charset=utf-8",
        default_filename="release_gates.csv",
        render=_render_release_gate_csv,
    )
