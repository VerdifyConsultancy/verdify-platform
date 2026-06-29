---
name: project-definition
description: Converts raw project knowledge into an approved end-to-end project definition covering discovery, requirements, product intent, design surfaces, lifecycle coverage, architecture-significant constraints, delivery, operations, relationships, and traceability. Use for new or poorly understood projects, after major product or technical direction changes, or when requirements, users, scope, data, infra, hosting, deployment, operations, integrations, ownership, flows, interfaces, or human approval points are missing or contradictory.
compatibility: Requires access to supplied project sources and permission to write .agent-workflow project artifacts. Human input may be required for material ambiguity.
metadata:
  author: Verdify
  version: "1.0.0"
  lifecycle-order: "2"
---

# Project Definition

Build one traceable end-to-end project definition through four ordered modes plus a final lifecycle coverage pass. Do not jump from raw notes to architecture, sprint planning, or implementation. Define the intent, constraints, relationships, and operating expectations that those later roles must satisfy.

## Canonical artifacts

- `.agent-workflow/northstar/NORTHSTAR_PRODUCT.md` — signed-off product North
  Star input when present
- `.agent-workflow/northstar/NORTHSTAR_ARCHITECTURE.md` — signed-off architecture
  North Star input when present
- `.agent-workflow/northstar/northstar-artifacts.yaml` — signoff and open-question
  status for North Star artifacts
- `.agent-workflow/project/project-definition.yaml` — authoritative structured definition
- `.agent-workflow/project/discovery.md` — generated human view
- `.agent-workflow/project/requirements.md` — generated human view
- `.agent-workflow/project/product.md` — generated human view
- `.agent-workflow/project/design-surface.md` — generated human view
- `.agent-workflow/project/lifecycle-readiness.md` — generated human view

The YAML is canonical. Markdown views summarize it and must not introduce new decisions.

## Select a mode

Run the earliest incomplete mode unless the router names one:

1. `discovery`
2. `requirements`
3. `product`
4. `design-surface`

## Discovery mode

1. Inventory every supplied source: signed-off North Star product/architecture
   artifacts, repository docs, transcripts, notes, research, prior outputs,
   screenshots, spreadsheets, issues, and existing specifications.
2. Summarize what each source actually supports.
3. Record known decisions, stated goals, constraints, assumptions, undefined terms, contradictions, relationships, and missing evidence.
4. Reconstruct the project ecosystem: users, teams, external systems, vendors, data domains, environments, infrastructure, hosting, deployment, operations, support, governance, and approval owners when evidence exists.
5. Ask evidence-based questions only after intake. Do not ask generic questions already answered by sources.
6. Record human answers and mark affected decisions.

Read `references/discovery-mode.md` when source material is large or contradictory.

## Requirements mode

1. Convert approved discovery into uniquely identified functional and non-functional requirements.
2. Record constraints, security, privacy, compliance, accessibility, performance, availability, scalability, resilience, integration, deployment, rollback, observability, data, migration, operability, maintainability, cost, and human-in-loop requirements when relevant.
3. Give each requirement measurable acceptance criteria and source/decision links.
4. Keep open questions explicit. Do not disguise assumptions as requirements.

Read `references/requirements-mode.md` for quality rules.

## Product mode

Define primary users, stakeholders, problem statement, jobs, workflows, minimum useful scope, non-goals, success metrics, product risks, rollout/adoption expectations, documentation needs, support expectations, and user-visible completion. Resolve conflicts between desired features and stated constraints through a decision gate.

Read `references/product-mode.md` for scope and metric guidance.

## Design-surface mode

Define every intentional interaction surface: UI, CLI, API, event, MCP/tool, agent workflow, configuration, deployment/release control, observability output, support/admin surface, review queue, and human approval flow. For each surface, define inputs, outputs, states, errors, permissions, and acceptance behavior.

Read `references/design-surface-mode.md` when the product is API-, agent-, or workflow-first rather than UI-first.

## Lifecycle coverage pass

Before final approval, read `references/lifecycle-coverage.md` and update `lifecycle` in the canonical YAML.

1. Mark every required coverage area as `covered`, `not_applicable`, `deferred`, or `unknown`. A material `unknown` blocks approval.
2. Record relationships between users, teams, services, vendors, regulators, deployment platforms, data providers, and approval owners.
3. Record architecture inputs without designing the architecture: system context, architecture decision areas, quality attributes, constraints, and assumptions.
4. Record delivery and operations expectations: environments, infrastructure, hosting, configuration, secrets, deployment, rollback, migrations, observability, support, incident response, and documentation.
5. Record open gaps with owner, impact, and blocking status. Blocking gaps must become human gates before architecture or sprint planning.
6. Write `lifecycle-readiness.md` from the same YAML; do not add decisions only in Markdown.

## Traceability

Use stable IDs such as `SRC-001`, `DEC-001`, `FR-001`, `NFR-001`, `AC-001`, `USR-001`, `FLOW-001`, `COV-001`, `REL-001`, and `GAP-001`. Every requirement must trace to evidence or an approved decision; every product, design, lifecycle coverage, relationship, and handoff element must trace to requirements, evidence, or an approved decision. Read `references/traceability.md` before final approval.

## Approval and handoff

Validate against `../../schemas/project-definition.schema.yaml`. A human or delegated policy owner approves material product intent and material lifecycle constraints. After all four modes are approved, lifecycle coverage has no blocking gaps, and the artifact validates, hand off to `architecture-contracts` with the canonical YAML and views.

## Stop conditions

Open a durable gate instead of proceeding when primary users, problem, scope, public behavior, data handling, security/privacy/compliance expectations, infrastructure or hosting expectations, deployment/rollback expectations, operational ownership, external relationships, approval authority, or success criteria remain materially unresolved.
