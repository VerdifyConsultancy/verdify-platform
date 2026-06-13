# Verdify Platform Epics

Last updated: 2026-06-13

Agent name: `verdify-platform`

## Current Planning Epics

| Epic | Status | Priority | Evidence / tracker |
|---|---|---|---|
| Platform architecture inventory | To Do | P1 | Needs a current k3s-era map that reconciles `README.md`, `docs/runbooks/laptop-operator.md`, `deploy/k8s/`, and older architecture docs. |
| API/service map | To Do | P1 | Issue #331. Services live across `api/`, `mcp/`, `ingestor/`, `planner_graph/`, `scripts/`, `firmware/`, and `deploy/k8s/components/`. |
| Fable workstream | Backlog | P3 | Issue #332. No in-repo Fable surface found in this pass; keep as `Clarification Needed` until code or issues appear. |
| CI/CD cleanup | In Progress | P1 | Existing anchors: #69, #78, #82, #99, #126, #127, #128, #319, #320, #322. |
| ArgoCD deployment | In Progress | P1 | Existing anchors: #70, #73, #86, #216, #317, #318, #321. |
| Secret audit | To Do | P1 | Secret schema is in `deploy/k8s/SECRETS.md`; access review anchors: #30, #66, #105, #301, #305. |
| Data/storage requirements | In Progress | P1 | Existing anchors: #72, #84, #129, #218, #233, #243-#245. |
| Observability requests | To Do | P2 | Existing anchors: #75, #89, #200, #241. Shared monitoring requests go to `monitoring-stack`. |
| Historical completed milestones | Done | P2 | Issue #333, `HISTORY.md`, and closed issues #69-#73, #216, #217. |

## Historical Epics Already Represented

| Epic | Status | Evidence |
|---|---|---|
| CI/CD Green Path | Done | Closed issue #69 and related completed issues #78, #81, #82, #92, #99, #126-#128. |
| Data and State Durability | Mixed | Closed issue #72; active follow-ups remain under #218 and #245. |
| Device Safety / Single Writer | Mixed | Closed issue #71 and record #216; active follow-ups include #89, #317, and #321. |
| Product Plane | Mixed | Closed issue #134; active work remains in data hygiene, observability, and hardware/operator-gated issues. |
| Deploy Enablement | In Progress | Epic #288 with tasks #301-#307. |
| HA Resilience | In Progress | Milestone #14 with closed HA sprint issues and open CNPG/cutover follow-ups. |

## Rules

- One issue has one primary owning agent.
- If work depends on another lane, record the dependency in
  `COORDINATION_REQUESTS.md` and the issue `## Project Tracking` block.
- Historical work must be marked `Done` only when linked to closed issues,
  merged PRs, commits, or durable runbook evidence.
