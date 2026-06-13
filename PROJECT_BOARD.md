# Verdify Platform Project Board

Last updated: 2026-06-13

Agent name: `verdify-platform`

## Connection Status

- Required board: `verdify-platform`.
- Current board: `VerdifyConsultancy` project #5,
  <https://github.com/orgs/VerdifyConsultancy/projects/5>.
- Current finding: the exact `verdify-platform` board now exists with 16
  EPIC-level issue cards and 22 fields.
- Repository link: linked to `VerdifyConsultancy/verdify-platform`.
- Visible Verdify organization boards:
  - `Agent Command Center Kanban`: project #4, 35 items, fields include the
    fleet-level board contract fields. Use as a template/source, not as the
    lane board.
  - `Verdify Platform`: project #1, 72 items, fields include `Status` with
    `Todo`, `In Progress`, and `Done`.
  - `Verdify Gravity Project`: project #2, 97 items, fields include `Status`,
    `Priority`, `Size`, `Estimate`, start date, and target date.
- Required fallback: keep `## Project Tracking` in every new or materially
  updated issue because issue bodies remain durable when project fields drift.

Workflow details live in `docs/PROJECT_BOARD_WORKFLOW.md`.

## Required Issue Block

```markdown
## Project Tracking

- Status: Backlog
- Priority: P2
- Effort: M
- Component: api/mcp/ingestor/deploy/k8s/docs
- Sprint: S0 lane-board-normalization
- Milestone: Lane board normalization
- Epic: Platform architecture inventory
- Agent Lane: verdify-platform
- Related Issues/PRs: none
- Dependencies: none
- Evidence: AGENT_LANE.md, EPICS.md
```

## Field Vocabulary

Statuses: `Backlog`, `Ready`, `In Progress`, `In Review`, `Done`.

Fields: `Priority`, `Effort`, `Component`, `Sprint`, `Epic`, `Agent Lane`,
`Milestone`, `Related Issues/PRs`, `Dependencies`, `Evidence`.

Board cards are EPICS. Child issues/tasks can exist, but planning decisions
happen at the epic level.

## Board Population Plan

The board-normalization pass added the epics listed in `EPICS.md` as board
cards, then added historical `Done` evidence links from `HISTORY.md`. Existing
issues were reused where they already owned the work; unclear ideas stay
`Backlog` with `Clarification Needed`.

## Current Epic Cards

| Status | Epic card |
|---|---|
| Done | #334 Lane Board Normalization |
| Ready | #207 Platform Architecture Inventory |
| Done | #331 API/Service Map |
| In Progress | #335 CI/CD And Promotion Hardening |
| In Progress | #336 ArgoCD Deployment And GitOps Cleanup |
| In Progress | #288 Deploy Enablement And Agent Access |
| In Progress | #218 Data/Storage Durability And DB HA/PITR |
| Ready | #75 Observability, Data Hygiene, And Product Health |
| In Progress | #287 Greenhouse Control Optimization |
| In Progress | #225 HA Resilience |
| Ready | #14 Compliance And Firmware Twins |
| Ready | #337 Decommission, Auth, And Residual Product Plane |
| Backlog | #16 Hardware And Seasonal Operations |
| Backlog | #332 Fable Workstream Clarification |
| Ready | #330 Repo Cleanup And Branch Review |
| Done | #333 Historical Completed Milestones |

Known existing issue anchors:

| Theme | Existing issue evidence |
|---|---|
| CI/CD green path | #69, #78, #82, #99, #126, #127, #128 |
| ArgoCD and cutover | #70, #73, #86, #216, #321 |
| Data and storage | #72, #84, #129, #218, #233, #245 |
| Device safety | #71, #79, #80, #89, #216, #317 |
| Observability | #75, #89, #200, #241 |
| Deploy enablement | #288, #301-#307 |
| Lane board normalization | #334 |
| CI/CD and promotion hardening | #335 |
| ArgoCD and GitOps cleanup | #336 |
| Decommission/auth/product plane | #337 |
| Repo cleanup/history | #330 |
| API/service map | #331 |
| Fable clarification | #332 |
| Historical lane roll-up | #333 |
| Branch/repo cleanup | #330 |

## Access Notes

Earlier fallback attachments exist on `Verdify Platform` project #1; use the
exact `verdify-platform` project #5 going forward. Project fields are useful for
views, but issue `## Project Tracking` blocks remain the durable fallback.
