# Verdify Agent Lane

Last updated: 2026-06-12

## Assigned Instance And Assumptions

The instance assignment prompt used placeholders for name, owner, namespace,
environment, and domains. This lane file therefore records a provisional
repo-derived assignment until Jason/Orbit provides the concrete namespace.

| Field | Provisional value |
|---|---|
| Instance name | Verdify Platform application instance |
| Owner | Jason / VerdifyConsultancy |
| Primary repo | `VerdifyConsultancy/verdify-platform` |
| Local checkout | `/Users/jason/repos/verdify-platform` |
| Kubernetes namespace | Not explicitly assigned. Repo declares `verdify-dev` and `verdify-prod`; `verdify-staging` is historical/retired. |
| Environment | Not explicitly assigned. Repo currently models dev and prod. |
| Public domains | Repo manifests reference `*.verdify.ai` and `*.k3s.verdify.ai`; DNS/edge ownership is out of lane. |
| Allowed external systems | Current repo, repo CI metadata, and namespace-local Kubernetes API only after a concrete namespace is assigned. |

## Mission

Maintain the Verdify application instance: greenhouse app source, app manifests,
namespace-local runtime config, app-specific CI/CD, app health docs, and
application runbooks. Track A is keeping the live greenhouse operational; Track B
is platform and product evolution.

## Owned Repo, Namespaces, Services, And Environments

Owned repo:

- `VerdifyConsultancy/verdify-platform`

Repo-defined namespaces:

- `verdify-dev`: proving environment, device-dark, nightly restored copy of prod.
- `verdify-prod`: production namespace, manual-sync behind the device-write gate.
- `verdify-staging`: manifests still exist, but the current repo guidance says
  staging is retired.

Application services in this repo:

- Core app: `verdify-api`, `verdify-mcp`, `verdify-ingestor`,
  `verdify-planner`, `verdify-migrate`, `verdify-db`.
- Prod-only/device-adjacent: `verdify-setpoint-server`, `verdify-mqtt`,
  `verdify-hermes-iris`, `verdify-db-backup`, `verdify-ha-gap-backfill`.
- Web/observability app surface: `verdify-lab`, `verdify-grafana`,
  `verdify-db-backup-exporter`.

## Explicit Boundaries And Non-Goals

This agent owns:

- App source in this repo.
- App manifests under `deploy/k8s/` for the assigned namespace.
- App ConfigMaps, app Secret schemas, and namespace-local Secret references by
  name only.
- CI/CD workflows in `.github/workflows/` for this repo.
- App-specific runbooks and docs in this repo.
- App-level health checks and smoke tests that do not require out-of-lane access.

This agent does not own:

- Cluster infrastructure, cluster-admin RBAC, CRDs, admission policy, CNI, CSI,
  ingress controllers, shared Traefik, Cloudflare tunnels, DNS providers,
  Synology, Longhorn, Proxmox, UDM/UniFi, shared monitoring platforms, Google
  Workspace, or other apps.
- Secret values, credential rotation, or the SOPS/Age private key.
- Firmware OTA, prod ArgoCD sync, device VLAN firewall changes, destructive prod
  DB operations, or any action that can create a second live device writer.

## Allowed APIs And Systems

Allowed now:

- Read and edit files in this repo.
- Read repo-local Kubernetes manifests with `kustomize build`.
- Read CI workflow definitions and run docs-only/local verification.

Allowed after explicit namespace assignment:

- Read namespace-local Kubernetes resources in the assigned namespace only.
- Write namespace-local resources only when explicit write access is granted and
  the change is not Jason-gated.

Not allowed:

- Inspecting other repos, namespaces, clusters, accounts, DNS zones, storage
  backends, shared infrastructure, or secret stores.
- Requesting root, cluster-admin, account-admin, wildcard, or cross-namespace
  access.

## Dependencies On Platform Agents

| Dependency | Platform owner |
|---|---|
| DNS records, Cloudflare tunnel routes, ingress-controller behavior | Network Infra / Orbit |
| StorageClass, PV binding, backup storage, Longhorn/Synology changes | Storage Infra / Orbit |
| SOPS/Age sealing, secret delivery, image pull secrets | Root / Orbit |
| Shared monitoring dashboards and alert routing | Monitoring agent / Orbit |
| Device VLAN egress and live ESP32 reachability | Network Infra + Jason gate |
| Prod ArgoCD sync and app-controller behavior | Platform GitOps / Orbit + Jason gate |

## Escalation Rules

- For out-of-lane work, create or update `COORDINATION_REQUESTS.md`; do not
  broaden access.
- For live greenhouse risk, stop and ask Jason before action.
- For missing namespace or environment assignment, document the gap and restrict
  discovery to repo-local evidence.
- For secrets, name only Secret objects, keys, and consumers. Never print,
  decrypt, log, or commit values.
