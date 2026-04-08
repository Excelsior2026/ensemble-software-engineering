# Release Integration Plugin

Example external ESE integration plugin that publishes a portable evidence bundle to disk.

## Install

```bash
pip install ./examples/release_integration_plugin
```

## Inspect

```bash
ese integrations
```

## Publish

```bash
ese publish \
  --integration filesystem-evidence \
  --artifacts-dir artifacts \
  --target ./published-evidence \
  --options '{"copy_documents": true, "max_documents": 3}'
```

The plugin writes:

- `evidence_manifest.json`
- `release_overview.md`
- optional copied source documents under `documents/`

It also exposes a dry-run GitHub publisher:

```bash
ese publish \
  --integration github-pr-evidence \
  --artifacts-dir artifacts \
  --target owner/repo#42 \
  --dry-run
```
