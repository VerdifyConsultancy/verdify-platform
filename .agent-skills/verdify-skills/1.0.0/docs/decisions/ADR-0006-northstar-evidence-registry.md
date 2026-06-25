# ADR-0006: Add North Star evidence registry

- Status: accepted
- Date: 2026-06-23

## Context

North Star planning needs research inputs that are referenceable, queryable, and
validatable. Loose notes in a planning folder are useful for humans, but they do
not give agents stable IDs, source hashes, tags, claims, or provenance.

## Decision

Add `northstar-research-ingest` as a first-class skill and CLI-backed command
before `northstar-planning`.

The command:

- copies the original research file into `.agent-workflow/northstar/collateral/sources/`;
- writes a normalized `.agent-workflow/northstar/collateral/<evidence-id>.yaml`;
- updates `.agent-workflow/northstar/evidence-registry.yaml`;
- exposes each item as `northstar://evidence/<evidence-id>`;
- supports registry query through `bin/verdify northstar evidence list`.

## Consequences

- Research becomes durable planning collateral before synthesis.
- North Star planning consumes registered evidence instead of unstructured file
  discovery.
- The package now exposes seventeen canonical skills after `ADR-0008` adds
  `northstar-interview`.
- Research containing secrets, regulated data, or unclear provenance must be
  stopped before ingestion and handled through a gate or question.
