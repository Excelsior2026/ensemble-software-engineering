# ESE Extensibility

ESE should remain the orchestration substrate, not the vertical application.

External packs target config-pack contract version `1`.

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

## Operating model

- Keep ESE releaseable with zero external packs installed.
- Treat packs as additive integrations, not core dependencies.
- Treat policy checks as additive governance layers, not hard-coded product logic in ESE core.
- Put domain tests in the domain repository, not in ESE core.
- Keep the pack contract stable so vertical repos can upgrade ESE without forking it.
