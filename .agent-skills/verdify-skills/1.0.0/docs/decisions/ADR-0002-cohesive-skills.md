# ADR-0002: Nine coherent lifecycle skills

- Status: accepted, extended by ADR-0004
- Date: 2026-06-22

## Decision

Preserve 17 detailed lifecycle stages but expose nine discoverable delivery
skills. Group discovery through design into `project-definition`, architecture
and modules into `architecture-contracts`, strategy/backlog reconciliation into
`state-of-union`, plan and lane compilation into `sprint-planning`,
implementation and closeout into `lane-delivery`, and integration through
outcome into `release-verification`.

ADR-0004 extends this package with readiness-loop skills without changing the
delivery-skill grouping above.

## Consequences

Activation is more reliable and context overhead is lower. Fresh-context criticism and deployment verification remain separate privileged roles.
