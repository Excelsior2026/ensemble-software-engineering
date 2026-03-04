# Contributing

## Required pull request checks

PRs are expected to pass all quality gates in `.github/workflows/ese.yml`:

- `ruff check ese tests`
- `pytest -q`
- `ese doctor --config ese.config.yaml`
- `ese roles` smoke check
- `ese start --config ese.config.yaml --artifacts-dir artifacts`

Artifact upload is configured with `if: always()` for debugging failed or successful runs.

## Local pre-PR checklist

Run locally before opening a PR:

```bash
pip install -e . pytest ruff
ruff check ese tests
pytest -q
```

For CLI smoke:

```bash
ese doctor --config ese.config.yaml
ese start --config ese.config.yaml --artifacts-dir artifacts
```

## Contract-sensitive changes

When changing config schema, adapters, or pipeline state:

- Update docs in `docs/CONFIG_CONTRACT.md` and/or `docs/PIPELINE_STATE.md`.
- Add or update tests covering the new contract behavior.
- Note migration impact in changelog/release notes.
