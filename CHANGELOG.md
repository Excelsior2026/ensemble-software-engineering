# Changelog

## [1.0.1] - 2026-03-23

### Added
- End-to-end CLI workflow tests covering task, rerun, export, and PR review flows.
- File-aware diff excerpt helpers shared by PR review and repository context generation.
- Disk-backed dashboard job persistence so job status survives dashboard restarts.

### Changed
- Centralized provider/runtime capability rules for wizard defaults, task builder behavior, doctor guidance, and adapter validation.
- Cached local runtime readiness checks within a pipeline run to avoid repeated Ollama probes and model scans.
- Improved local runtime docs, troubleshooting, and runtime summaries for built-in vs demo execution.

### Compatibility
- Config contract remains `version: 1`.
- No breaking CLI or config-schema changes; this is a backward-compatible patch release.

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
