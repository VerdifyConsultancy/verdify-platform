# Verdify Platform Project Board

Last updated: 2026-06-13

Agent name: `verdify-platform`

## Connection Status

- Requested board: `Agent Command Center Kanban`.
- Current finding: the exact board name is not visible under the accessible
  `jvallery` user projects or `VerdifyConsultancy` organization projects.
- Visible Verdify organization boards:
  - `Verdify Platform`: project #1, 72 items, fields include `Status` with
    `Todo`, `In Progress`, and `Done`.
  - `Verdify Gravity Project`: project #2, 97 items, fields include `Status`,
    `Priority`, `Size`, `Estimate`, start date, and target date.
- Required fallback: use `## Project Tracking` in every new or materially
  updated issue until the preferred board and fields are available.

Workflow details live in `docs/PROJECT_BOARD_WORKFLOW.md`.

## Required Issue Block

```markdown
## Project Tracking

- Status: Backlog
- Priority: P2
- Effort: M
- Component: api/mcp/ingestor/deploy/k8s/docs
- Sprint: S0 lane-board-normalization
- Epic: Platform architecture inventory
- Agent Lane: verdify-platform
- Related Issue/PR: none
- Dependencies: none
```

## Field Vocabulary

Statuses: `Backlog`, `To Do`, `In Progress`, `Review/QA`, `Done`, `Blocked`.

Fields: `Priority`, `Effort`, `Component`, `Sprint`, `Epic`, `Agent Lane`,
`Related Issue/PR`, `Dependencies`.

## Board Population Plan

The first board-normalization pass should create or update issues for the
epics listed in `EPICS.md`, then add historical `Done` evidence links from
`HISTORY.md`. Existing issues should be reused when they already own the work.

Known existing issue anchors:

| Theme | Existing issue evidence |
|---|---|
| CI/CD green path | #69, #78, #82, #99, #126, #127, #128 |
| ArgoCD and cutover | #70, #73, #86, #216, #321 |
| Data and storage | #72, #84, #129, #218, #233, #245 |
| Device safety | #71, #79, #80, #89, #216, #317 |
| Observability | #75, #89, #200, #241 |
| Deploy enablement | #288, #301-#307 |
| Repo cleanup/history | #330 |
| API/service map | #331 |
| Fable clarification | #332 |
| Historical lane roll-up | #333 |

## Access Gap

Project V2 field writes cannot be considered complete until the exact
`Agent Command Center Kanban` board is visible or its owner/project number is
provided. Until then, issue tracking blocks are the durable source. Issues #75,
#207, #218, #305, #321, #322, #331, #332, and #333 are attached to the visible
`Verdify Platform` project #1 as the closest available board.
