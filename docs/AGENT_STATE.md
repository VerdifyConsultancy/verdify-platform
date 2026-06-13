# Agent State

Last updated: 2026-06-13

## Current Purpose

Verdify is the production control platform for one 367 sq ft greenhouse in
Longmont, CO. Track A is live greenhouse safety and continuity. Track B is
platform/product evolution through the k3s/GitOps service plane.

Future Codex sessions should start with `AGENTS.md`, then read this file,
`README.md`, `AGENT_LANE.md`, `PROJECT_BOARD.md`, `EPICS.md`, and the relevant
runbook or architecture references before editing.

## Architecture Pointers

- `docs/SERVICE_MAP.md` is the current service/API/k8s map for the
  `verdify-platform` lane.
- `docs/runbooks/laptop-operator.md` is the operator path for DB access,
  pipeline triggers, promotion, prod sync, and OTA flow.
- `deploy/k8s/argocd/apps/` defines the dev/prod ArgoCD applications.
- `deploy/k8s/base`, `deploy/k8s/components`, and `deploy/k8s/overlays/{dev,prod}`
  define desired app state. Staging is retired.
- `deploy/k8s/SECRETS.md` documents secret names and keys only; never expose raw
  secret values.
- `docs/SYSTEM-ARCHITECTURE.md` and `docs/FOLDER-HIERARCHY.md` remain useful but
  include VM-era details; prefer `AGENTS.md`, this file, and the k3s manifests
  when docs conflict.

## Active Plans

- GitHub issues are the live tracker for `VerdifyConsultancy/verdify-platform`.
- `EPICS.md`, `MILESTONES.md`, `SPRINTS.md`, and `PROJECT_BOARD.md` mirror the
  lane-level planning state.
- Issue #331 is the API/service-map workstream; its durable artifact is
  `docs/SERVICE_MAP.md`.
- Issue #332 keeps the Fable workstream in clarification until in-repo code,
  docs, or issue evidence exists.

## Known Risks / Blockers

- Production is live. Do not create a second ESP32/device writer.
- Jason is the human gate for firmware OTA, prod ArgoCD sync that can touch the
  live writer, device VLAN work, destructive prod DB work, credential rotation,
  and public DNS/edge/org changes.
- The exact requested `Agent Command Center Kanban` project board is not visible;
  use issue `## Project Tracking` blocks and the visible `Verdify Platform`
  project as the fallback.
- Some architecture docs predate the 2026-06-10 branch/deployment simplification
  and the k3s service-plane work.

## Last Verified Commands

- 2026-06-13: `git diff --check` passed for lane-tracking and service-map docs.
- 2026-06-13: GitHub CI and Container Publish were green on `main` at commit
  `d9f30c2`.

## Next Recommended Codex Prompt

```text
Wake in /Users/jason/repos/verdify-platform. Read AGENTS.md, README.md,
docs/AGENT_STATE.md, AGENT_LANE.md, PROJECT_BOARD.md, EPICS.md, and
docs/SERVICE_MAP.md. Report branch/worktree state, access assumptions, live
greenhouse safety gates, relevant tracker items, and the smallest safe
verification path before proposing edits.
```
