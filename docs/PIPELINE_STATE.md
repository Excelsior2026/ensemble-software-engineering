# Pipeline State Contract

After `ese start`, ESE writes:
- `ese_summary.md` (human-readable summary)
- `pipeline_state.json` (machine-readable execution contract)
- `ese_config.snapshot.yaml` (normalized config used for the run)

## Deterministic role ordering

Execution order is deterministic:
1. Built-in roles follow fixed order when present:
   - `architect`
   - `implementer`
   - `adversarial_reviewer`
   - `security_auditor`
   - `test_generator`
   - `performance_analyst`
   - `documentation_writer`
   - `devops_sre`
   - `database_engineer`
   - `release_manager`
2. Any custom roles run after built-ins in the order they appear in `roles`.

## Context chaining contract

Role context is explicit and stable:
- `architect` receives no upstream context.
- `implementer` receives `architect` output.
- All other roles receive `architect` + `implementer` outputs.

## Role artifact contract

- When `output.enforce_json=true` (default), role artifacts use the `.json` extension.
- Each role artifact must be a JSON object with at least:
  - `summary` (string)
  - `findings` (list)
  - `artifacts` (list of strings)
  - `next_steps` (list of strings)
- Each finding must include:
  - `severity` (`LOW`, `MEDIUM`, `HIGH`, or `CRITICAL`)
  - `title` (string)
  - `details` (string)
- When `gating.fail_on_high=true`, the pipeline stops after the first role that reports a `HIGH` or `CRITICAL` finding.

## `pipeline_state.json` schema

```json
{
  "status": "completed",
  "mode": "ensemble",
  "provider": "openai",
  "adapter": "dry-run",
  "scope": "...",
  "config_snapshot": "artifacts/ese_config.snapshot.yaml",
  "role_models": {
    "architect": "openai:gpt-5",
    "implementer": "openai:gpt-5-mini"
  },
  "artifacts": {
    "architect": "artifacts/01_architect.md",
    "implementer": "artifacts/02_implementer.md"
  },
  "execution": [
    {
      "role": "architect",
      "model": "openai:gpt-5",
      "artifact": "artifacts/01_architect.md"
    }
  ]
}
```

## Stability guarantees

For contract version `1`:
- top-level keys shown above are stable,
- execution ordering rules are stable,
- role chaining behavior is stable.

On gated failures, `pipeline_state.json` also includes:
- `status: "failed"`
- `failure`: a human-readable reason for the stop condition

Any breaking contract change requires config/version policy updates and release notes.
