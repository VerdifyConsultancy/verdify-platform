# Verdify Project Board Workflow

Last updated: 2026-06-16

This repo tracks work in GitHub issues first. Project Board fields are useful
for views, but issue bodies must remain self-describing because project field
access and naming can drift.

## Board Source Of Truth

- Preferred board: `verdify-platform`, owned by `VerdifyConsultancy`
  (<https://github.com/orgs/VerdifyConsultancy/projects/5>).
- Current canonical lane epic cards: #343-#352.
- Template/source board: `Agent Command Center Kanban` if copying field/view
  shape is needed.
- Fallback required by the lane objective: add a `## Project Tracking` block to
  every new or materially updated issue for this lane so issue bodies remain
  self-describing if project field access drifts.

## Statuses

Use these status values in issue tracking blocks and Project Board fields when
available:

- `Backlog`
- `Ready`
- `In Progress`
- `In Review`
- `Done`

When using an existing Verdify board with different option names, map `Todo` to
`Ready`, `Review/QA` to `In Review`, and `In progress` to `In Progress`.

## Required Fields

Each new or materially updated issue for `verdify-platform` should include:

```markdown
## Project Tracking

- Status: Ready
- Priority: P1
- Effort: L
- Component: firmware/ingestor/db/grafana/docs
- Sprint: S5 firmware-first-climate-core
- Milestone: G1 - Firmware-First Determinism
- Epic: L2 Firmware Core
- Agent Lane: verdify-platform
- Related Issues/PRs: #287, #344
- Dependencies: Jason OTA gate; schema-first sequencing
- Evidence: AGENTS.md, EPICS.md
```

Use `Dependencies: none` when the issue is entirely inside this repo. Use
`Clarification Needed` in the body for unclear work and keep the status
`Backlog`.

## Planning Order

1. Mine repo evidence first: `AGENTS.md`, `README.md`, `docs/`, `Makefile`,
   `pyproject.toml`, `.github/workflows/`, `deploy/k8s/`, open and recently
   closed issues, PRs, milestones, and recent commits.
2. Convert findings into lanes/epics before milestones, milestones before
   sprints, and sprints before granular issues.
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
