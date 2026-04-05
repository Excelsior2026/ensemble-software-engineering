"""Example external ESE report exporter."""

from __future__ import annotations

import csv
import io

from ese.report_exporters import ReportExporterDefinition


def _render_blocker_csv(report: dict) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["role", "severity", "title", "details", "confidence"])
    for blocker in report.get("blockers", []):
        writer.writerow(
            [
                blocker.get("role", ""),
                blocker.get("severity", ""),
                blocker.get("title", ""),
                blocker.get("details", ""),
                blocker.get("confidence", ""),
            ]
        )
    return buffer.getvalue()


def load_exporter():
    """Return the example blocker CSV exporter."""
    return ReportExporterDefinition(
        key="blocker-csv",
        title="Blocker CSV",
        summary="CSV export of blocker findings for spreadsheets and ops tooling.",
        content_type="text/csv; charset=utf-8",
        default_filename="ese_blockers.csv",
        render=_render_blocker_csv,
    )
