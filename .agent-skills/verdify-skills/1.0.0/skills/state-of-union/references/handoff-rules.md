# Handoff rules

Choose one next skill. Do not hedge by listing several equal next steps.

Route by first blocking condition:

1. Product intent, users, lifecycle coverage, or success criteria are stale or missing -> `project-definition`.
2. Architecture, ADRs, module contracts, ownership boundaries, or deployment model are stale or missing -> `architecture-contracts`.
3. A strategy, priority, dependency, or acceptance decision needs a human owner -> open a `strategy` gate and keep `state-of-union`.
4. Deployment/log/telemetry health evidence is required before strategy can be
   trusted -> `release-verification` in `observability-diagnostics` mode.
5. Candidate issues are ready and no higher-priority gate exists -> `sprint-planning`.
6. An approved sprint is active and needs dispatch, monitoring, issue reconciliation, or gate resolution -> `sprint-orchestrator`.
7. Critic-approved lanes need a review packet before integration -> `release-verification` in `review-inbox` mode.
8. Approved lanes await integration, deployment proof, or outcome acceptance -> `release-verification`.

The handoff reason must cite durable evidence: artifact paths, issue numbers, PR numbers, gate IDs, sprint IDs, or deployment evidence.
