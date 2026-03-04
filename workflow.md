# ESE Workflow

## 1) Human scope
Define the feature, constraints, performance targets, non-goals, and risk posture.

## 2) Architect
Produce an architecture and interface spec.

## 3) Implementer
Generate the implementation according to the spec.

## 4) Adversarial Reviewer
Try to break assumptions and expose missing tests/edge cases.

## 5) Security Auditor
Perform threat modeling and misuse analysis.

## 6) Test Generator
Generate deterministic unit/edge case tests.

## 7) Performance Analyst
Evaluate scaling limits and resource constraints.

## 8) Human merge
Human decides whether risk is acceptable and the code should merge.

## Optional role extensions
- Documentation Writer: Maintains README/API docs and migration notes.
- DevOps/SRE: Reviews CI/CD safety, observability, and rollback readiness.
- Database Engineer: Reviews schema/index/migration correctness.
- Release Manager: Performs go/no-go checks and rollout risk analysis.
