# Changelog

## [1.0.0] - 2026-03-04

### Added
- Built-in `custom_api` runtime adapter with provider/base URL/auth validation.
- Deterministic pipeline contract documentation (`docs/PIPELINE_STATE.md`).
- Config schema and migration policy documentation (`docs/CONFIG_CONTRACT.md`).
- Troubleshooting guide for auth/config/adapter failures (`docs/TROUBLESHOOTING.md`).
- Automated pytest suite covering config, doctor, adapters, pipeline, init wizard helpers, and CLI smoke paths.
- CI quality gates for lint/test/CLI checks plus always-on artifact upload.

### Changed
- `runtime.max_retries` now supports `0` retries consistently at runtime.
- Init wizard now supports executable `custom_api` adapter selection with required custom base URL.
- README expanded with production quickstart and contract links.

### Compatibility
- Config contract remains `version: 1`.
- Breaking config changes require explicit version increment and migration notes.
