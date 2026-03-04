# Release Process

## Version bump

1. Update `pyproject.toml` project version.
2. Update `CHANGELOG.md` with release date, features, and compatibility notes.
3. Verify docs for config/pipeline contracts and troubleshooting are current.

## Pre-release verification

Run locally:

```bash
pip install -e . pytest ruff
ruff check ese tests
pytest -q
ese doctor --config ese.config.yaml
ese start --config ese.config.yaml --artifacts-dir artifacts
```

## Publish flow

1. Create and push a release tag (for example `v1.0.0`).
2. Publish a GitHub release from that tag.
3. GitHub release event triggers `.github/workflows/pypi-publish.yml`.
4. Workflow builds package and publishes to PyPI using `PYPI_API_TOKEN`.

## Post-release smoke checks

After publish:

```bash
python -m venv /tmp/ese-smoke
source /tmp/ese-smoke/bin/activate
pip install ese-cli==<version>
ese --help
ese roles
```

Confirm CLI loads and basic commands execute.
