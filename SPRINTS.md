# Verdify Platform Sprints

Last updated: 2026-06-13

Agent name: `verdify-platform`

The repo retired sprint-number-driven agent routing in favor of GitHub issues and
PR-scoped work. These sprint names are planning labels for Project Tracking
blocks, not durable branch names.

## S0: Lane Board Normalization

Status: `In Progress`

Goal: make `verdify-platform` discoverable as a least-privilege operating lane
with project tracking blocks, ArgoCD enforcement rules, and dependency requests.

Candidate issues:

- Platform architecture inventory.
- API/service map (#331).
- Fable ownership clarification (#332).
- Secret/access review.
- ArgoCD enforcement and prod app rename cleanup.
- Historical milestone roll-up (#333).

Verification:

- `git diff --check` for docs-only changes.
- GitHub issues contain `## Project Tracking` blocks where new or materially
  updated.

## S1: Platform Inventory And Service Map

Status: `To Do`

Goal: produce current k3s-era inventory for services, entrypoints, ports, data
stores, external dependencies, and verification commands without relying on old
VM-era architecture claims.

Dependency agents: `network-infra` for route truth, `storage-infra` for PVC and
backup truth, `monitoring-stack` for telemetry truth.

## S2: GitOps And Access Hardening

Status: `To Do`

Goal: close least-privilege gaps for ArgoCD app ownership, secret metadata,
namespace-local access, prod promotion, and direct-cluster-change exceptions.

Existing anchors: #301-#307, #317, #318, #320, #321.

## S3: Data/Storage And Observability Requests

Status: `Backlog`

Goal: align persistence, backup, CNPG/PITR, health checks, dashboards, and alert
handoffs with dependency agents.

Existing anchors: #75, #89, #218, #233, #241, #243-#245.
