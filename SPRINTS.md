# Verdify Platform Sprints

Last updated: 2026-06-13

Agent name: `verdify-platform`

The repo retired sprint-number-driven agent routing in favor of GitHub issues and
PR-scoped work. These sprint names are planning labels for Project Tracking
blocks, not durable branch names.

## S0: Lane Board Normalization

Status: `Done`

Goal: make `verdify-platform` discoverable as a least-privilege operating lane
with a lane-specific GitHub Project Board named exactly `verdify-platform`,
epic-level cards, project tracking blocks, ArgoCD enforcement rules, and
dependency requests.

Candidate issues:

- Lane Board Normalization.
- Platform Architecture Inventory.
- Fable Workstream Clarification (#332).
- Repo Cleanup And Branch Review (#330).
- ArgoCD Deployment And GitOps Cleanup.
- Historical Completed Milestones (#333).

Verification:

- `git diff --check` for docs-only changes.
- The `verdify-platform` Project Board exists with epic-level cards.
- GitHub issues contain `## Project Tracking` blocks where new or materially
  updated.

## S1: Platform Inventory And Service Map

Status: `Done`

Goal: produce current k3s-era inventory for services, entrypoints, ports, data
stores, external dependencies, and verification commands without relying on old
VM-era architecture claims.

Dependency agents: `network-infra` for route truth, `storage-infra` for PVC and
backup truth, `monitoring-stack` for telemetry truth.

Evidence: issue #331 and `docs/SERVICE_MAP.md`.

## S2: GitOps And Access Hardening

Status: `Ready`

Goal: close least-privilege gaps for ArgoCD app ownership, secret metadata,
namespace-local access, prod promotion, and direct-cluster-change exceptions.

Existing anchors: #301-#307, #317, #318, #320, #321.

## S3: Data/Storage And Observability Requests

Status: `Backlog`

Goal: align persistence, backup, CNPG/PITR, health checks, dashboards, and alert
handoffs with dependency agents.

Existing anchors: #13, #14, #75, #89, #218, #233, #241, #243-#245.
