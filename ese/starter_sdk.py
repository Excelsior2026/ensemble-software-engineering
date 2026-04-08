"""Scaffolding and validation helpers for external ESE starter bundles."""

from __future__ import annotations

import importlib
import re
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ese.artifact_views import ARTIFACT_VIEW_CONTRACT_VERSION, ArtifactViewDefinition
from ese.extension_contracts import normalize_contract_version, normalize_non_empty
from ese.integrations import INTEGRATION_CONTRACT_VERSION, IntegrationDefinition
from ese.pack_sdk import (
    PACK_MANIFEST_NAME,
    PackProjectError,
    default_pack_title,
    describe_pack_project,
    smoke_test_pack_project,
)
from ese.policy_checks import POLICY_CHECK_CONTRACT_VERSION, PolicyCheckDefinition
from ese.report_exporters import (
    REPORT_EXPORTER_CONTRACT_VERSION,
    ReportExporterDefinition,
)

STARTER_MANIFEST_NAME = "ese_starter.yaml"
STARTER_CONTRACT_VERSION = 1
_SKIP_DISCOVERY_PARTS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".venv",
    "__pycache__",
    "node_modules",
    "venv",
}


class StarterProjectError(ValueError):
    """Raised when an external starter project is malformed."""


@dataclass(frozen=True)
class StarterExtensionManifestEntry:
    key: str
    module: str
    file: Path


@dataclass(frozen=True)
class StarterProject:
    manifest_path: Path
    contract_version: int
    key: str
    title: str
    summary: str
    package_name: str
    pack_manifest_path: Path
    policy_checks: tuple[StarterExtensionManifestEntry, ...]
    report_exporters: tuple[StarterExtensionManifestEntry, ...]
    artifact_views: tuple[StarterExtensionManifestEntry, ...]
    integrations: tuple[StarterExtensionManifestEntry, ...]


def _clean_key(value: str) -> str:
    collapsed = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    if not collapsed:
        raise StarterProjectError("Starter key must contain at least one ASCII letter or digit.")
    return collapsed


def default_starter_title(starter_key: str) -> str:
    return default_pack_title(_clean_key(starter_key))


def default_starter_package_name(starter_key: str) -> str:
    package = _clean_key(starter_key).replace("-", "_")
    if package[0].isdigit():
        package = f"ese_starter_{package}"
    if not package.endswith("_starter"):
        package = f"{package}_starter"
    return package


def _validate_package_name(value: str) -> str:
    clean_value = (value or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", clean_value):
        raise StarterProjectError(
            "Starter package names must start with a letter or underscore and contain only letters, digits, and underscores."
        )
    return clean_value


def default_starter_summary(title: str) -> str:
    clean_title = (title or "").strip() or "External"
    return f"Starter vertical repository for ESE {clean_title.lower()} workflows."


def _manifest_candidates(root: Path) -> list[Path]:
    return [
        path
        for path in sorted(root.rglob(STARTER_MANIFEST_NAME))
        if not any(part in _SKIP_DISCOVERY_PARTS for part in path.relative_to(root).parts)
    ]


def resolve_starter_manifest(path: str | Path | None = None) -> Path:
    candidate = Path(path or ".").expanduser()
    if not candidate.exists():
        raise StarterProjectError(f"Starter path does not exist: {candidate}")
    if candidate.is_file():
        return candidate.resolve()
    if not candidate.is_dir():
        raise StarterProjectError(f"Starter path must be a directory or manifest file: {candidate}")

    matches = _manifest_candidates(candidate)
    if not matches:
        raise StarterProjectError(
            f"No {STARTER_MANIFEST_NAME} manifest found under {candidate.resolve()}."
        )
    if len(matches) > 1:
        joined = ", ".join(str(item) for item in matches)
        raise StarterProjectError(
            f"Multiple {STARTER_MANIFEST_NAME} manifests found under {candidate.resolve()}: {joined}"
        )
    return matches[0].resolve()


def _read_manifest_yaml(manifest_path: Path) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except OSError as err:
        raise StarterProjectError(f"Could not read manifest {manifest_path}: {err}") from err
    except yaml.YAMLError as err:
        raise StarterProjectError(f"Manifest {manifest_path} is not valid YAML: {err}") from err
    if not isinstance(loaded, dict):
        raise StarterProjectError(f"Manifest {manifest_path} must be a mapping")
    return loaded


def _normalize_manifest_entry(
    value: Any,
    *,
    manifest_path: Path,
    label: str,
) -> StarterExtensionManifestEntry:
    if not isinstance(value, dict):
        raise StarterProjectError(f"{label} entries must be mappings")
    key = normalize_non_empty(value.get("key"), label=f"{label} key")
    module = normalize_non_empty(value.get("module"), label=f"{label} module")
    file_text = normalize_non_empty(value.get("file"), label=f"{label} file")
    file_path = (manifest_path.parent / file_text).resolve()
    if not file_path.exists():
        raise StarterProjectError(f"{label} file does not exist: {file_path}")
    return StarterExtensionManifestEntry(key=key, module=module, file=file_path)


def _normalize_manifest_entries(
    value: Any,
    *,
    manifest_path: Path,
    label: str,
) -> tuple[StarterExtensionManifestEntry, ...]:
    if value in (None, []):
        return ()
    if not isinstance(value, list):
        raise StarterProjectError(f"{label} must be a list")
    return tuple(
        _normalize_manifest_entry(item, manifest_path=manifest_path, label=label)
        for item in value
    )


def load_starter_project(path: str | Path | None = None) -> StarterProject:
    manifest_path = resolve_starter_manifest(path)
    manifest = _read_manifest_yaml(manifest_path)

    contract_version = normalize_contract_version(
        manifest.get("contract_version"),
        extension_name="starter bundle",
        expected_version=STARTER_CONTRACT_VERSION,
    )
    key = _clean_key(normalize_non_empty(manifest.get("key"), label="starter key"))
    title = normalize_non_empty(manifest.get("title"), label="starter title")
    summary = normalize_non_empty(manifest.get("summary"), label="starter summary")
    package_name = _validate_package_name(normalize_non_empty(manifest.get("package_name"), label="starter package_name"))

    pack_section = manifest.get("pack")
    if not isinstance(pack_section, dict):
        raise StarterProjectError("starter manifest must include a pack section")
    pack_manifest_file = normalize_non_empty(
        pack_section.get("manifest", PACK_MANIFEST_NAME),
        label="starter pack manifest",
    )
    pack_manifest_path = (manifest_path.parent / pack_manifest_file).resolve()
    try:
        describe_pack_project(pack_manifest_path)
    except PackProjectError as err:
        raise StarterProjectError(f"starter pack is invalid: {err}") from err

    return StarterProject(
        manifest_path=manifest_path,
        contract_version=contract_version,
        key=key,
        title=title,
        summary=summary,
        package_name=package_name,
        pack_manifest_path=pack_manifest_path,
        policy_checks=_normalize_manifest_entries(
            manifest.get("policy_checks"),
            manifest_path=manifest_path,
            label="policy_checks",
        ),
        report_exporters=_normalize_manifest_entries(
            manifest.get("report_exporters"),
            manifest_path=manifest_path,
            label="report_exporters",
        ),
        artifact_views=_normalize_manifest_entries(
            manifest.get("artifact_views"),
            manifest_path=manifest_path,
            label="artifact_views",
        ),
        integrations=_normalize_manifest_entries(
            manifest.get("integrations"),
            manifest_path=manifest_path,
            label="integrations",
        ),
    )


def describe_starter_project(path: str | Path | None = None) -> dict[str, Any]:
    project = load_starter_project(path)
    return {
        "manifest_path": str(project.manifest_path),
        "starter_key": project.key,
        "title": project.title,
        "summary": project.summary,
        "package_name": project.package_name,
        "contract_version": project.contract_version,
        "pack_manifest_path": str(project.pack_manifest_path),
        "policy_checks": [entry.key for entry in project.policy_checks],
        "report_exporters": [entry.key for entry in project.report_exporters],
        "artifact_views": [entry.key for entry in project.artifact_views],
        "integrations": [entry.key for entry in project.integrations],
    }


@contextmanager
def _temporary_sys_path(path: Path):
    entry = str(path)
    sys.path.insert(0, entry)
    importlib.invalidate_caches()
    try:
        yield
    finally:
        try:
            sys.path.remove(entry)
        except ValueError:
            pass


def _clear_package_modules(package_name: str) -> None:
    prefixes = {package_name, f"{package_name}."}
    for module_name in list(sys.modules):
        if any(module_name == prefix.rstrip(".") or module_name.startswith(prefix) for prefix in prefixes):
            sys.modules.pop(module_name, None)


def _load_entrypoint_reference(reference: str, *, label: str) -> Any:
    module_name, separator, attribute_name = reference.partition(":")
    if not module_name or not separator or not attribute_name:
        raise StarterProjectError(
            f"{label} module reference must use the form package.module:callable",
        )
    try:
        module = importlib.import_module(module_name)
    except Exception as err:  # noqa: BLE001
        raise StarterProjectError(f"Could not import {label} module '{module_name}': {err}") from err

    try:
        loader = getattr(module, attribute_name)
    except AttributeError as err:
        raise StarterProjectError(
            f"{label} module '{module_name}' does not define '{attribute_name}'",
        ) from err

    if not callable(loader):
        raise StarterProjectError(f"{label} loader '{reference}' must be callable")

    try:
        return loader()
    except Exception as err:  # noqa: BLE001
        raise StarterProjectError(f"{label} loader '{reference}' failed: {err}") from err


def _assert_extension_key(value: str, *, actual: str, label: str) -> None:
    if actual != value:
        raise StarterProjectError(
            f"{label} loader returned key '{actual}', expected '{value}'",
        )


def smoke_test_starter_project(
    path: str | Path | None = None,
    *,
    provider: str = "openai",
    model: str | None = None,
) -> dict[str, Any]:
    project = load_starter_project(path)
    pack_smoke = smoke_test_pack_project(
        project.pack_manifest_path,
        provider=provider,
        model=model,
    )
    source_root = project.manifest_path.parent.parent
    loaded: dict[str, list[str]] = {
        "policy_checks": [],
        "report_exporters": [],
        "artifact_views": [],
        "integrations": [],
    }

    with _temporary_sys_path(source_root):
        _clear_package_modules(project.package_name)
        for entry in project.policy_checks:
            value = _load_entrypoint_reference(entry.module, label=f"policy check '{entry.key}'")
            if not isinstance(value, PolicyCheckDefinition):
                raise StarterProjectError(
                    f"policy check '{entry.key}' must load to PolicyCheckDefinition",
                )
            if value.contract_version != POLICY_CHECK_CONTRACT_VERSION:
                raise StarterProjectError(
                    f"policy check '{entry.key}' contract version {value.contract_version} is not supported",
                )
            _assert_extension_key(entry.key, actual=value.key, label="policy check")
            loaded["policy_checks"].append(value.key)

        for entry in project.report_exporters:
            value = _load_entrypoint_reference(entry.module, label=f"report exporter '{entry.key}'")
            if not isinstance(value, ReportExporterDefinition):
                raise StarterProjectError(
                    f"report exporter '{entry.key}' must load to ReportExporterDefinition",
                )
            if value.contract_version != REPORT_EXPORTER_CONTRACT_VERSION:
                raise StarterProjectError(
                    f"report exporter '{entry.key}' contract version {value.contract_version} is not supported",
                )
            _assert_extension_key(entry.key, actual=value.key, label="report exporter")
            loaded["report_exporters"].append(value.key)

        for entry in project.artifact_views:
            value = _load_entrypoint_reference(entry.module, label=f"artifact view '{entry.key}'")
            if not isinstance(value, ArtifactViewDefinition):
                raise StarterProjectError(
                    f"artifact view '{entry.key}' must load to ArtifactViewDefinition",
                )
            if value.contract_version != ARTIFACT_VIEW_CONTRACT_VERSION:
                raise StarterProjectError(
                    f"artifact view '{entry.key}' contract version {value.contract_version} is not supported",
                )
            _assert_extension_key(entry.key, actual=value.key, label="artifact view")
            loaded["artifact_views"].append(value.key)

        for entry in project.integrations:
            value = _load_entrypoint_reference(entry.module, label=f"integration '{entry.key}'")
            if not isinstance(value, IntegrationDefinition):
                raise StarterProjectError(
                    f"integration '{entry.key}' must load to IntegrationDefinition",
                )
            if value.contract_version != INTEGRATION_CONTRACT_VERSION:
                raise StarterProjectError(
                    f"integration '{entry.key}' contract version {value.contract_version} is not supported",
                )
            _assert_extension_key(entry.key, actual=value.key, label="integration")
            loaded["integrations"].append(value.key)
        _clear_package_modules(project.package_name)

    report = describe_starter_project(project.manifest_path)
    report.update(
        {
            "provider": pack_smoke["provider"],
            "model": pack_smoke["model"],
            "pack_smoke": pack_smoke,
            "loaded": loaded,
        }
    )
    return report


def scaffold_starter_project(
    target_dir: str | Path,
    *,
    starter_key: str,
    title: str | None = None,
    summary: str | None = None,
    package_name: str | None = None,
    preset: str = "strict",
    goal_profile: str | None = None,
    force: bool = False,
) -> StarterProject:
    clean_key = _clean_key(starter_key)
    clean_title = (title or "").strip() or default_starter_title(clean_key)
    clean_summary = (summary or "").strip() or default_starter_summary(clean_title)
    clean_package_name = _validate_package_name(
        (package_name or "").strip() or default_starter_package_name(clean_key)
    )

    target = Path(target_dir).expanduser().resolve()
    if target.exists() and not target.is_dir():
        raise StarterProjectError(f"Target path is not a directory: {target}")
    if target.exists() and any(target.iterdir()) and not force:
        raise StarterProjectError(
            f"Target directory is not empty: {target}. Pass force=True to allow scaffolding into a populated directory."
        )

    package_dir = target / "src" / clean_package_name
    prompts_dir = package_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)

    goal = (goal_profile or "high-quality").strip() or "high-quality"
    reviewer_key = f"{clean_key.replace('-', '_')}_reviewer"
    analyst_key = f"{clean_key.replace('-', '_')}_analyst"
    pack_manifest = {
        "contract_version": 1,
        "key": clean_key,
        "title": clean_title,
        "summary": clean_summary,
        "preset": preset,
        "goal_profile": goal,
        "roles": [
            {
                "key": analyst_key,
                "responsibility": f"Analyze the scoped work from the {clean_title} perspective.",
                "prompt_file": "prompts/analyst.md",
                "temperature": 0.2,
            },
            {
                "key": reviewer_key,
                "responsibility": f"Challenge the {clean_title} work for risk, evidence gaps, and blockers.",
                "prompt_file": "prompts/reviewer.md",
                "temperature": 0.2,
            },
        ],
    }

    starter_manifest = {
        "contract_version": STARTER_CONTRACT_VERSION,
        "key": clean_key,
        "title": clean_title,
        "summary": clean_summary,
        "package_name": clean_package_name,
        "pack": {
            "key": clean_key,
            "manifest": PACK_MANIFEST_NAME,
        },
        "policy_checks": [
            {
                "key": f"{clean_key}-safety",
                "module": f"{clean_package_name}.policy:load_policy",
                "file": "policy.py",
            }
        ],
        "report_exporters": [
            {
                "key": f"{clean_key}-csv",
                "module": f"{clean_package_name}.exporters:load_exporter",
                "file": "exporters.py",
            }
        ],
        "artifact_views": [
            {
                "key": f"{clean_key}-brief",
                "module": f"{clean_package_name}.views:load_view",
                "file": "views.py",
            }
        ],
        "integrations": [
            {
                "key": f"{clean_key}-bundle",
                "module": f"{clean_package_name}.integration:load_integration",
                "file": "integration.py",
            }
        ],
    }

    pyproject_text = "\n".join(
        [
            "[build-system]",
            'requires = ["setuptools>=69", "wheel"]',
            'build-backend = "setuptools.build_meta"',
            "",
            "[project]",
            f'name = "{clean_key}-starter"',
            'version = "0.1.0"',
            f'description = "{clean_summary}"',
            'readme = "README.md"',
            'requires-python = ">=3.10"',
            'dependencies = ["ese-cli>=1.0.0"]',
            "",
            '[project.entry-points."ese.config_packs"]',
            f'{clean_key.replace("-", "_")} = "{clean_package_name}.pack:load_pack"',
            "",
            '[project.entry-points."ese.policy_checks"]',
            f'{clean_key.replace("-", "_")}_safety = "{clean_package_name}.policy:load_policy"',
            "",
            '[project.entry-points."ese.report_exporters"]',
            f'{clean_key.replace("-", "_")}_csv = "{clean_package_name}.exporters:load_exporter"',
            "",
            '[project.entry-points."ese.artifact_views"]',
            f'{clean_key.replace("-", "_")}_brief = "{clean_package_name}.views:load_view"',
            "",
            '[project.entry-points."ese.integrations"]',
            f'{clean_key.replace("-", "_")}_bundle = "{clean_package_name}.integration:load_integration"',
            "",
            "[tool.setuptools.packages.find]",
            'where = ["src"]',
            "",
            "[tool.setuptools.package-data]",
            f'"{clean_package_name}" = ["{PACK_MANIFEST_NAME}", "{STARTER_MANIFEST_NAME}", "prompts/*.md"]',
            "",
        ]
    )

    readme_text = "\n".join(
        [
            f"# {clean_title} Starter",
            "",
            clean_summary,
            "",
            "## Development",
            "",
            "```bash",
            "pip install -e .",
            "ese starter validate .",
            "ese pack validate .",
            "```",
            "",
        ]
    )

    files: dict[Path, str] = {
        target / "pyproject.toml": pyproject_text + "\n",
        target / "README.md": readme_text + "\n",
        package_dir / "__init__.py": '"""External ESE starter bundle."""\n',
        package_dir / "pack.py": "\n".join(
            [
                '"""Entry point for the external starter pack."""',
                "",
                "from pathlib import Path",
                "",
                "from ese.pack_sdk import load_pack_definition_from_manifest",
                "",
                "",
                "def load_pack():",
                '    """Return the ConfigPackDefinition exported by this starter."""',
                f'    return load_pack_definition_from_manifest(Path(__file__).with_name("{PACK_MANIFEST_NAME}"))',
                "",
            ]
        ),
        package_dir / "policy.py": "\n".join(
            [
                '"""Starter safety policy."""',
                "",
                "from ese.policy_checks import POLICY_ERROR, PolicyCheckDefinition",
                "",
                "",
                "def _check_scope(context):",
                "    if context.scope.strip():",
                "        return []",
                "    return [{\"severity\": POLICY_ERROR, \"message\": \"Starter scope must not be empty.\"}]",
                "",
                "",
                "def load_policy():",
                '    """Return the starter policy check."""',
                "    return PolicyCheckDefinition(",
                f'        key="{clean_key}-safety",',
                f'        title="{clean_title} Safety",',
                '        summary="Starter safety policy for scoped runs.",',
                "        check=_check_scope,",
                "    )",
                "",
            ]
        ),
        package_dir / "exporters.py": "\n".join(
            [
                '"""Starter report exporter."""',
                "",
                "from ese.report_exporters import ReportExporterDefinition",
                "",
                "",
                "def _render_csv(report: dict) -> str:",
                '    return "role,severity,title\\n" + "\\n".join(',
                '        f"{item.get(\'role\', \'\')},{item.get(\'severity\', \'\')},{item.get(\'title\', \'\')}"',
                '        for item in report.get("blockers", [])',
                "    ) + \"\\n\"",
                "",
                "",
                "def load_exporter():",
                '    """Return the starter CSV exporter."""',
                "    return ReportExporterDefinition(",
                f'        key="{clean_key}-csv",',
                f'        title="{clean_title} CSV",',
                '        summary="Starter CSV export of blocker findings.",',
                '        content_type="text/csv; charset=utf-8",',
                f'        default_filename="{clean_key}_blockers.csv",',
                "        render=_render_csv,",
                "    )",
                "",
            ]
        ),
        package_dir / "views.py": "\n".join(
            [
                '"""Starter artifact view."""',
                "",
                "from ese.artifact_views import ArtifactViewDefinition",
                "",
                "",
                "def _render_view(report: dict) -> str:",
                '    return "# Starter Brief\\n\\n" + str(report.get("scope") or "No scope recorded.") + "\\n"',
                "",
                "",
                "def load_view():",
                '    """Return the starter artifact view."""',
                "    return ArtifactViewDefinition(",
                f'        key="{clean_key}-brief",',
                f'        title="{clean_title} Brief",',
                '        summary="Starter brief view for dashboard consumption.",',
                '        format="md",',
                "        render=_render_view,",
                "    )",
                "",
            ]
        ),
        package_dir / "integration.py": "\n".join(
            [
                '"""Starter evidence integration."""',
                "",
                "import json",
                "from pathlib import Path",
                "",
                "from ese.integrations import (",
                "    INTEGRATION_CONTRACT_VERSION,",
                "    PUBLISH_STATUS_DRY_RUN,",
                "    PUBLISH_STATUS_PUBLISHED,",
                "    IntegrationDefinition,",
                "    IntegrationPublishResult,",
                ")",
                "",
                "",
                "def _publish(context, request):",
                '    target = Path(request.target or context.artifacts_dir).resolve() / "starter-evidence.json"',
                "    if request.dry_run:",
                "        return IntegrationPublishResult(",
                f'            integration_key="{clean_key}-bundle",',
                "            status=PUBLISH_STATUS_DRY_RUN,",
                "            location=str(target),",
                '            message="Previewed starter evidence bundle.",',
                "            outputs=(str(target),),",
                "        )",
                "    target.parent.mkdir(parents=True, exist_ok=True)",
                '    target.write_text(json.dumps({"scope": context.report.get("scope")}, indent=2) + "\\n", encoding="utf-8")',
                "    return IntegrationPublishResult(",
                f'        integration_key="{clean_key}-bundle",',
                "        status=PUBLISH_STATUS_PUBLISHED,",
                "        location=str(target),",
                '        message="Published starter evidence bundle.",',
                "        outputs=(str(target),),",
                "    )",
                "",
                "",
                "def load_integration():",
                '    """Return the starter integration."""',
                "    return IntegrationDefinition(",
                f'        key="{clean_key}-bundle",',
                f'        title="{clean_title} Bundle",',
                '        summary="Starter evidence publishing integration.",',
                "        publish=_publish,",
                "        contract_version=INTEGRATION_CONTRACT_VERSION,",
                "    )",
                "",
            ]
        ),
        package_dir / "prompts" / "analyst.md": (
            f"You are the primary analyst for the {clean_title} starter.\n\n"
            "Analyze the scoped work, list risks, and call out the highest-leverage next steps.\n"
        ),
        package_dir / "prompts" / "reviewer.md": (
            f"You are the adversarial reviewer for the {clean_title} starter.\n\n"
            "Challenge weak assumptions, missing evidence, and delivery blockers.\n"
        ),
        package_dir / PACK_MANIFEST_NAME: yaml.safe_dump(pack_manifest, sort_keys=False),
        package_dir / STARTER_MANIFEST_NAME: yaml.safe_dump(starter_manifest, sort_keys=False),
    }

    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    return load_starter_project(package_dir / STARTER_MANIFEST_NAME)
