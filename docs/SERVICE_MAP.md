# Verdify Service Map

Last updated: 2026-06-13

This is the current k3s-era service map for the `verdify-platform` lane. Treat
`deploy/k8s/`, `AGENTS.md`, `README.md`, and `docs/AGENT_STATE.md` as the
current orientation surfaces. Older VM-era architecture docs remain useful for
history and operational context, but may describe services that have since moved
or been retired.

## Environments

| Environment | ArgoCD app | Namespace | Desired state | Sync model | Device posture |
|---|---|---|---|---|---|
| Dev | `verdify-dev` | `verdify-dev` | `deploy/k8s/overlays/dev` | Automated | Device-dark: ingestor replicas `0`, ESP32 egress denied, nightly restored copy of prod DB. |
| Prod | `verdify-prod-dark` | `verdify-prod` | `deploy/k8s/overlays/prod` | Manual, behind device-write gate | Single live writer; `VERDIFY_DEVICE_WRITE_ENABLED=1`; prod sync requires Jason. |
| Prod rename target | `verdify-prod` | `verdify-prod` | `deploy/k8s/overlays/prod` | Manual | Intended replacement name for the legacy `verdify-prod-dark` app. |
| Staging | Historical | `verdify-staging` | `deploy/k8s/overlays/staging` | Retired | Retired dead weight pending removal; do not add new work here. |

ArgoCD application manifests live in `deploy/k8s/argocd/apps/`. Promotion and
operator commands live in `docs/runbooks/laptop-operator.md`.

## Traffic

Prod public HTTP enters Cloudflare/tier-1 apps Traefik, then the prod tier-2
`verdify-traefik` in `deploy/k8s/overlays/prod/traefik/`.

| Host | Route manifest | Target service |
|---|---|---|
| `lab.verdify.ai`, `labs.verdify.ai` | `deploy/k8s/overlays/prod/traefik/verdify-traefik-routes.yaml` | `verdify-lab:8080` |
| `api.verdify.ai` | `deploy/k8s/overlays/prod/traefik/verdify-traefik-routes.yaml` | `verdify-api:8080` |
| `mcp.verdify.ai` | `deploy/k8s/overlays/prod/traefik/verdify-traefik-routes.yaml` | `verdify-mcp:8000` |
| `graphs.verdify.ai` | `deploy/k8s/components/grafana/graphs-ingressroute.yaml` | `verdify-grafana:3000` |

Dev routes are direct shared-apps-Traefik routes for `api.k3s.verdify.ai`,
`api-dev.verdify.ai`, `lab.k3s.verdify.ai`, `lab-dev.verdify.ai`,
`graphs.k3s.verdify.ai`, and `graphs-dev.verdify.ai`.

## Core Workloads

| Workload | Source and entrypoint | Manifest | Image | Port / surface | Data and external deps |
|---|---|---|---|---|---|
| `verdify-db` | TimescaleDB/PostgreSQL system of record | `deploy/k8s/base/db-statefulset.yaml` | `timescale/timescaledb:2.25.2-pg16` | `5432`, headless ClusterIP | `verdify-app-secrets/POSTGRES_PASSWORD`; PVC `db-data`; schema in `db/schema.sql` and `db/migrations/`. |
| `verdify-migrate` | `db/Dockerfile.migrate`, `/usr/local/bin/migrate.sh` | `deploy/k8s/base/migration-job.yaml` | `ghcr.io/verdifyconsultancy/verdify-migrate` | ArgoCD PreSync Job | Replays `db/schema.sql` plus migration bootstrap against `verdify-db`; not the prod data restore path. |
| `verdify-api` | `api/main.py`, `uvicorn main:app --port 8080` | `deploy/k8s/base/api-deployment.yaml` | `ghcr.io/verdifyconsultancy/verdify-api` | `8080`, ClusterIP; prod `api.verdify.ai` | `verdify-config`; `verdify-app-secrets` keys `POSTGRES_PASSWORD`, `VERDIFY_WRITE_API_KEY`; TimescaleDB. |
| `verdify-mcp` | `mcp/server.py`, FastMCP streamable HTTP | `deploy/k8s/base/mcp-deployment.yaml` | `ghcr.io/verdifyconsultancy/verdify-mcp` | `8000`, ClusterIP; prod `mcp.verdify.ai` | `verdify-config`; `verdify-app-secrets/POSTGRES_PASSWORD`; planner tool surface. |
| `verdify-ingestor` | `ingestor/ingestor.py` | `deploy/k8s/base/ingestor-deployment.yaml` plus prod patches | `ghcr.io/verdifyconsultancy/verdify-ingestor` | No inbound service; connect-out worker | ESP32 `192.168.10.111:6053`, HA `192.168.30.107:8123`, MQTT, TimescaleDB, Open-Meteo, Slack, Hermes. Single-writer invariant: replicas `1`, strategy `Recreate`. |
| `verdify-planner` | `planner_graph/`, `python -m planner_graph.server` | `deploy/k8s/components/planner/planner-deployment.yaml` | `ghcr.io/verdifyconsultancy/verdify-planner` | `8080`, ClusterIP, `/health` | TimescaleDB run store; optional `OPENAI_API_KEY`; reached by Hermes/Iris and cron replan paths. |
| `verdify-setpoint-server` | `scripts/setpoint-server.py` | `deploy/k8s/components/setpoint-server/setpoint-server.yaml` | `ghcr.io/verdifyconsultancy/verdify-setpoint-server` | `8200`, ClusterIP | Prod-only grow-light writer and diagnostics; HA token Secret mount; TimescaleDB. Device-affecting cutover is Jason-gated. |
| `verdify-hermes-iris` | upstream Hermes gateway, args `gateway run` | `deploy/k8s/components/hermes-iris/hermes-iris.yaml` | `nousresearch/hermes-agent@sha256:...` | `8642`, ClusterIP | Secret `verdify-hermes`, optional `verdify-hermes-slack`, PVC `verdify-hermes-iris-data`, MCP URL to `verdify-mcp`. |
| `verdify-mqtt` | Mosquitto fan-out broker | `deploy/k8s/components/mqtt-broker/mqtt-broker.yaml` | `eclipse-mosquitto:2` | `1883`, ClusterIP | In-cluster telemetry fan-out; separate from HAOS/Sentinel broker; no persistence. |
| `verdify-lab` | static Quartz site image | `deploy/k8s/components/lab-site/lab-site.yaml` | `ghcr.io/verdifyconsultancy/verdify-lab` | `8080`, ClusterIP; prod lab hosts | Public research site. Runtime image is built from the separate `verdify-site` repo; this repo pins/deploys it. |
| `verdify-grafana` | Grafana plus image-renderer sidecar | `deploy/k8s/components/grafana/grafana.yaml` | `grafana/grafana-oss:11.6.0`, `grafana/grafana-image-renderer:3.12.6` | `3000`, ClusterIP; prod `graphs.verdify.ai` | Provisioned dashboards/config from git; TimescaleDB datasource; `verdify-grafana-secrets` admin password optional in render. |

App Secret contracts are documented by name and key in `deploy/k8s/SECRETS.md`.
Do not print or commit raw secret values.

## Operational Components

| Component | Manifest | Current wiring | Notes |
|---|---|---|---|
| Prod DB backup | `deploy/k8s/components/db-backup/backup-cronjob.yaml` | Prod overlay | Nightly `pg_dump -Fc` to `verdify-db-dumps` NFS PVC; read-only against `verdify-db`. |
| Backup freshness exporter | `deploy/k8s/components/db-backup/backup-freshness-exporter.yaml` | Prod overlay | Exposes backup RPO metrics on `:8080/metrics` for observability scrape. |
| Dev DB restore | `deploy/k8s/overlays/dev/db-restore-from-prod.yaml` | Dev overlay | Nightly prod dump restore into dev; wipes dev-written plans and rows by design. |
| DB watchdog | `deploy/k8s/overlays/prod/db-watchdog.yaml` | Prod overlay | Narrow CronJob that can delete only `verdify-db-0` after a specific DB mount/config CrashLoop signature. |
| HA gap backfill | `deploy/k8s/components/ha-gap-backfill/ha-gap-backfill-cronjob.yaml` | Prod overlay | Hourly HA recorder gap reconciliation; writes missing telemetry rows only. |
| Gather script mount | `deploy/k8s/components/ingestor-gather-script/` | Prod overlay | ConfigMap delivery for `scripts/gather-plan-context.sh` into the ingestor image. |
| Grafana band curve refresh | `deploy/k8s/components/grafana/band-curve-refresh-cronjob.yaml` | Dev and prod overlays with Grafana | Refreshes `mv_band_curve` every 10 minutes. |
| Firmware twin | `deploy/k8s/components/firmware-twin/` | Component only; not referenced by current dev/prod overlays | Read-only shadow path with live-prod schema/user gates. |
| Umami analytics | `deploy/k8s/components/umami/` | Component only; not referenced by current dev/prod overlays | Residual analytics tier, explicitly not wired into prod yet. |

## Storage Touchpoints

This section is a service-level inventory, not a complete SQL lineage parser.
For authoritative schema details, use `db/schema.sql`, `db/migrations/`, and
the drift guards in `verdify_schemas/tests/`.

| Service | Primary tables, views, and functions |
|---|---|
| API | Crop and topology tables/views: `greenhouses`, `crops`, `crop_events`, `observations`, `harvests`, `zones`, `shelves`, `positions`, `crop_catalog`, `equipment`, `sensors`, `switches`, `v_position_current`, `v_zone_full`, `v_crop_lifecycle`, `v_crop_catalog_with_profiles`; public/status surfaces: `climate`, `equipment_state`, `system_state`, `alert_log`, `plan_journal`, `planner_lessons`, `public_contact_submissions`, `v_data_pipeline_health`, `v_data_trust_ledger`, `v_planner_trigger_health`, `fn_planner_scorecard`, `fn_band_trace`, `fn_lighting_policy`, `fn_lighting_minutes_policy`. |
| MCP | Planner/control state: `climate`, `system_state`, `equipment_state`, `weather_forecast`, `setpoint_changes`, `setpoint_plan`, `plan_delivery_log`, `plan_journal`, `planner_lessons`, `planner_trigger_ledger`, `v_active_plan`, `v_plan_guardrail_scorecard`, `fn_planner_scorecard`. |
| Ingestor | Live telemetry/control write path: `climate`, `equipment_state`, `system_state`, `diagnostics`, `daily_summary`, `setpoint_changes`, `setpoint_snapshot`, `weather_forecast`, `alert_log`, planner delivery tables, HA/energy/hydro/weather-derived columns. Entity routing starts in `ingestor/entity_map.py`. |
| Planner graph | Planner run and memory state: `planner_graph_runs`, `planner_memory_items`, `planner_memory_embeddings`, `planner_memory_retrievals`, `plan_delivery_log`, `climate`, `weather_forecast`, `plan_journal`, `setpoint_plan`, `alert_log`, `setpoint_clamps`, `setpoint_snapshot`, `fn_planner_scorecard`. |
| Setpoint server | Lighting and setpoint diagnostics: `equipment_state`, `setpoint_changes`, `setpoint_plan`, lighting policy functions, HA service calls for grow-light circuits. |
| Grafana | Read-only dashboard datasource over TimescaleDB; dashboards depend on public views/functions including `fn_band_trace`, `fn_lighting_timeline`, `v_data_pipeline_health`, `v_planner_trigger_health`, `mv_band_curve`, and site dashboard JSON under `grafana/` plus generated ConfigMaps. |
| HA gap backfill | `setpoint_snapshot`, `equipment_state`, `system_state` and HA recorder-derived telemetry windows. |
| Firmware twin | `twin_decisions` through INSERT-only `twin`/`twin_ro` role when the component is enabled. |

## External Dependencies

| Dependency | Used by | Contract |
|---|---|---|
| ESP32 greenhouse controller `192.168.10.111:6053` | Ingestor, firmware validation | ESPHome native API with Noise PSK; live device writes are gated. |
| Home Assistant `192.168.30.107:8123` | Ingestor, setpoint server, HA backfill | Token from `verdify-ha-token`; service calls can be device-affecting. |
| MQTT | Ingestor, fan-out broker | HAOS/Sentinel broker plus in-cluster `verdify-mqtt`; telemetry fan-out is not a device control path. |
| OpenAI API | Planner, Hermes | Optional for planner manifest render, required for LLM-backed production planning. |
| Open-Meteo | Ingestor forecast sync | Hourly weather forecast source. |
| Slack | Ingestor alerts, Hermes | Optional Slack config/secret references; alert emission only. |
| GHCR | All app images | `ghcr-jvallery-readonly` pull secret; image digests are pinned by overlays/CI. |
| NAS / storage platform | DB dumps, PVs/PVCs, backups | StorageClass/PV provision belongs to `storage-infra`/Jason, not this repo. |
| Cloudflare / shared Traefik / DNS | Public hosts | Edge and DNS ownership is outside this lane; this repo owns app route manifests. |

## Verification Pointers

Use `AGENTS.md` for the full command order.

| Change type | Commands |
|---|---|
| Docs-only | `git diff --check` |
| Python/runtime | `make lint`, then `make test` |
| Schema or migration | `make migration-rollback-safety`, plus the targeted rollback proof |
| Firmware | `make test-firmware`, `make firmware-invariants`, `make firmware-replay OLD=<base> NEW=HEAD`, `make firmware-check` |
| Site/UI | Relevant `Makefile` or `site/package.json` command plus local render check |
| Kustomize shape | CI validates rendered manifests; local candidate checks are `kubectl kustomize deploy/k8s/overlays/dev` and `kubectl kustomize deploy/k8s/overlays/prod` when `kubectl` has the needed plugins/CRDs available. |
