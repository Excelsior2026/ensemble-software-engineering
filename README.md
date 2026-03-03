# Ensemble Software Engineering (ESE)

ESE is a lightweight framework for AI-assisted software development using specialized model roles.

## Core pipeline
```mermaid
flowchart TD
  A[Human Scope] --> B[Architect]
  B --> C[Implementer]
  C --> D[Adversarial Reviewer]
  C --> E[Security Auditor]
  C --> F[Test Generator]
  C --> G[Performance Analyst]
  D --> H[Human Merge]
  E --> H
  F --> H
  G --> H
```

## Quick start
- Create artifacts for each role stage
- Run the pipeline via CLI
- Review severity findings

## GitHub Actions (optional)
Use `.github/workflows/ese.yml` to run ESE on pull requests.
