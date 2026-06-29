# Migration from the original sprint package

The original repository exposed one `verdify-agentic-sprint` skill with a 16-prompt sequence, duplicated root and skill-local prompts/schemas, and a draft workflow. Version 1.0 keeps its strongest rules—GitHub Issues as backlog, lane contracts, fresh critics, worktrees, human gates, integration, deployment proof, and outcome review—while changing the package boundary.

## Mapping

| Original capability | Version 1.0 owner |
|---|---|
| Discover, ingest review, interview, synthesize | `project-definition` discovery/requirements modes |
| Reconcile roadmap, backlog, issues, and north-star goal | `state-of-union` strategy review |
| Plan sprint, decompose lanes, compile contracts | `sprint-planning` as one approved transaction |
| Controller bootstrap/status guidance | `project-router` and `sprint-orchestrator` |
| Worker start and closeout | `lane-delivery` |
| Independent critic | `independent-critic` |
| Integration, deployment, closure, human review | `release-verification` |

## Breaking changes

- Invoke `project-router` instead of `verdify-agentic-sprint`.
- Use canonical root schemas only; skill-local schema copies were removed.
- Use `bin/verdify lane create` rather than manually assigning a worktree.
- Store machine-local leases in the shared Git directory.
- Treat the worktree path as runtime data, not a required field in lane contracts.
- Prefer one issue per lane; record an approved coupling justification for exceptions.
- Pin bootstrap sessions to a tag or commit.
