# Release Safety Policy

Example external ESE policy plugin that adds doctor-time governance checks without modifying the core repository.

## Development

```bash
pip install -e .
ese policies
```

After installation, `ese doctor`, `ese start`, `ese task`, and `ese pr` will enforce the installed release-safety rule.
