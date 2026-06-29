# Verdify Platform Agent Lane

Last updated: 2026-06-17

Agent name: `verdify-platform`

## Mission

`verdify-platform` owns the live greenhouse controller platform for the 367 sq
ft Longmont greenhouse. The current planning mission is the 2026-06-16
controller replan: make the stack firmware-first, deterministic, auditable, and
crop-agnostic at the firmware layer, while keeping AI bounded to tactical
tunables.

Track A is keeping the greenhouse safe and operational. Track B is platform and
product evolution. Track A wins whenever they conflict.

## Owned Resources

- Repository: `VerdifyConsultancy/verdify-platform`.
- Canonical project board: `VerdifyConsultancy` project #5,
  <https://github.com/orgs/VerdifyConsultancy/projects/5>.
- Canonical current lane epics: GitHub issues #343-#352.
- Branch model: `main` is canonical; see `AGENTS.md`.
- Environment model: single-env prod only. `verdify-dev`, staging, and
  `live/platform-main` are retired/deleted.
- Live production namespace: `verdify-prod`.
- Live production ArgoCD app: `verdify-prod-dark` (legacy name) pointing at
  `deploy/k8s/overlays/prod`, manual-sync behind the device-write gate.
- CI/CD: `.github/workflows/ci.yml`, `container-publish.yml`,
  `k8s-manifests.yml`, `prod-promote.yml`, `promote-diff-guard.yml`,
  `cnpg-image.yml`, `lab-content-pipeline.yml`, and
  `reusable-container-build.yml`.
- App secret contracts by name and key only: see `deploy/k8s/SECRETS.md`.
- Service contracts and architecture pointers: `README.md`,
  `docs/SERVICE_MAP.md`, `docs/runbooks/laptop-operator.md`,
  `docs/RUNBOOK.md`, `docs/BCDR-AND-OPERATIONS.md`,
  `docs/SYSTEM-ARCHITECTURE.md`, `docs/FOLDER-HIERARCHY.md`, `docs/adr/`,
  `docs/agents/`, and `docs/reviews/data-path-adversarial-review-2026-06-16.md`.
- Dashboards authored in this repo: Grafana manifests/generated ConfigMaps and
  `docs/grafana-panel-catalog.md`.
- Lab notebook and publishing code: `site/`, `scripts/lab-publish-k3s.sh`,
  `lab-content-pipeline.yml`, and S3-backed lab publisher manifests.

## Current Lanes

| Lane | Canonical issue | Status | Milestone |
|---|---:|---|---|
| L1 Architecture Audit, Drift Check, and CI/CD | #343 | Done (2026-06-17) | G0 - Controller Architecture Audit |
| L2 Firmware Core | #344 | Done (2026-06-17) | G1 - Firmware-First Determinism |
| L3 Climate Control | #345 | Done (2026-06-17) | G1 - Firmware-First Determinism |
| L4 AI Planner and Tunables | #346 | Ready | G3 - Planner, Irrigation, Lab, and Research |
| L5 Data, Schema, and Source of Truth | #347 | Ready | G2 - Data Contracts and Observability |
| L6 Observability, Dashboards, and KPIs | #348 | Ready | G2 - Data Contracts and Observability |
| L7 Lighting and Occupancy | #349 | Ready | G1 - Firmware-First Determinism |
| L8 Irrigation, Fertilization, and Orchids | #350 | Ready | G3 - Planner, Irrigation, Lab, and Research |
| L9 Lab Notebook, Website, and Publishing | #351 | Ready | G3 - Planner, Irrigation, Lab, and Research |
| L10 Testing and Research | #352 | Ready | G3 - Planner, Irrigation, Lab, and Research |

## Components

- Controller/firmware source and validation: `firmware/`, firmware replay and
  invariant tooling, `verdify_schemas/` drift guards.
- APIs/services: `api/`, `mcp/`, `ingestor/`, `planner_graph/`, `scripts/`;
  current map: `docs/SERVICE_MAP.md`.
- Data/storage contracts: `db/`, migrations, schema dump, TimescaleDB manifests,
  DB backup/PITR manifests, and migration rollback-safety tooling.
- Web/lab surfaces: `site/`, lab publisher components, Grafana dashboards, and
  public lab content generation scripts.
- External systems referenced by code or manifests: ESP32 `192.168.10.111`,
  Home Assistant, Frigate, MQTT/Mosquitto, TimescaleDB/PostgreSQL, Grafana,
  Hermes/OpenAI planner gateway, Slack alerts, Tempest, Open-Meteo, S3-compatible
  lab storage, GHCR, Cloudflare/TLS/edge routes, and NAS-backed storage paths.

This lane owns the app contracts and repo references for those systems, not the
shared providers themselves.

## Non-Goals

`verdify-platform` does not own `verdify-www` marketing, `verdify-crm`, public
DNS/Cloudflare ownership, cluster-wide storage, GPU stack, shared monitoring
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
| `storage-infra` | StorageClass, PV/PVC, backups, NAS/Longhorn/Synology, PITR prerequisites | Request status/actions; do not mutate storage directly. |
| `network-infra` | DNS, routes, ingress, Cloudflare, device VLAN, Frigate/HA path truth | Request named route or firewall work; do not change edge/network state. |
| `monitoring-stack` | Shared metrics, alerts, dashboards, log routing | Request telemetry integration; this repo owns app-local dashboard manifests. |
| Jason | Live greenhouse, prod sync, OTA, credentials, horticulture decisions, outward-facing gates | Explicit approval required. |

## Verification

Use `AGENTS.md` for the full order. For docs-only lane changes, run
`git diff --check`. For runtime changes, use `make lint`, `make test`, and the
area-specific firmware, migration, k8s, lighting, irrigation, or site commands.
