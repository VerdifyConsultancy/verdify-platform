# Planner delivery lane

- Issue: `#427`
- Branch: `lane/recovery-planner-427`
- Worktree: `/Users/jason/repos/verdify-worktrees/software-recovery-planner-427`
- Sprint baseline: `0a9a19a840be6bae1beba604497d880b3b74b1ef`

## Outcome

Restore a self-healing Hermes/MCP path and a bounded, terminally truthful planner. Tool loss must fail readiness and recover indefinitely; a required `set_plan` trigger must be satisfied only by a valid full plan; materialization must apply the strictest bounds once, keep one expiring effective plan, and use the correct forecast comparator. The already-deployed non-authoritative planner_graph workload must also become worker-truthful or be explicitly decommissioned. Deterministic firmware remains authoritative.

## Readiness and sequencing

This lane is `READY_FOR_CRITIC` at substantive remediation head `8b2fdeca8184efad720b3e8ad7303dcb6012d6c2`. The prior independent `CHANGES_REQUIRED` verdict is preserved in [critic-report.md](critic-report.md), and the code/test disposition for every finding is in [critic-remediation.md](critic-remediation.md). Device-writer and DLI availability were independently accepted and merged before implementation began, and evidence-core was consumed as a soft input. Production deployment, migration application, alert mutation, and required-plan acceptance remain release-control work. The sprint baseline above remains the audit reference.

## Boundaries

The authoritative path and interface lists are in [lane.yaml](lane.yaml). The lane owns Hermes/MCP liveness, planner trigger and terminal ledgers, materialization/lifecycle, forecast scoring, context, and the narrow planner_graph worker/health/manifest surface. Read-only production evidence shows `planner_graph_runs=0`; planner_graph remains non-authoritative and must never receive production trigger routing, plan acceptance authority, or device-write access. The lane must not edit firmware, the dispatcher/device push path, or Grafana. Shared registry, schema-dump, and ingestor-manifest changes require controller coordination.

No lane worker may deploy, restart production services, retire stale intent, or clear the critical alert manually. Any schema change is serialized before consumers, receives rollback proof, and documents the required `verdify-mcp`/`verdify-ingestor` restart.

## Acceptance

The lane must prove MCP disconnect/recovery, terminal action classification, strict bound intersection, single-plan expiry, SUNRISE/SUNSET valid-plan-or-neutral behavior, and manifest/test health. If planner_graph remains deployed, fault injection must prove its worker cannot die behind green health after DB/DNS loss; if removed, desired state must make that explicit. Neither path may satisfy Hermes/MCP acceptance. Evidence is tied to the immutable lane head. Issue `#427`, the PR, specs/docs, and status/evidence manifests stay current.

The worker finishes with a pushed, clean branch, open linked PR, green required CI, recorded adversarial self-audit, and `READY_FOR_CRITIC`. An independent distributed-state/planner critic must accept the exact head before controller integration. Production acceptance remains `release-control` work.
