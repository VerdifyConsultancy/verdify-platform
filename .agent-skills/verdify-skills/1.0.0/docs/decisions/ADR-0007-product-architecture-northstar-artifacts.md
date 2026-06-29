# ADR-0007: Split North Star into product and architecture artifacts

- Status: accepted
- Date: 2026-06-23

## Context

The current `northstar-plan.yaml` captures synthesis, but the delivery system
needs durable human-readable planning artifacts that downstream skills can cite
directly. Product intent and architecture intent must stay separate enough for
review and ownership while remaining cross-linked through evidence and stable
IDs.

## Decision

Extend `northstar-planning` into an iterative artifact loop that creates and
maintains:

- `.agent-workflow/northstar/NORTHSTAR_PRODUCT.md`
- `.agent-workflow/northstar/NORTHSTAR_ARCHITECTURE.md`
- `.agent-workflow/northstar/northstar-artifacts.yaml`

`NORTHSTAR_PRODUCT.md` owns PRDs, users, product requirements, user stories,
milestones, waves, surfaces/shapes, review scripts, and product questions.

`NORTHSTAR_ARCHITECTURE.md` owns architecture stories, architecture
requirements, high-level design, infrastructure, interfaces, security/RBAC,
observability, release/rollback, ADR references, and architecture questions.

`northstar-artifacts.yaml` records loop status, iteration, evidence references,
cross-links, open questions, review state, approvals, and handoff.

## Consequences

- Downstream lifecycle skills must read and cite the signed-off product and
  architecture North Star artifacts when they exist.
- `northstar-planning` remains active through evidence, research, questions,
  design, and review feedback until final lock approval is recorded.
- Draft artifacts may be explicitly accepted as input, but they are not
  protected planning authority until signoff is recorded.
- The old `northstar-plan.yaml` remains useful as a structured synthesis/index
  rather than the only North Star artifact.
