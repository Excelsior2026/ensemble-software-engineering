# ADR 0001: ESE Core vs. Vertical Application Boundary

- Status: Accepted
- Date: 2026-04-09

## Context

As ESE has expanded, the project has accumulated three distinct concerns:

1. the generic orchestration substrate
2. extension contracts and SDK surfaces
3. example or verticalized application bundles built on top of that substrate

The repository already states that ESE core should remain generic and that vertical applications should live outside the core repository. However, that doctrine is currently expressed across multiple documents rather than in a single durable decision record.

That leaves room for drift, especially when new features are proposed that are useful for one vertical product but do not belong in the substrate itself.

## Decision

ESE will be governed as a generic orchestration and assurance substrate, not as a vertical application repository.

Specifically:

- The `ese` core package owns orchestration, runtime abstraction, artifact/state contracts, dashboard/reporting substrate, CLI surfaces, and stable extension points.
- Vertical or domain-specific applications will live in separate repositories, even when they are built entirely on ESE.
- ESE may include example packs, example plugins, and reference application bundles only as portability proofs, SDK examples, and reference implementations.
- Reference bundles inside ESE are not the long-term home for product logic.
- Domain prompts, domain schemas, domain ingestion/persistence, product UI, product-specific evaluation datasets, and product-specific policy/reporting/integration logic should live in the vertical repository or installable application bundle.

## Rationale

This boundary preserves the properties ESE needs in order to stay credible as a reusable framework:

- **Portability:** ESE core remains installable and releaseable without any specific vertical product.
- **Upgradeability:** Vertical products can track and upgrade ESE without forking the core repository.
- **Governance clarity:** Product logic is easier to reason about when it is versioned and reviewed in the product repository.
- **Contract discipline:** Extension surfaces can evolve deliberately instead of being distorted by one product's immediate needs.
- **Commercial flexibility:** Multiple products can be built on the same substrate without bloating the core repo.

## Consequences

### What belongs in ESE core

- orchestration flow and role execution semantics
- provider/runtime abstraction
- preflight and policy-check infrastructure
- report/export/view/integration extension contracts
- artifact state, lineage, and evidence contracts
- generic dashboard and CLI capabilities
- SDK tooling for packs, bundles, checks, exporters, views, and integrations
- sample/reference implementations that prove the contracts work

### What does not belong in ESE core

- a long-lived vertical business application
- domain-owned prompts and schemas for a specific product line
- product-specific persistence or ingestion systems
- product-specific user interfaces beyond generic framework tooling
- product-specific evaluation corpora and acceptance logic
- product-specific policy/reporting/integration behavior that should instead ship as an installable bundle or sibling repository

## Implementation guidance

When a proposed change is discussed, the default question should be:

> Does this improve the substrate for many possible products, or is it primarily product logic for one vertical application?

If it is substrate capability, it may belong in ESE core.
If it is product logic, it should move to an application bundle or sibling vertical repository.

Reference bundles kept in this repository should be treated as:

- examples
- starter kits
- portability tests
- contract demonstrations

They should not become the permanent home of a production vertical offering.

## Related documents

- `README.md`
- `docs/EXTENSIBILITY.md`
- `docs/COMMERCIAL_PACKAGING.md`

## Supersedes

This ADR does not supersede an earlier ADR. It formalizes the operating doctrine already implied by the current documentation.
