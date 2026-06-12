# Verdify Application Inventory

Last updated: 2026-06-12

Evidence sources: `README.md`, `Makefile`, `.github/workflows/*.yml`,
`deploy/k8s/**`, `pyproject.toml`, `site/package.json`, `planner_graph/pyproject.toml`,
and local `kustomize build` renders. No live Kubernetes namespace was queried
because the assigned namespace was not supplied.

## Repo Structure

| Path | Purpose |
|---|---|
| `api/` | FastAPI crop catalog and compatibility `/setpoints` surface. |
| `ingestor/` | ESP32 data capture, setpoint dispatcher, HA/Tempest/Frigate sync, alerts, planner trigger loops. |
| `mcp/` | FastMCP tool surface used by Iris/Hermes. |
| `planner_graph/` | Standalone LangGraph planner service package and image. |
| `firmware/` | ESPHome YAML, C++ controller logic, native firmware tests and replay corpus. |
| `db/` | Schema, migrations, migration container inputs. |
| `deploy/k8s/` | Kustomize base, overlays, components, ArgoCD app manifests, secret contract docs. |
| `grafana/` | Dashboard source/provisioning assets. |
| `site/` | Quartz static site source/config. |
| `scripts/` | Operational scripts, setpoint server, audits, firmware/deploy helpers. |
| `tests/` and `verdify_schemas/tests/` | Python tests, schema tests, drift guards. |
| `docs/` | Runbooks, architecture docs, handoff state, agent workflow docs. |

## CI/CD Pipelines

| Workflow | Purpose |
|---|---|
| `.github/workflows/ci.yml` | Lint/format, site generated guards, schema/drift tests, device-write gate, firmware compile, firmware logic/replay/invariants, replay diff, tunable readback guard, restart hygiene, migration rollback safety. |
| `.github/workflows/k8s-manifests.yml` | Render and kubeconform-validate base and overlays. |
| `.github/workflows/container-publish.yml` | Build/publish impacted app images to GHCR, pin dev digests, dispatch GitOps promotion. |
| `.github/workflows/reusable-container-build.yml` | In-repo reusable image build/publish workflow. |
| `.github/workflows/prod-promote.yml` | Compute dev-to-prod digest promotion and open prod-promote PR. |
| `.github/workflows/promote-diff-guard.yml` | Ensure prod promote PRs only advance prod digests to current dev digests. |
| `.github/workflows/cnpg-image.yml` | Build/publish TimescaleDB-on-CNPG image. |
| `.github/workflows/lab-content-pipeline.yml` | Lab Quartz build smoke and optional cross-repo lab image rebuild dispatch. |

## Kubernetes Overlays

| Overlay | Namespace | Status | Device posture | Public routes from repo |
|---|---|---|---|---|
| `deploy/k8s/overlays/dev` | `verdify-dev` | Active proving env per `AGENTS.md` | Device-dark: ingestor replicas `0`, denied ESP32 egress, `VERDIFY_DEVICE_WRITE_ENABLED=0` | `api.k3s.verdify.ai`, `api-dev.verdify.ai`, `lab.k3s.verdify.ai`, `lab-dev.verdify.ai`, `graphs.k3s.verdify.ai`, `graphs-dev.verdify.ai` |
| `deploy/k8s/overlays/prod` | `verdify-prod` | Production target shape | Device-write enabled and Jason-gated | `api.verdify.ai`, `graphs.verdify.ai`, `lab.verdify.ai`, `labs.verdify.ai`, `mcp.verdify.ai`, tier-1 wildcard forward |
| `deploy/k8s/overlays/prod-dark` | `verdify-prod` | Legacy/device-dark production adoption shape | Device-dark, no setpoint-server | `lab.verdify.ai` |
| `deploy/k8s/overlays/staging` | `verdify-staging` | Retired/historical per `AGENTS.md` | Device-dark | `verdify.vallery.net`, `verdify-staging.vallery.net`, `api.verdify.ai` |

## Namespace Objects From Rendered Overlays

`verdify-dev` renders:

- Deployments: `verdify-api`, `verdify-grafana`, `verdify-ingestor`,
  `verdify-lab`, `verdify-mcp`, `verdify-planner`.
- StatefulSets: `verdify-db`.
- Jobs/CronJobs: `verdify-migrate`, `verdify-band-curve-refresh`,
  `verdify-db-restore-from-prod`.
- Services: `verdify-api`, `verdify-db`, `verdify-grafana`, `verdify-lab`,
  `verdify-mcp`, `verdify-planner`.
- PVCs: `verdify-db-dumps-prod-ro`.
- ConfigMaps: `verdify-config`, Grafana provisioning/dashboards.
- NetworkPolicies: default deny, API/MCP/DB/Grafana allowances, restore allow,
  web ingress allow, `deny-esp32-egress`.
- IngressRoutes/Middleware: dev API/lab/graphs routes and identity-header strip.

`verdify-prod` renders:

- Deployments: `verdify-api`, `verdify-db-backup-exporter`,
  `verdify-grafana`, `verdify-hermes-iris`, `verdify-ingestor`,
  `verdify-lab`, `verdify-mcp`, `verdify-mqtt`, `verdify-planner`,
  `verdify-setpoint-server`, `verdify-traefik`.
- StatefulSets: `verdify-db`.
- Jobs/CronJobs: `verdify-migrate`, `verdify-band-curve-refresh`,
  `verdify-db-backup`, `verdify-db-watchdog`, `verdify-ha-gap-backfill`.
- Services: `verdify-api`, `verdify-db`, `verdify-grafana`,
  `verdify-hermes-iris`, `verdify-lab`, `verdify-mcp`, `verdify-mqtt`,
  `verdify-planner`, `verdify-setpoint-server`, `verdify-traefik`.
- PVCs: `verdify-db-dumps`, `verdify-hermes-iris-data`,
  `verdify-ingestor-state`.
- ConfigMaps: `verdify-config`, Grafana provisioning/dashboards,
  `verdify-ha-gap-backfill-script`, `verdify-hermes-iris-config`,
  `verdify-ingestor-gather-script`, `verdify-mqtt-config`.
- RBAC: namespace-local ingestor lease Role/RoleBinding and db-watchdog
  Role/RoleBinding; `verdify-traefik` ClusterRole/ClusterRoleBinding is
  platform-scope and should be treated as out-of-lane for namespace-scoped agents.
- NetworkPolicies: default deny, DB/app/service allowances, MQTT fan-out,
  metrics scrape, prod ESP32 egress allow, tier-2 Traefik policies.
- IngressRoutes/Middleware: prod API, graphs, lab, MCP, tier-1 forward, identity
  header strip.

Secrets referenced by rendered workloads:

- `verdify-app-secrets`
- `verdify-ha-token`
- `verdify-hermes`
- `verdify-hermes-slack`
- `verdify-grafana-secrets`
- `ghcr-jvallery-readonly`

Kustomize placeholders are local-config only and render zero `kind: Secret`
objects. See `SECRETS_AUDIT.md` and `deploy/k8s/SECRETS.md` for the detailed
Secret schema and delivery model.

## Container Map

| Workload | Container/image | Service/port | Owner | Health check | Storage | Config dependencies | Secret dependencies |
|---|---|---|---|---|---|---|---|
| `verdify-api` | `ghcr.io/verdifyconsultancy/verdify-api` | `verdify-api:8080` | app/api | startup/readiness `GET /health`; liveness TCP `:8080` | `emptyDir /tmp` | `verdify-config` DB and API env | `verdify-app-secrets` keys `POSTGRES_PASSWORD`, `VERDIFY_WRITE_API_KEY` |
| `verdify-mcp` | `ghcr.io/verdifyconsultancy/verdify-mcp` | `verdify-mcp:8000` | app/genai | TCP probes on `:8000` | `emptyDir /tmp` | `verdify-config` MCP/DB env | `verdify-app-secrets` key `POSTGRES_PASSWORD` |
| `verdify-ingestor` | `ghcr.io/verdifyconsultancy/verdify-ingestor` | no Service; connect-out worker | app/ingestor | no HTTP probe in base | prod PVC `verdify-ingestor-state`, `emptyDir /tmp`, HA token volume | `verdify-config`, `verdify-ingestor-gather-script` in prod | `verdify-app-secrets`, `verdify-ha-token`, prod `verdify-hermes` for `HERMES_IRIS_API_KEY` |
| `verdify-db` | `timescale/timescaledb:2.25.2-pg16` | `verdify-db:5432` | app/data | `pg_isready` readiness/liveness | StatefulSet volume claim template; prod storage patched to Longhorn NVMe RWO | none beyond PostgreSQL env | `verdify-app-secrets` key `POSTGRES_PASSWORD` |
| `verdify-migrate` | `ghcr.io/verdifyconsultancy/verdify-migrate` | Job only | app/db | Job completion | `emptyDir /tmp` | `verdify-config` DB env | `verdify-app-secrets` key `POSTGRES_PASSWORD` |
| `verdify-planner` | `ghcr.io/verdifyconsultancy/verdify-planner` | `verdify-planner:8080` | app/planner | `GET /health` liveness/readiness | `emptyDir /tmp` | `verdify-config` DB/app env | `verdify-app-secrets` keys `POSTGRES_PASSWORD`, optional `OPENAI_API_KEY` |
| `verdify-lab` | `ghcr.io/verdifyconsultancy/verdify-lab` | `verdify-lab:8080` | app/web | `GET /` liveness/readiness | `emptyDir` nginx scratch | image-baked static site | image pull secret only |
| `verdify-grafana` | `grafana/grafana-oss:11.6.0`, renderer `grafana/grafana-image-renderer:3.12.6` | `verdify-grafana:3000` | app/observability | Grafana HTTP probes | `emptyDir`, dashboard/provisioning ConfigMaps | Grafana ConfigMaps | `verdify-grafana-secrets`, `verdify-app-secrets` |
| `verdify-setpoint-server` | `ghcr.io/verdifyconsultancy/verdify-setpoint-server` | `verdify-setpoint-server:8200` | app/device-adjacent | `GET /health` liveness/readiness | `emptyDir /tmp`, HA token volume | `verdify-config` DB/HA env | `verdify-app-secrets`, `verdify-ha-token` |
| `verdify-mqtt` | `eclipse-mosquitto:2` | `verdify-mqtt:1883` | app/fanout | TCP socket probes | `emptyDir`/container data path | `verdify-mqtt-config` | none in rendered component |
| `verdify-hermes-iris` | `nousresearch/hermes-agent@sha256:a711...` | `verdify-hermes-iris:8642` | app/planner-gateway | HTTP/TCP gateway probes in component | PVC `verdify-hermes-iris-data`, Slack config volume | `verdify-hermes-iris-config` | `verdify-hermes`, optional `verdify-hermes-slack` |
| `verdify-db-backup` | `timescale/timescaledb:2.25.2-pg16` | CronJob only | app/data | CronJob success | PVC `verdify-db-dumps` | `verdify-config` DB env | `verdify-app-secrets` key `POSTGRES_PASSWORD` |
| `verdify-ha-gap-backfill` | `ghcr.io/verdifyconsultancy/verdify-ingestor` | CronJob only | app/ingestor | CronJob success | script ConfigMap, HA token volume | `verdify-ha-gap-backfill-script`, `verdify-config` | `verdify-app-secrets`, `verdify-ha-token` |

## External Dependencies Used By The App

| Dependency | Used by | Lane status |
|---|---|---|
| ESP32 at `192.168.10.111:6053` | prod ingestor native API writer | Jason/device/network gated |
| Home Assistant at `192.168.30.107:8123` | ingestor telemetry tasks, setpoint-server, HA gap backfill | App consumes token; HA itself is out of lane |
| MQTT broker/fan-out | ingestor, `verdify-mqtt` | App owns namespace component; cross-namespace access needs platform coordination |
| Frigate URL in config | ingestor occupancy/camera path | External service out of lane |
| OpenAI/Hermes | planner and Hermes gateway | App consumes Secret refs; account/key management out of lane |
| Slack | alerts/Hermes notifications | App consumes config/Secret refs; workspace/app ownership out of lane |
| GHCR | image pull and publish | Repo CI owns image refs; org/package access is platform/repo-admin lane |
| Shared Traefik/Cloudflare/DNS | public routes | Out of lane; request via Network Infra/Orbit |
| StorageClass/PV backends | DB, dumps, state PVCs | Out of lane; request via Storage Infra/Orbit |
