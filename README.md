# Ensemble Software Engineering (ESE)

ESE is a lightweight CLI framework for AI-assisted software development using specialized model roles and explicit ensemble constraints.

## Core pipeline

```mermaid
flowchart TD
  A["Human Scope"] --> B["Architect"]
  B --> C["Implementer"]
  C --> D["Adversarial Reviewer"]
  C --> E["Security Auditor"]
  C --> F["Test Generator"]
  C --> G["Performance Analyst"]
  D --> H["Human Merge"]
  E --> H
  F --> H
  G --> H
```

## Installation

```bash
pip install ese-cli
```

## Production quickstart

1. Generate a config:

```bash
ese init --advanced
```

2. Validate configuration and ensemble constraints:

```bash
ese doctor --config ese.config.yaml
```

3. Execute the pipeline:

```bash
ese start --config ese.config.yaml --artifacts-dir artifacts
```

4. Review outputs:
- `artifacts/ese_summary.md`
- `artifacts/pipeline_state.json`

`ese run` remains available as a backward-compatible alias for `ese start`.

## Role catalog

Use `ese roles` to print the role catalog in the CLI.

- `architect`: System design, decomposition, and interface contracts.
- `implementer`: Code changes and refactors.
- `adversarial_reviewer`: Bug/risk hunting and regression checks.
- `security_auditor`: Threat modeling and vulnerability review.
- `test_generator`: Unit/integration/e2e test generation.
- `performance_analyst`: Latency, memory, and scalability analysis.
- `documentation_writer`: README, API docs, and migration notes.
- `devops_sre`: CI/CD, deploy safety, and observability.
- `database_engineer`: Schema/index/migration correctness.
- `release_manager`: Go/no-go risk assessment and rollout checks.

## Provider/model selection and adapters

Wizard provider presets: `openai`, `anthropic`, `google`, `xai`, `openrouter`, `huggingface`, `local`, `custom_api`.

Built-in runtime adapters:
- `dry-run`: deterministic placeholder artifacts, no API calls.
- `openai`: OpenAI Responses API adapter with retry/timeout handling.
- `custom_api`: Responses-compatible custom provider adapter with validated base URL and auth env var.
- `module:function`: custom Python callable adapter.

### OpenAI runtime example

```yaml
provider:
  name: openai
  model: gpt-5-mini
  api_key_env: OPENAI_API_KEY
runtime:
  adapter: openai
  timeout_seconds: 60
  max_retries: 2
  retry_backoff_seconds: 1.0
  openai:
    base_url: https://api.openai.com/v1
```

### Custom API runtime example

```yaml
provider:
  name: my-gateway
  model: my-model-id
  api_key_env: CUSTOM_GATEWAY_TOKEN
  base_url: https://gateway.example/v1
runtime:
  adapter: custom_api
  timeout_seconds: 60
  max_retries: 2
  retry_backoff_seconds: 1.0
  custom_api:
    base_url: https://gateway.example/v1
```

## Contract documentation

- Config schema + version policy: [`docs/CONFIG_CONTRACT.md`](docs/CONFIG_CONTRACT.md)
- Pipeline state schema + deterministic role ordering: [`docs/PIPELINE_STATE.md`](docs/PIPELINE_STATE.md)
- Troubleshooting: [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md)
- Contributor CI requirements: [`CONTRIBUTING.md`](CONTRIBUTING.md)
- Release checklist for 1.0.0: [`MILESTONE_1_0_0.md`](MILESTONE_1_0_0.md)
- Changelog: [`CHANGELOG.md`](CHANGELOG.md)
- Release process: [`docs/RELEASE.md`](docs/RELEASE.md)
