# ESE Extensibility

ESE should remain the orchestration substrate, not the vertical application.

Supported external surfaces all target contract version `1` in this build:

- `ese.config_packs`
- `ese.policy_checks`
- `ese.report_exporters`
- `ese.artifact_views`
- `ese.integrations`

Use `ese extensions` to inspect the supported surface names, entry point groups, and contract versions from the installed CLI.
Use `ese doctor --environment` to validate the installed extension environment before a run starts.

## Core boundary

The `ese` package owns:

- role sequencing and parallel execution
- runtime and provider abstraction
- run artifacts, reruns, and summaries
- reporting and dashboard views
- framework-oriented setup and templates

External application repositories should own:

- domain-specific role catalogs
- domain prompts and schemas
- domain ingestion, persistence, and UI
- pack-specific evaluation datasets

## Config packs

Packs are discovered through the Python entry point group `ese.config_packs`.

Each entry point should load to either:

- a single `ConfigPackDefinition`
- a mapping shaped like `ConfigPackDefinition`
- an iterable of either of the above

Each pack exposes:

- `key`
- `title`
- `summary`
- `preset`
- `goal_profile`
- `roles`

Each role exposes:

- `key`
- `responsibility`
- `prompt`
- optional `temperature`

## Pack SDK workflow

Use the built-in SDK commands when creating a new external pack repository:

```bash
ese pack init ../my-pack --key my-pack --preset strict
ese pack validate ../my-pack
ese pack test ../my-pack
```

The scaffold writes a manifest-driven project layout:

```text
my-pack/
  pyproject.toml
  README.md
  src/my_pack/
    __init__.py
    pack.py
    ese_pack.yaml
    prompts/
      analyst.md
      reviewer.md
```

`ese pack validate` checks the manifest, prompt assets, preset/goal compatibility, and role uniqueness.
`ese pack test` also generates a dry-run ESE config from the pack and validates it against the core config contract.

## Manifest shape

```yaml
contract_version: 1
key: release-ops
title: Release Operations
summary: External ESE pack for release readiness workflows.
preset: strict
goal_profile: high-quality
roles:
  - key: release_planner
    responsibility: Plan the release sequence, checkpoints, and handoff expectations.
    prompt_file: prompts/release_planner.md
    temperature: 0.2
```

## Packaging example

```toml
[project.entry-points."ese.config_packs"]
release_ops = "my_product_pack.pack:load_pack"
```

## Policy checks

External policy checks are discovered through the Python entry point group `ese.policy_checks`.

Each entry point should load to either:

- a `PolicyCheckDefinition`
- a mapping shaped like `PolicyCheckDefinition`
- a callable that accepts `PolicyCheckContext`

Each policy check exposes:

- `key`
- `title`
- `summary`
- `check`
- optional `contract_version` (defaults to `1`)

Each policy result exposes:

- `severity`: `error` or `warning`
- `message`
- optional `hint`

Packaging example:

```toml
[project.entry-points."ese.policy_checks"]
release_safety = "my_policy_plugin.policy:load_policy"
```

Example check:

```python
from ese.policy_checks import POLICY_ERROR, PolicyCheckDefinition


def load_policy():
    return PolicyCheckDefinition(
        key="release-safety",
        title="Release Safety",
        summary="Require a release-focused role for rollout-sensitive scopes.",
        check=lambda context: [
            {
                "severity": POLICY_ERROR,
                "message": "Release-sensitive scope requires a release-focused role.",
                "hint": "Add a release role before running.",
            }
        ],
    )
```

## Report exporters

External report exporters are discovered through the Python entry point group `ese.report_exporters`.

Each entry point should load to either:

- a `ReportExporterDefinition`
- a mapping shaped like `ReportExporterDefinition`
- a callable that accepts the collected run report and returns a string payload

Each exporter exposes:

- `key`
- `title`
- `summary`
- `content_type`
- `default_filename`
- `render`
- optional `contract_version` (defaults to `1`)

Packaging example:

```toml
[project.entry-points."ese.report_exporters"]
blocker_csv = "my_reporting_plugin.exporters:load_exporter"
```

## Artifact views

External artifact views are discovered through the Python entry point group `ese.artifact_views`.

Each entry point should load to either:

- an `ArtifactViewDefinition`
- a mapping shaped like `ArtifactViewDefinition`
- a callable that accepts the collected run report and returns a string or mapping payload

Each view exposes:

- `key`
- `title`
- `summary`
- `format`
- `render`
- optional `available`
- optional `contract_version` (defaults to `1`)

Views are surfaced inside the normal `report["documents"]` payload using keys prefixed with `view:`.

Packaging example:

```toml
[project.entry-points."ese.artifact_views"]
release_brief = "my_reporting_plugin.views:load_view"
```

## Integrations

External integrations are discovered through the Python entry point group `ese.integrations`.

Each entry point should load to either:

- an `IntegrationDefinition`
- a mapping shaped like `IntegrationDefinition`
- a callable that accepts `IntegrationContext` and `IntegrationRequest`

Each integration exposes:

- `key`
- `title`
- `summary`
- `publish`
- optional `contract_version` (defaults to `1`)

Integrations are executed through:

```bash
ese publish --integration my-integration --artifacts-dir artifacts
```

Packaging example:

```toml
[project.entry-points."ese.integrations"]
filesystem_evidence = "my_integration_plugin.integration:load_integration"
```

## Starter repository model

Starter bundles are validated through a manifest named `ese_starter.yaml`. The manifest is the source of truth for a vertical bundle's pack, policy checks, exporters, views, and integrations, even though Python entry points are still required for runtime discovery.

Starter SDK workflow:

```bash
ese starter init ../my-vertical --key my-vertical
ese starter validate ../my-vertical
ese starter test ../my-vertical
```

Starter repositories should be treated as real installable packages that happen to use ESE as their foundation.

Recommended layout:

```text
starter/
  pyproject.toml
  README.md
  src/my_vertical/
    __init__.py
    pack.py
    policy.py
    exporters.py
    views.py
    integration.py
    ese_pack.yaml
    ese_starter.yaml
    prompts/
      analyst.md
      reviewer.md
```

Starter manifest shape:

```yaml
contract_version: 1
key: release-governance
title: Release Governance Starter
summary: Starter vertical repository for release-governance workflows built on top of ESE.
package_name: release_governance_starter
pack:
  key: release-governance
  manifest: ese_pack.yaml
policy_checks:
  - key: release-governance-safety
    module: release_governance_starter.policy:load_policy
    file: policy.py
report_exporters:
  - key: release-gate-csv
    module: release_governance_starter.exporters:load_exporter
    file: exporters.py
artifact_views:
  - key: go-live-brief
    module: release_governance_starter.views:load_view
    file: views.py
integrations:
  - key: release-governance-bundle
    module: release_governance_starter.integration:load_integration
    file: integration.py
```

Recommended operating model:

- keep ESE core generic
- put vertical prompts, packs, policies, and integrations in the starter or sibling app repo
- version each starter independently of ESE core
- prove portability in CI by installing the starter into a clean environment
- let future products fork or clone the starter, not the ESE core repo

## Operating model

- Keep ESE releaseable with zero external packs installed.
- Treat packs as additive integrations, not core dependencies.
- Treat policy checks as additive governance layers, not hard-coded product logic in ESE core.
- Treat exporters and artifact views as additive reporting layers, not new built-in output formats for every vertical.
- Treat integrations as additive evidence-delivery layers, not hard-coded SaaS destinations in ESE core.
- Put domain tests in the domain repository, not in ESE core.
- Keep the pack contract stable so vertical repos can upgrade ESE without forking it.
