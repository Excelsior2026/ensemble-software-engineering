# Release Governance Starter

Starter vertical repository for release-governance workflows built on top of ESE.

It contributes:

- a release-governance config pack
- a rollout-safety policy check
- a release-gate CSV exporter
- a go-live artifact view
- a release-evidence integration

## Install

```bash
pip install .
```

## Use

```bash
ese starter validate .
ese packs
ese policies
ese exporters
ese views
ese integrations
```

Generate a portable starter config:

```bash
ese task "Review the staged rollout plan for billing cutover" \
  --pack release-governance \
  --execution-mode demo \
  --artifacts-dir artifacts
```

Publish release evidence:

```bash
ese publish \
  --integration release-governance-bundle \
  --artifacts-dir artifacts \
  --target ./release-evidence
```

Export a gate review CSV:

```bash
ese export \
  --artifacts-dir artifacts \
  --format release-gate-csv \
  --output-path ./release-evidence/release_gates.csv
```
