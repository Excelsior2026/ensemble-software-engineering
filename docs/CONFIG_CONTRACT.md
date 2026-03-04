# ESE Config Contract (v1)

`ese.config.yaml` is schema-validated before doctor/pipeline execution.

## Top-level keys

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `version` | int | yes | Must be `1` for current contract. |
| `mode` | enum | yes | `ensemble` or `solo`. |
| `provider` | object | yes | Global provider/model defaults. |
| `roles` | map | yes | Role-specific overrides and metadata. |
| `constraints` | object | no | Ensemble separation checks (for doctor). |
| `output` | object | no | Artifact/output behavior flags. |
| `gating` | object | no | Pipeline failure gating preferences. |
| `runtime` | object | no | Adapter and runtime execution settings. |

## Provider object

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `name` | string | yes | Non-empty provider identifier (for example `openai`, `my-gateway`). |
| `model` | string | yes | Non-empty default model identifier. |
| `api_key_env` | string | no | Non-empty env var name used for auth token lookup. |
| `base_url` | string | no | Optional provider endpoint; required for `runtime.adapter=custom_api` unless supplied at `runtime.custom_api.base_url`. |

## Runtime object

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `adapter` | string | no | Built-ins: `dry-run`, `openai`, `custom_api`; or `module:function`. |
| `timeout_seconds` | float | no | Must be `> 0`. |
| `max_retries` | int | no | Must be `>= 0`. |
| `retry_backoff_seconds` | float | no | Must be `> 0`. |
| `max_output_tokens` | int | no | Must be `> 0` when set. |
| `openai.base_url` | string | no | Optional OpenAI endpoint override. |
| `custom_api.base_url` | string | no | Optional custom API endpoint override. |

## `custom_api` validation rules

When `runtime.adapter=custom_api`:
- `provider.name` must not be `openai`.
- `provider.api_key_env` is required.
- One of `provider.base_url` or `runtime.custom_api.base_url` is required.
- Role model references must match configured provider name and include a model id.

## Version and migration policy

- Current supported config version: `1`.
- Any other `version` value fails validation with a field-level error.
- Breaking config schema changes will increment `version` and require explicit migration.
- Migration process for future versions:
  1. Add a versioned migration document.
  2. Add compatibility tests for old/new versions.
  3. Provide upgrade examples and failure modes in release notes.

## Validation behavior

Validation is performed by `ese.config.validate_config` (Pydantic-backed).
Invalid configs fail fast with field-level errors surfaced by `ese doctor` and `ese start`.
