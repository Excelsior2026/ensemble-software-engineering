from __future__ import annotations

from ese.report_exporters import (
    ReportExporterDefinition,
    discover_external_report_exporters,
    list_builtin_report_exporters,
    render_report_export,
)


class _FakeEntryPoint:
    def __init__(self, name: str, payload) -> None:  # noqa: ANN001
        self.name = name
        self._payload = payload

    def load(self):  # noqa: ANN201
        return self._payload


def test_list_builtin_report_exporters_includes_sarif_and_junit() -> None:
    exporters = list_builtin_report_exporters()

    assert {exporter.key for exporter in exporters} == {"junit", "sarif"}


def test_discover_external_report_exporters_loads_entry_points(monkeypatch) -> None:
    definition = ReportExporterDefinition(
        key="blocker-csv",
        title="Blocker CSV",
        summary="CSV export of blocker findings.",
        content_type="text/csv; charset=utf-8",
        default_filename="ese_blockers.csv",
        render=lambda report: "role,severity\narchitect,HIGH\n",
    )
    monkeypatch.setattr(
        "ese.report_exporters._report_exporter_entry_points",
        lambda: [_FakeEntryPoint("blocker_csv", definition)],
    )

    exporters, failures = discover_external_report_exporters()

    assert failures == []
    assert len(exporters) == 1
    assert exporters[0].key == "blocker-csv"


def test_discover_external_report_exporters_loads_loader_callables(monkeypatch) -> None:
    monkeypatch.setattr(
        "ese.report_exporters._report_exporter_entry_points",
        lambda: [
            _FakeEntryPoint(
                "blocker_csv",
                lambda: ReportExporterDefinition(
                    key="blocker-csv",
                    title="Blocker CSV",
                    summary="CSV export of blocker findings.",
                    content_type="text/csv; charset=utf-8",
                    default_filename="ese_blockers.csv",
                    render=lambda report: "role,severity\narchitect,HIGH\n",
                ),
            )
        ],
    )

    exporters, failures = discover_external_report_exporters()

    assert failures == []
    assert len(exporters) == 1
    assert exporters[0].key == "blocker-csv"


def test_render_report_export_supports_external_formats(monkeypatch) -> None:
    monkeypatch.setattr(
        "ese.report_exporters.discover_external_report_exporters",
        lambda: (
            [
                ReportExporterDefinition(
                    key="blocker-csv",
                    title="Blocker CSV",
                    summary="CSV export of blocker findings.",
                    content_type="text/csv; charset=utf-8",
                    default_filename="ese_blockers.csv",
                    render=lambda report: "role,severity\narchitect,HIGH\n",
                )
            ],
            [],
        ),
    )

    body, content_type, filename = render_report_export({"blockers": []}, "blocker-csv")

    assert "architect,HIGH" in body
    assert content_type.startswith("text/csv")
    assert filename == "ese_blockers.csv"


def test_discover_external_report_exporters_rejects_unsupported_contract_version(monkeypatch) -> None:
    monkeypatch.setattr(
        "ese.report_exporters._report_exporter_entry_points",
        lambda: [
            _FakeEntryPoint(
                "blocker_csv",
                {
                    "key": "blocker-csv",
                    "title": "Blocker CSV",
                    "summary": "CSV export of blocker findings.",
                    "content_type": "text/csv; charset=utf-8",
                    "default_filename": "ese_blockers.csv",
                    "contract_version": 99,
                    "render": lambda report: "role,severity\narchitect,HIGH\n",
                },
            )
        ],
    )

    exporters, failures = discover_external_report_exporters()

    assert exporters == []
    assert len(failures) == 1
    assert "not supported" in failures[0].error
