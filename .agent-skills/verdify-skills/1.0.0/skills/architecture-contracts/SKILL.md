---
name: architecture-contracts
description: Creates an approved north-star architecture and converts it into black-box module contracts with stable inputs, outputs, interfaces, ownership, invariants, and validation. Use after project definition, when architecture is missing or stale, or before strategy review and parallel implementation lanes are planned.
compatibility: Requires an approved project definition and repository access. Material security, migration, public-interface, or deployment decisions may require human approval.
metadata:
  author: Verdify
  version: "1.0.0"
  lifecycle-order: "3"
---

# Architecture and Module Contracts

Define the system before dividing implementation work. Architecture describes the whole; module contracts define independently buildable black boxes.

## Canonical artifacts

- `.agent-workflow/architecture/architecture.yaml`
- `.agent-workflow/architecture/north-star-architecture.md`
- `.agent-workflow/architecture/decisions/ADR-*.md`
- `.agent-workflow/modules/module-map.md`
- `.agent-workflow/modules/contracts/<module-id>.contract.yaml`
- `.agent-workflow/modules/dependency-graph.md`
- `.agent-workflow/modules/interface-risk-report.md`

## Mode 1: north-star architecture

1. Verify that `NORTHSTAR_PRODUCT.md`, `NORTHSTAR_ARCHITECTURE.md`, and
   `northstar-artifacts.yaml` are approved or explicitly accepted as draft
   input, and that project definition is approved and traceable.
2. Reconstruct existing code and deployment reality before proposing changes.
3. Define system context, components, runtime topology, data flow, storage, external integrations, trust/security boundaries, deployment model, observability, failure modes, and major tradeoffs.
4. Record material choices as ADRs with alternatives and consequences.
5. Trace components and risks to requirements and design surfaces.
6. Validate `architecture.yaml` against `../../schemas/architecture.schema.yaml`.

Read `references/architecture-mode.md` for decision coverage.

## Mode 2: module contracts

For each module, define:

- purpose and requirement IDs;
- owned and prohibited paths;
- typed inputs and outputs;
- public interfaces and compatibility rules;
- hard and soft dependencies;
- invariants and failure behavior;
- runtime/resource ownership;
- contract tests and validation commands;
- evidence and definition of done.

The module implementation may vary internally while the contract remains stable. Read `references/module-contract-mode.md`.

## Interface review

Review all contracts as a set. Detect cycles, ambiguous ownership, duplicated responsibilities, shared mutable resources, incompatible schemas, and untestable boundaries. Record coordination surfaces and serialize coupled modules rather than forcing parallelism.

Read `references/contract-review.md` before approval.

## Boundaries

Do not create sprint lanes in this skill. A module may take multiple sprints; a lane may implement a bounded slice of one module. `sprint-planning` chooses the current slice.

## Approval and handoff

Architecture and module contracts must be approved, versioned on the default branch or in an approved PR, and free of blocking interface risks before handoff to `state-of-union`.
