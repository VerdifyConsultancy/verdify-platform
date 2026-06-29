# ADR-0010: Make planning and review comprehensive across backlog, health, lanes, and review packets

- Status: accepted
- Date: 2026-06-24

## Context

Operators want to ask for a full project replan in one natural request: inspect
the project, triage GitHub Issues and planning artifacts, check live deployment
and recent logs, add health issues to tracking, define what is in and deferred,
carve work into lanes and waves, assign ownership, and report QA and human
review milestones.

Earlier Verdify skills had the right lifecycle pieces, but the operating path
was implicit. `state-of-union` could classify issues, `sprint-planning` could
create lane contracts, and `release-verification` could assemble review and
deployment evidence. The missing contract was the comprehensive loop between
those skills.

Brave Search research registered as
`northstar://evidence/NSE-20260624-comprehensive-planning-and-review-loop-b`
supports keeping this loop evidence-first and control-plane-first: GitHub owns
backlog/dependencies/milestones, runtime health needs observable deployment and
telemetry evidence, sprint planning needs a stakeholder-readable plan, and
review readiness needs exact revision/check/deployment/rollback evidence.

## Decision

Keep Verdify decomposed into lifecycle skills, but make the planning/review path
explicitly comprehensive:

- `state-of-union` records source freshness, planning artifact inventory,
  delivery health, discovered health issues, issue actions, recommended
  sequence, sprint candidates, and one handoff.
- `github-backlog-sync` remains the detailed GitHub control-plane reconciliation
  artifact for issues, PRs, lanes, checks, deployments, Projects, dependencies,
  and delivery findings.
- `release-verification` owns observability diagnostics when planning, review,
  readiness, release, incidents, or feedback need runtime/log evidence.
- `sprint-planning` records included and deferred work, lane owners/reviewers,
  QA milestones, human review milestones, user stories for review, lane
  contracts, and wave release plan in one approval transaction.
- The router requires a complete approving review inbox packet before routing
  critic-approved lanes to integration.

## Consequences

- A broad "triage and replan everything" prompt now has a durable lifecycle path
  instead of producing a private chat plan.
- Health issues discovered from deployments, logs, telemetry, probes, or user
  feedback become GitHub issue actions or diagnostics rather than release-note
  footnotes.
- Sprint plans now answer "what's in, what's deferred, who owns what, when is
  QA/human review, and what user stories are reviewable" from canonical YAML.
- Integration waits for review packet evidence, reinforcing that critic approval
  and review readiness are related but separate gates.
