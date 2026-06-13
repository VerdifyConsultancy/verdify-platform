# Verdify Platform Agent Lane

Last updated: 2026-06-13

Agent name: `verdify-platform`

## Mission

`verdify-platform` owns the Verdify core platform/application services lane for
the live greenhouse control system. Track A is keeping the 367 sq ft greenhouse
operational; Track B is platform and product evolution.

## Owned Resources

- Repository: `VerdifyConsultancy/verdify-platform`.
- Branch model: `main` is canonical; see `AGENTS.md` and `README.md`.
- Application namespaces authored here: `verdify-dev`, `verdify-prod`.
- Live production ArgoCD app name: `verdify-prod-dark` pointing at
  `deploy/k8s/overlays/prod`; rename target is `verdify-prod`.
- ArgoCD project: `app-test` as referenced by the Application manifests.
- App desired state: `deploy/k8s/base`, `deploy/k8s/components`, and
  `deploy/k8s/overlays/{dev,prod}`.
- CI/CD: `.github/workflows/ci.yml`, `container-publish.yml`,
  `k8s-manifests.yml`, `prod-promote.yml`, `promote-diff-guard.yml`,
  `cnpg-image.yml`, and `lab-content-pipeline.yml`.
- App secret contracts by name and key only: see `deploy/k8s/SECRETS.md`.
- Service contracts and architecture pointers: `README.md`,
  `docs/runbooks/laptop-operator.md`, `docs/RUNBOOK.md`,
  `docs/BCDR-AND-OPERATIONS.md`, `docs/SYSTEM-ARCHITECTURE.md`,
  `docs/FOLDER-HIERARCHY.md`, `docs/adr/`, and `docs/agents/`.
- Fable-related work only if it lands inside this repo.

## Components

- APIs/services: `api/`, `mcp/`, `ingestor/`, `planner_graph/`, `scripts/`.
- Controller/firmware source and validation: `firmware/`, `verdify_schemas/`,
  firmware replay and invariant tooling.
- Data/storage app contracts: `db/`, `deploy/k8s/base/db-statefulset.yaml`,
  `deploy/k8s/components/db-backup/`, `deploy/k8s/cnpg/`.
- Web/app surfaces inside this repo: `site/`, lab/grafana/umami/setpoint-server
  components under `deploy/k8s/components/`.

## Non-Goals

`verdify-platform` does not own marketing-site strategy outside this repo, DNS,
Cloudflare tunnel ownership, cluster-wide storage, GPU stack, shared monitoring
platforms, cluster-admin RBAC, CRDs, UDM/UniFi, NAS administration, credential
rotation, or raw secret custody.

## Hard Gates

Jason is the human gate for firmware OTA, the prod ArgoCD sync that can touch
the live writer, device VLAN actions, destructive prod DB work, credential
rotation, and outward-facing DNS/edge/org settings.

Do not create a second live device writer. Do not expose secrets. Do not make
durable cluster changes outside GitOps except emergency rollback or read-only
diagnostics.

## Dependency Agents

| Dependency | Needed for | Boundary |
|---|---|---|
| `storage-infra` | StorageClass, PV/PVC, backups, NAS/Longhorn/Synology | Request status/actions; do not mutate storage directly. |
| `network-infra` | DNS, routes, ingress, Cloudflare, device VLAN | Request named route or firewall work; do not change edge/network state. |
| `monitoring-stack` | Shared metrics, alerts, dashboards, log routing | Request telemetry integration; this repo owns only app-local dashboard manifests. |
| `cortex-ai-compute` | GPU/AI runtime if Verdify needs local model execution | Request runtime only when an in-repo workload requires it. |
| Jason | Live greenhouse, prod sync, OTA, credential and outward-facing gates | Explicit approval required. |

## Verification

Use `AGENTS.md` for the full order. For docs-only lane changes, run
`git diff --check`. For runtime changes, use `make lint`, `make test`, and the
area-specific firmware, migration, k8s, or site commands.
