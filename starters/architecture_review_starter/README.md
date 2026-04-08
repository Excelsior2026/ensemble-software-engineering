# Architecture Review Starter

Starter vertical repository for architecture-review and migration-decision workflows built on top of ESE.

It contributes:

- an architecture-review config pack
- an architecture-scope policy check
- a decision brief artifact view
- a risk-register CSV exporter
- an architecture decision integration

## Install

```bash
pip install .
```

## Use

```bash
ese starter validate .
ese packs
ese policies
ese views
ese exporters
ese integrations
```

Run an architecture review workflow:

```bash
ese task "Review the service-boundary changes for the billing migration" \
  --pack architecture-review \
  --execution-mode demo \
  --artifacts-dir artifacts
```

Export a risk register and publish a portable decision packet:

```bash
ese export \
  --artifacts-dir artifacts \
  --format architecture-risk-csv \
  --output-path ./architecture-evidence/architecture_risks.csv

ese publish \
  --integration architecture-decision-bundle \
  --artifacts-dir artifacts \
  --target ./architecture-evidence
```
