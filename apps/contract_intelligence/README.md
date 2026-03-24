# Contract Intelligence Pilot

This directory contains the starter scaffold for a domain-specific application
layer built on top of ESE.

The goal is to validate a reusable case-intelligence platform using a first
vertical pack: construction contract management, evaluation, and tracking.

## Current scope

The first slice is a `bid_review` workflow that turns a contract package into:

- document inventory
- contractor-side risk findings
- insurance anomalies
- funding and compliance findings
- decision summary
- obligations preview
- adversarial review challenges

## Local usage

Run the deterministic pilot over a project folder from the repo root:

```bash
python -m apps.contract_intelligence bid-review ./sample_project
```

Or send artifacts somewhere specific:

```bash
python -m apps.contract_intelligence bid-review ./sample_project --artifacts-dir ./tmp/bid_review
```

The runner currently works best with plain-text inputs such as `.md`, `.txt`,
`.json`, `.yaml`, and `.docx`. Binary PDFs are detected as files but are not yet
parsed for text.

## Folder map

- `domain/`: shared pilot models and enums
- `ingestion/`: early document typing and intake helpers
- `orchestration/`: role catalog, pipeline definition, and prompts
- `schemas/`: JSON schema contracts for stable artifacts
- `api/`: placeholder API surface for the future product shell
- `storage/`: placeholder persistence boundary
- `ui/`: placeholder UI boundary

## Design rule

This package is intentionally not part of the published `ese` distribution yet.
It is a starter layer for product incubation while `ese` remains the generic
execution engine.
