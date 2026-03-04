# Pipeline State Contract

After `ese start`, ESE writes:
- `ese_summary.md` (human-readable summary)
- `pipeline_state.json` (machine-readable execution contract)

## Deterministic role ordering

Execution order is deterministic:
1. Built-in roles follow fixed order when present:
   - `architect`
   - `implementer`
   - `adversarial_reviewer`
   - `security_auditor`
   - `test_generator`
   - `performance_analyst`
2. Any custom roles run after built-ins in the order they appear in `roles`.

## Context chaining contract

Role context is explicit and stable:
- `architect` receives no upstream context.
- `implementer` receives `architect` output.
- All other roles receive `architect` + `implementer` outputs.

## `pipeline_state.json` schema

```json
{
  "mode": "ensemble",
  "provider": "openai",
  "adapter": "dry-run",
  "scope": "...",
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

Any breaking contract change requires config/version policy updates and release notes.
