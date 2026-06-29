# Workflow

The canonical machine-readable workflow is `verdify.workflow.yaml`. Human-readable lifecycle guidance is in `docs/lifecycle.md`.

The workflow keeps all 17 original delivery stages while exposing eighteen
coherent skills, including readiness loops required by the North Star evidence:

1. `project-router`
2. `transcript-replan` - transcript intake, routed proposals, conflicts, issues, and gates
3. `northstar-research-ingest` - research files, collateral copies, evidence registry, and query
4. `northstar-planning` - self-improving evidence, research, product/architecture drafts, feedback, and final lock approval
5. `northstar-interview` - North Star review findings, prioritized questions, proposed defaults, and answer routing
6. `northstar-question-resolution` - large question inventories, research, delegated answers, and human escalation packs
7. `project-definition` - discovery, requirements, product, design surface
8. `architecture-contracts` - architecture and black-box module contracts
9. `state-of-union` - source freshness, backlog alignment, health triage, strategy, and next sprint candidates
10. `repo-hygiene` - Wave 0 repository compliance before feature work
11. `sprint-planning` - issue selection, sprint planning, lane decomposition, ownership/review plan, lane-contract compilation
12. `sprint-orchestrator` - execution runbook, Agent Platform lane dispatch, terminal/session supervision, CI/CD and deployment readiness
13. `controller-loop` - durable outer-loop state, session ledger, and wave supervision
14. `platform-readiness` - Agent Platform, Kubernetes, CI/CD, secrets, observability, and review readiness
15. `gravity-readiness` - Gravity inventory, readiness checklist, and pilot design gate
16. `lane-delivery` - worker execution and closeout
17. `independent-critic`
18. `release-verification` - review inbox, diagnostics, integration, deployment verification, and outcome review

GitHub controls backlog and delivery state; versioned Verdify artifacts define
approved intent and execution contracts; the sprint execution runbook defines
Agent Platform lane session orchestration; local lane leases control worktree
ownership.
