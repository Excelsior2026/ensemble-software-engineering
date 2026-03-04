# ESE Milestone: 1.0.0

This document defines what must be true before releasing `ese-cli` version `1.0.0`.

## Definition of 1.0.0

`1.0.0` means the CLI is stable for production use with a documented, tested, and supported contract for:

- role orchestration behavior,
- configuration schema and versioning,
- model adapter integration,
- CI and release quality gates.

## Exit checklist

All items must be complete:

- [x] Real adapter support shipped and documented.
  - [x] Built-in provider adapter implemented (`openai`).
  - [x] `custom_api` runtime path validated (custom provider/base URL/model).
  - [x] Adapter error handling covers retries, timeouts, and actionable messages.
- [x] Pipeline chaining behavior is complete.
  - [x] Architect output is passed to Implementer.
  - [x] Implementer output is passed to reviewer/auditor/test/perf roles.
  - [x] Artifacts + machine state include enough context for deterministic audit.
- [x] Config contract is frozen.
  - [x] Schema validation enforced for `load_config`.
  - [x] `version` behavior documented and tested.
  - [x] Migration strategy defined for future breaking config changes.
- [x] Test suite provides release confidence.
  - [x] Unit tests for config, doctor, and role resolution.
  - [x] Pipeline tests for role order/chaining/artifacts.
  - [x] CLI smoke tests for `init`, `roles`, `doctor`, and `run`.
- [x] CI enforces quality in pull requests.
  - [x] Lint + tests run in CI.
  - [x] `ese doctor` and `ese run` run in CI with reproducible config.
  - [x] Artifacts are uploaded for failed/successful debugging.
- [x] Documentation and examples are complete.
  - [x] Role catalog and role selection behavior documented.
  - [x] Provider/model presets + `custom_api` setup documented.
  - [x] Troubleshooting section for auth/config/adapter failures.
- [ ] Release process is repeatable.
  - [x] Version bump + changelog process documented.
  - [ ] GitHub release -> PyPI publish flow validated end to end.

## PR plan (mapped work)

Use this sequence; each PR should be independently reviewable and mergeable.

### PR-1: Provider adapter foundation

- Scope:
  - Introduce production adapter interface implementation for provider calls.
  - Add built-in `openai` adapter while preserving `dry-run`.
- Deliverables:
  - New adapter module(s) with request/response/error normalization.
  - Runtime config hooks for adapter selection and provider settings.
  - Basic retry/timeout support and clear failure messages.
- Acceptance:
  - `ese run` completes with real adapter when credentials are set.
  - `ese run` fails gracefully with actionable errors when credentials are missing.

### PR-2: Custom API adapter path hardening

- Scope:
  - Make `custom_api` route production-ready.
- Deliverables:
  - Support custom provider name, base URL, model ID, and auth env var.
  - Validate required fields and emit targeted errors.
- Acceptance:
  - `ese init` generated `custom_api` config executes through pipeline path.
  - Docs include a full custom API example.

### PR-3: Pipeline chaining and artifact contract

- Scope:
  - Finalize explicit artifact/context flow across roles.
- Deliverables:
  - Standard per-role prompt/context builder.
  - Expanded `pipeline_state.json` contract documentation.
  - Deterministic role execution ordering rules.
- Acceptance:
  - Integration tests verify architect->implementer->reviewer chaining.
  - Summary/state artifacts consistently match execution.

### PR-4: Config contract + migration policy

- Scope:
  - Lock config schema behavior for 1.0.
- Deliverables:
  - Explicit schema docs and examples for every top-level key.
  - Version policy and migration guidance (for `version > 1` or legacy input).
  - Validation tests for malformed/missing data.
- Acceptance:
  - Invalid configs fail with precise field errors.
  - Version mismatch behavior is documented and tested.

### PR-5: Test suite expansion

- Scope:
  - Add release-quality automated tests.
- Deliverables:
  - Unit tests for config/doctor/init wizard role-model resolution.
  - Pipeline integration tests with fixture configs and expected artifacts.
  - CLI smoke tests.
- Acceptance:
  - Test suite passes locally and in CI.
  - Core regressions in role selection/chaining are covered.

### PR-6: CI enforcement and artifact diagnostics

- Scope:
  - Upgrade GitHub Actions from basic execution to quality gates.
- Deliverables:
  - Add test job(s) and enforce failures on test/lint breakage.
  - Keep artifact upload on `always()` for debugging.
  - Document CI expectations for contributors.
- Acceptance:
  - Failing tests block merges.
  - CI provides downloadable artifacts and readable failure summaries.

### PR-7: Documentation completion

- Scope:
  - Make docs sufficient for first-time setup and operational troubleshooting.
- Deliverables:
  - Update README + workflow docs with role/provider examples.
  - Add troubleshooting guide for auth, schema, and adapter failures.
  - Add minimal “production quickstart” section.
- Acceptance:
  - New user can install, configure, run, and interpret outputs without source reading.

### PR-8: Release prep and `1.0.0` cut

- Scope:
  - Final pre-release hardening and publish.
- Deliverables:
  - Final version bump to `1.0.0`.
  - Changelog/release notes summarizing contract and breaking-change policy.
  - GitHub release creation and PyPI publish validation.
- Acceptance:
  - PyPI shows `ese-cli==1.0.0`.
  - Release notes link to tests/docs/CI guarantees.

## Suggested tracking labels

- `milestone:1.0.0`
- `area:adapters`
- `area:pipeline`
- `area:config`
- `area:testing`
- `area:ci`
- `area:docs`
- `release`
