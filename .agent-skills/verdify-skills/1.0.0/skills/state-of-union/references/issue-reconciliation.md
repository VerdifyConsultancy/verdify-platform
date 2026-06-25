# Issue reconciliation

Read live GitHub Issues when possible. Use `.agent-workflow/github/snapshot.json` only as a cache and record that limitation.

When backlog sync materially affects routing or sprint candidates, record the
classification set in `.agent-workflow/strategy/github-backlog-sync.yaml` and
validate it against `schemas/github-backlog-sync.schema.yaml`.

Classify each relevant issue:

- `candidate`: ready to sequence toward the north-star goal.
- `underspecified`: lacks problem, desired outcome, acceptance intent, risk, dependency, or evidence.
- `stale`: conflicts with approved artifacts, code, or delivery reality.
- `duplicate`: overlaps another issue at the same acceptance boundary.
- `blocked`: depends on a decision, architecture change, issue, PR, deployment, or external party.
- `missing`: required work has no GitHub Issue.
- `deferred`: valid but intentionally outside the next strategic sequence.
- `in_progress`: already represented by an active lane or PR.
- `done`: delivered and accepted or explicitly closed by policy.

Prefer issue updates over strategy-only notes. A strategy recommendation is not executable until backlog intent lives in GitHub or a durable Verdify artifact.

For each issue, record the evidence used, why it matters to the north-star goal, and the next action required before sprint planning.

When triage discovers runtime, log, deployment, telemetry, documentation, or
planning health work with no GitHub Issue, classify it as `missing` and record a
create-issue action. The proposed issue should include observed evidence,
severity, acceptance intent, owner/routing skill, and whether it blocks sprint
planning, review readiness, deployment verification, or outcome acceptance.
