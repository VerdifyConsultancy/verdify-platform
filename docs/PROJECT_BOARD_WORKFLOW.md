# Verdify Project Board Workflow

Last updated: 2026-06-13

This repo tracks work in GitHub issues first. Project Board fields are useful
for views, but issue bodies must remain self-describing because project field
access and naming can drift.

## Board Source Of Truth

- Preferred board: `Agent Command Center Kanban`.
- Current access finding: that exact board is not visible from this checkout's
  available GitHub account/project scope.
- Visible Verdify boards: `VerdifyConsultancy` project `Verdify Platform` and
  `Verdify Gravity Project`.
- Fallback required by the lane objective: add a `## Project Tracking` block to
  every new or materially updated issue for this lane until the preferred board
  and fields are available.

## Statuses

Use these status values in issue tracking blocks and Project Board fields when
available:

- `Backlog`
- `To Do`
- `In Progress`
- `Review/QA`
- `Done`
- `Blocked`

When using an existing Verdify board with different option names, map `Todo` or
`Ready` to `To Do`, `In review` to `Review/QA`, and `In progress` to
`In Progress`.

## Required Fields

Each new or materially updated issue for `verdify-platform` should include:

```markdown
## Project Tracking

- Status: Backlog
- Priority: P2
- Effort: M
- Component: deploy/k8s
- Sprint: S0 lane-board-normalization
- Epic: ArgoCD deployment
- Agent Lane: verdify-platform
- Related Issue/PR: none
- Dependencies: storage-infra, network-infra
```

Use `Dependencies: none` when the issue is entirely inside this repo. Use
`Clarification Needed` in the body for unclear work and keep the status
`Backlog`.

## Planning Order

1. Mine repo evidence first: `AGENTS.md`, `README.md`, `docs/`, `Makefile`,
   `pyproject.toml`, `.github/workflows/`, `deploy/k8s/`, open and recently
   closed issues, PRs, milestones, and recent commits.
2. Convert findings into epics before milestones, milestones before sprints, and
   sprints before granular issues.
3. Prefer updating an existing issue when it already owns the work. Create a new
   issue only when there is no primary owner.
4. Add historical work as `Done` with evidence links to merged PRs, commits, or
   closed issues.
5. Add unclear work as `Backlog` with `Clarification Needed`.

## GitOps Rule

Kubernetes durable desired state is GitOps-managed. Do not use direct
`kubectl apply/edit/patch` for durable changes except emergency rollback or
read-only diagnostics. Every workload, namespace, secret reference, ingress,
PVC, RBAC, and config change must trace to Git, PR, ArgoCD sync health, and an
issue or project tracking block.

## Cross-Agent Rule

One issue has one primary owning agent. If work crosses repo, namespace,
account, service, or lane boundaries, create a coordination request instead of
taking ownership. Name the target agent, required action, minimal access,
blocker status, and deadline.
