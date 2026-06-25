# ADR-0004: Add readiness-loop skills

- Status: accepted
- Date: 2026-06-23

## Context

The original cohesive package exposed nine skills around definition,
architecture, strategy, sprint planning, orchestration, lane delivery,
criticism, and release verification. New North Star evidence requires additional
loops before autonomous feature work can safely run:

- transcript-driven replanning;
- Wave 0 repository hygiene;
- durable controller state and session ledger;
- Agent Platform and environment readiness;
- Gravity-specific readiness and pilot gating.

These loops cannot be treated as ordinary implementation lanes because they own
source intake, platform gates, cross-repo readiness, and human sign-off
semantics.

## Decision

Add five first-class skills:

- `transcript-replan`
- `repo-hygiene`
- `controller-loop`
- `platform-readiness`
- `gravity-readiness`

Keep the original delivery lifecycle intact. The new skills gate and supervise
the lifecycle; they do not replace `project-definition`, `architecture-contracts`,
`sprint-planning`, `lane-delivery`, `independent-critic`, or
`release-verification`.

## Consequences

- The package now exposes seventeen canonical skills after `ADR-0005` adds
  `northstar-planning`, `ADR-0006` adds `northstar-research-ingest`, and
  `ADR-0008` adds `northstar-interview`.
- Router and state-of-union handoffs may target readiness-loop skills.
- Repo hygiene is required before sprint planning when a strategy is ready for
  feature execution.
- Gravity implementation remains blocked until `gravity-readiness` and
  `platform-readiness` evidence is approved.
- Existing one issue/lane/branch/worktree/session/PR policy remains the default
  until an approved architecture or policy change resolves the wave-branch
  conflict.
