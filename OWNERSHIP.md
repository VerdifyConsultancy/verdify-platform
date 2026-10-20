# OWNERSHIP: VerdifyConsultancy/verdify-platform

This file is the detailed ownership record for everything the **Verdify greenhouse product** lane owns: namespaces `verdify-prod`, `verdify-descheduler` and `verdify-db-rehearsal`, the lab/labs/api/mcp/graphs.verdify.ai routes and the tier-1 `*.verdify.ai` HostRegexp, the `verdify-*` PriorityClasses, product Grafana, the ESP32 greenhouse controller and its probes, the product images, database and firmware.

- Central record (one owner per item, R1): [ESTATE-INVENTORY.md](https://github.com/jvallery/agents/blob/main/docs/fleet-inventory/ESTATE-INVENTORY.md), rendered from `registry/policy/estate-ownership` in jvallery/agents. Lane map and rules R1 to R22: [docs/estate/README.md](https://github.com/jvallery/agents/blob/main/docs/estate/README.md).
- How to update: change the central entry in jvallery/agents and this file in the same change set (a companion PR pair, R1 exception (a)). Where the central record and this file disagree, the central record wins.
- Facts with their own authoritative doc are linked, not copied (R22): device IPs and VLANs → [network-infra `ipam/allocation.md`](https://github.com/jvallery/network-infra/blob/main/ipam/allocation.md); hardware rows → [proxmox-infrastructure `inventory/fleet-hardware.md`](https://github.com/jvallery/proxmox-infrastructure/blob/main/inventory/fleet-hardware.md); the NAS share behind `verdify-db-dumps` → [storage-infra `inventory/obsidian-data-inventory.md`](https://github.com/jvallery/storage-infra/blob/main/inventory/obsidian-data-inventory.md); Longhorn disks → storage-infra `inventory/longhorn-v1-disks.yml`; Secret names and keys → [`deploy/k8s/SECRETS.md`](deploy/k8s/SECRETS.md).
- Snapshot: live readback of cluster `vallery` on 2026-10-19; source at verdify-platform `origin/main` 7e86ca1. Secret values were never read; only names appear here.

Health legend: P = probes, A = alerts, M = metrics, D = dashboards. `[VNS]` = the six namespace-level Verdify alerts (VerdifyIngestBacklogHigh, VerdifyIngestBacklogStale, VerdifyIngestDiskHeadroomLow, VerdifyIngestSpoolHealthUnknown, VerdifyWriterLeaseObservationUnknown, VerdifyWriterLeaseUnsafe); `[ARGO]` = ArgoCDAppMetricsAbsent, ArgoCDApplicationDegraded, ArgoCDApplicationHealthUnknown, ArgoCDApplicationOutOfSync; `[LH]` = LonghornProtectedVolumeBackupStale, LonghornVolumeDetachedTooLong, LonghornVolumeNotHealthy; `[EP]` = the namespace-level endpoint probes for api, graphs, lab, labs, mcp and the tier-1 hosts. **none** marks a coverage gap. Action codes A1 to A15 refer to [Open actions](#open-actions); D-xx codes refer to [Decisions applied](#decisions-applied-2026-10-19).

## Summary

| Kind | Items | Status `owned` | Other status |
|---|---:|---:|---:|
| argo-app | 4 | 3 | 1 |
| cluster-object | 13 | 9 | 4 |
| cluster-scoped | 3 | 3 | 0 |
| contribution | 1 | 1 | 0 |
| endpoint | 5 | 5 | 0 |
| k8s-workload | 33 | 29 | 4 |
| namespace | 4 | 1 | 3 |
| other | 4 | 2 | 2 |
| physical-device | 4 | 4 | 0 |
| pvc | 17 | 15 | 2 |
| repo | 4 | 1 | 3 |
| route | 6 | 6 | 0 |
| scheduled-job | 1 | 0 | 1 |
| secret | 2 | 2 | 0 |
| **total** | **101** | **81** | **20** |

Record status: owned 81, retire 11, adopt 6, resolve-conflict 3. The Status column in every table below is the central record's status; the text after it is this repo's note or action.

Live Kubernetes objects attributed to this repo: 474 declared, 252 residue-delete, 11 system, 4 hand-applied-adopt, 3 generated-by-owned-component, 3 hand-applied, 1 conflict-needs-decision (748 total). Action objects: 257 (see [Open actions](#open-actions)).

## Namespaces

| ID | Name | Where | What | State | Source of truth | Health | Critical deps | Status (record) / action |
|---|---|---|---|---|---|---|---|---|
| `namespace:verdify-db-rehearsal` | verdify-db-rehearsal | k3s vallery / namespace verdify-db-rehearsal | Kubernetes namespace verdify-db-rehearsal. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/cnpg/rehearsal/namespace@main | **none** | none recorded | `adopt`; D-04 approved 2026-10-19: experiment namespace owned by this repo (standing rule); label disposable with an expiry, add to the lane table, retire when S2 closes (A4) |
| `namespace:verdify-descheduler` | verdify-descheduler | k3s vallery / namespace verdify-descheduler | Kubernetes namespace verdify-descheduler. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/descheduler/namespace.yaml | P: none; A: none; M: `OBSERVABILITY_REGISTRY namespace:verdify-descheduler tier 1 owner VerdifyConsultancy/platform`, `observability-registry:namespace:verdify-descheduler (owner VerdifyConsultancy/platform)`; D: none | none recorded | `owned`; adopt Namespace object (hand-applied; excluded from #802 adoption) (A7) |
| `namespace:verdify-prod` | verdify-prod | k3s vallery / namespace verdify-prod | Kubernetes namespace verdify-prod. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod@main | P: none; A: `[VNS]`; M: `OBSERVABILITY_REGISTRY namespace:verdify-prod tier 1 owner VerdifyConsultancy/platform`, `observability-registry:namespace:verdify-prod (owner VerdifyConsultancy/platform)`; D: none | none recorded | `owned` |
| `namespace:verdify-edge` | verdify-edge | k3s vallery | Residual namespace from the retired per-product verdify edge; holds only wildcard-verdify-ai TLS and zot pull secret. | retired-residue | live-only | **none** | none recorded | `retire`; Namespace object is this repo's in the final record; delete it once [agents#4468](https://github.com/jvallery/agents/issues/4468) has removed the jvallery/agents Certificate `wildcard-verdify-ai` and its Secret; the zot pull secret there is agents KSOPS lane per D-03 (approved 2026-10-19) and goes with the namespace (non-data) (A14) |

Adjacent: Namespace `verdify-platform` (agent-sessions namesake, `ci-dbg-*` residue) is owned by [jvallery/agents](https://github.com/jvallery/agents/blob/main/docs/fleet-inventory/estate/jvallery__agents.md) in the final record; this repo only removes its own four dashboard ConfigMaps there (row `contribution:verdify-platform/configmap/verdifyconsultancy/verdify-platform` below, A9). In `verdify-edge` the Certificate `wildcard-verdify-ai`, its CertificateRequest/Order children and Secret `wildcard-verdify-ai-tls` are jvallery/agents' and retire under agents#4468; this repo owns only the Namespace object (retire).

## Argo CD Applications

The Application declarations and AppProjects are jvallery/agents' (R6); this repo owns the source paths they sync and what renders from them.

| ID | Name | Where | What | State | Source of truth | Health | Critical deps | Status (record) / action |
|---|---|---|---|---|---|---|---|---|
| `argo-app:argocd/verdify-db-rehearsal-cluster` | verdify-db-rehearsal-cluster | k3s vallery ns argocd -> dest ns verdify-db-rehearsal | Argo CD Application (project app-verdify-cnpg-rehearsal) syncing VerdifyConsultancy/verdify-platform:deploy/k8s/cnpg/rehearsal/cluster@main into namespace verdify-db-rehearsal. | running | agents:platform/gitops/applications/local-prod/verdify-db-rehearsal-cluster.yaml | P: none; A: `[ARGO]`; M: `argocd_app_info (scrape_job argocd-app-controller)`; D: none | `argo-app:argocd/agent-fleet-local-prod-apps`, `argo-project:argocd/app-verdify-cnpg-rehearsal`, `operator:argocd/argo-cd` | `owned`; OutOfSync; D-04 approved 2026-10-19: experiment namespace owned here, retires when S2 closes (A4) |
| `argo-app:argocd/verdify-db-rehearsal-namespace` | verdify-db-rehearsal-namespace | k3s vallery ns argocd -> dest ns verdify-db-rehearsal | Argo CD Application (project app-verdify-cnpg-rehearsal) syncing VerdifyConsultancy/verdify-platform:deploy/k8s/cnpg/rehearsal/namespace@main into namespace verdify-db-rehearsal. | running | agents:platform/gitops/applications/local-prod/verdify-db-rehearsal-namespace.yaml | P: none; A: `[ARGO]`; M: `argocd_app_info (scrape_job argocd-app-controller)`; D: none | `argo-app:argocd/agent-fleet-local-prod-apps`, `argo-project:argocd/app-verdify-cnpg-rehearsal`, `operator:argocd/argo-cd` | `owned`; D-04 approved 2026-10-19: retires when S2 closes (A4) |
| `argo-app:argocd/verdify-descheduler` | verdify-descheduler | k3s vallery ns argocd -> dest ns verdify-descheduler | Argo CD Application (project verdify-descheduler) syncing VerdifyConsultancy/verdify-platform:deploy/k8s/descheduler@b523a9b1ffecf0f82f8952763a1c0de207e540b5 into namespace verdify-descheduler. | running | agents:platform/gitops/applications/local-prod/verdify-descheduler.yaml | P: none; A: `[ARGO]`; M: `argocd_app_info (scrape_job argocd-app-controller)`; D: none | `argo-app:argocd/agent-fleet-local-prod-apps`, `argo-project:argocd/verdify-descheduler`, `operator:argocd/argo-cd` | `owned`; (source); SHA-pinned `b523a9b1`, 70 commits behind main; verify pin before delivery |
| `argo-app:argocd/verdify-prod-dark` | verdify-prod-dark | k3s vallery ns argocd -> dest ns verdify-prod | Argo CD Application (project app-test) syncing VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod@main into namespace verdify-prod. | running | verdify-platform:deploy/k8s/argocd/apps/verdify-prod-dark.yaml | P: none; A: `[ARGO]`; M: `argocd_app_info (scrape_job argocd-app-controller)`; D: none | `argo-project:argocd/app-test`, `operator:argocd/argo-cd` | `resolve-conflict`; transition: hand-applied, unparented, AppProject `app-test`; adopt into agents local-prod catalog ([agents#4461](https://github.com/jvallery/agents/issues/4461)) |

## Hostnames and routes

DNS records, tunnel ingress and the shared `traefik-apps` edge are jvallery/network-infra's (R2). This repo owns the backends and the routes in `verdify-prod` (R3).

### Hostnames

| ID | Name | Where | What | State | Source of truth | Health | Critical deps | Status (record) / action |
|---|---|---|---|---|---|---|---|---|
| `endpoint:lab.verdify.ai` | lab.verdify.ai | verdify-prod/verdify-t2-lab | Hostname lab.verdify.ai: backend owner VerdifyConsultancy/verdify-platform, route owner VerdifyConsultancy/verdify-platform, DNS owner jvallery/network-infra (R2). | running | verdify-prod/verdify-t2-lab | P: `OBSERVABILITY_REGISTRY HTTPRouteEndpoint lab.verdify.ai`; A: none; M: none; D: none | `cloudflare-edge-local-staging`, `dns:network-infra`, `traefik-apps` | `owned` |
| `endpoint:labs.verdify.ai` | labs.verdify.ai | verdify-prod/verdify-t2-lab | Hostname labs.verdify.ai: backend owner VerdifyConsultancy/verdify-platform, route owner VerdifyConsultancy/verdify-platform, DNS owner jvallery/network-infra (R2). | running | verdify-prod/verdify-t2-lab | P: `OBSERVABILITY_REGISTRY HTTPRouteEndpoint labs.verdify.ai`; A: none; M: none; D: none | `cloudflare-edge-local-staging`, `dns:network-infra`, `traefik-apps` | `owned` |
| `endpoint:api.verdify.ai` | api.verdify.ai | verdify-prod/verdify-t2-api | Hostname api.verdify.ai: backend owner VerdifyConsultancy/verdify-platform, route owner VerdifyConsultancy/verdify-platform, DNS owner jvallery/network-infra (R2). | running | verdify-prod/verdify-t2-api | P: `OBSERVABILITY_REGISTRY HTTPRouteEndpoint api.verdify.ai`; A: none; M: none; D: none | `cloudflare-edge-local-staging`, `dns:network-infra`, `traefik-apps` | `owned` |
| `endpoint:mcp.verdify.ai` | mcp.verdify.ai | verdify-prod/verdify-t2-mcp | Hostname mcp.verdify.ai: backend owner VerdifyConsultancy/verdify-platform, route owner VerdifyConsultancy/verdify-platform, DNS owner jvallery/network-infra (R2). | running | verdify-prod/verdify-t2-mcp | P: `OBSERVABILITY_REGISTRY HTTPRouteEndpoint mcp.verdify.ai`; A: none; M: none; D: none | `cloudflare-edge-local-staging`, `dns:network-infra`, `traefik-apps` | `owned` |
| `endpoint:graphs.verdify.ai` | graphs.verdify.ai | verdify-prod/verdify-t2-graphs | Hostname graphs.verdify.ai: backend owner VerdifyConsultancy/verdify-platform, route owner VerdifyConsultancy/verdify-platform, DNS owner jvallery/network-infra (R2). | running | verdify-prod/verdify-t2-graphs | P: `OBSERVABILITY_REGISTRY HTTPRouteEndpoint graphs.verdify.ai`; A: none; M: none; D: none | `cloudflare-edge-local-staging`, `dns:network-infra`, `traefik-apps` | `owned` |

### Routes, middleware and tier-2 Traefik

| ID | Name | Where | What | State | Source of truth | Health | Critical deps | Status (record) / action |
|---|---|---|---|---|---|---|---|---|
| `route:verdify-prod/verdify-t2-api` | verdify-t2-api | k8s ns verdify-prod (verdify-traefik (verdify-prod)) | Traefik IngressRoute (verdify-web) for api.verdify.ai -> verdify-prod/verdify-api:8080 [ClusterIP ready=2]; TLS none (HTTP or tunnel-terminated) | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod/traefik/verdify-traefik-routes.yaml | P: 4 (`api.verdify.ai:endpoint-prober:api-verdify-ai-health(blackbox-verdify-public)`, `api.verdify.ai:tls:blackbox-verdify-local/edge_vip_host_api_verdify_ai`, ...); A: `EdgeHostProbeDown`, `EndpointProbeCellDown`, `TLSCertificateExpiringSoon`, `TraefikBackend5xxRatioHigh`; M: `probe_success`, `traefik_service_requests_total`; D: none | `dns:api.verdify.ai`, `k8s-service:verdify-prod/verdify-api:8080`, `k8s-workload:traefik-apps/daemonset/traefik-apps`, `middleware:verdify-prod/verdify-strip-identity-headers`, `tlsstore:traefik-apps/default` | `owned` |
| `route:verdify-prod/verdify-t2-graphs` | verdify-t2-graphs | k8s ns verdify-prod (verdify-traefik (verdify-prod)) | Traefik IngressRoute (verdify-web) for graphs.verdify.ai -> verdify-prod/verdify-grafana-render-cache:8080 [ClusterIP ready=2]; verdify-prod/verdify-grafana:3000 [ClusterIP ready=1]; TLS none (HTTP or tunnel-terminated) | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod | P: 4 (`endpoint-prober:graphs.verdify.ai`, `graphs.verdify.ai:endpoint-prober:graphs-verdify-ai(blackbox-verdify-public)`, ...); A: `EdgeHostProbeDown`, `EndpointProbeCellDown`, `TLSCertificateExpiringSoon`, `TraefikBackend5xxRatioHigh`; M: `probe_success`, `traefik_service_requests_total`; D: none | `dns:graphs.verdify.ai`, `k8s-service:verdify-prod/verdify-grafana-render-cache:8080`, `k8s-service:verdify-prod/verdify-grafana:3000`, `k8s-workload:traefik-apps/daemonset/traefik-apps`, `middleware:verdify-prod/verdify-strip-identity-headers`, `tlsstore:traefik-apps/default` | `owned` |
| `route:verdify-prod/verdify-t2-lab` | verdify-t2-lab | k8s ns verdify-prod (verdify-traefik (verdify-prod)) | Traefik IngressRoute (verdify-web) for lab.verdify.ai, labs.verdify.ai -> verdify-prod/verdify-lab:8080 [ClusterIP ready=2]; TLS none (HTTP or tunnel-terminated) | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod/traefik/verdify-traefik-routes.yaml | P: 7 (`endpoint-prober:lab.verdify.ai`, `lab.verdify.ai:endpoint-prober:lab-verdify-ai(blackbox-verdify-public)`, ...); A: `EdgeHostProbeDown`, `EndpointProbeCellDown`, `TLSCertificateExpiringSoon`, `TraefikBackend5xxRatioHigh`; M: `probe_success`, `traefik_service_requests_total`; D: none | `dns:lab.verdify.ai`, `dns:labs.verdify.ai`, `k8s-service:verdify-prod/verdify-lab:8080`, `k8s-workload:traefik-apps/daemonset/traefik-apps`, `middleware:verdify-prod/verdify-strip-identity-headers`, `tlsstore:traefik-apps/default` | `owned` |
| `route:verdify-prod/verdify-t2-mcp` | verdify-t2-mcp | k8s ns verdify-prod (verdify-traefik (verdify-prod)) | Traefik IngressRoute (verdify-web) for mcp.verdify.ai -> verdify-prod/verdify-mcp:8000 [ClusterIP ready=2]; TLS none (HTTP or tunnel-terminated) | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod/traefik/verdify-traefik-routes.yaml | P: 4 (`endpoint-prober:mcp.verdify.ai`, `mcp.verdify.ai:endpoint-prober:mcp-verdify-ai-mcp(blackbox-verdify-public)`, ...); A: `EdgeHostProbeDown`, `EndpointProbeCellDown`, `TLSCertificateExpiringSoon`, `TraefikBackend5xxRatioHigh`; M: `probe_success`, `traefik_service_requests_total`; D: none | `dns:mcp.verdify.ai`, `k8s-service:verdify-prod/verdify-mcp:8000`, `k8s-workload:traefik-apps/daemonset/traefik-apps`, `middleware:verdify-prod/verdify-strip-identity-headers`, `tlsstore:traefik-apps/default` | `owned` |
| `route:verdify-prod/verdify-tier1-forward` | verdify-tier1-forward | k8s ns verdify-prod (traefik-apps, VIP <IP: ipam>) | Traefik IngressRoute (websecure,web) for analytics.verdify.ai, auth.verdify.ai, botauth.verdify.ai, crm.verdify.ai, logs.verdify.ai, mqtt.verdify.ai, proposals-hello.verdify.ai, traefik.verdify.ai, www-dev.verdify.ai, ww | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod/traefik/verdify-tier1-apps-forward.yaml | P: 11 (`analytics.verdify.ai:endpoint-prober:analytics-verdify-ai(blackbox-route-coverage)`, `analytics.verdify.ai:tls:blackbox-route-coverage/edge_ok404`, ...); A: `EdgeHostProbeDown`, `EndpointProbeCellDown`, `TLSCertificateExpiringSoon`, `TraefikBackend5xxRatioHigh`; M: `probe_success`, `traefik_service_requests_total`; D: none | `dns:analytics.verdify.ai`, `dns:auth.verdify.ai`, `dns:botauth.verdify.ai`, `dns:crm.verdify.ai`, `dns:logs.verdify.ai`, `dns:mqtt.verdify.ai`, `k8s-service:verdify-prod/verdify-traefik:80`, `k8s-workload:traefik-apps/daemonset/traefik-apps`, `tlsstore:traefik-apps/default` | `owned` |
| `middleware:verdify-prod/aggregate` | verdify-prod trivial middlewares (1) | k8s ns verdify-prod | Traefik headers/redirect/rateLimit/ipAllowList middlewares: verdify-strip-identity-headers(headers) | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod | **none** | none recorded | `owned` |
| `k8s-workload:verdify-prod/deployment/verdify-traefik` | verdify-traefik | k8s ns verdify-prod (2 replicas) | Second, app-scoped Traefik (kubernetescrd provider limited to ns verdify-prod, label traefik-tier=verdify, entrypoint verdify-web :8000) serving api/graphs/lab/mcp.verdify.ai behind verdify-tier1-forward | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod | P: ns endpoint probes (7, `[EP]`); A: `[VNS]`; M: none; D: none | `route:verdify-prod/verdify-tier1-forward` | `owned`; image `docker.io/traefik:v3.7.1` not digest-pinned (A12) |
| `endpoint:tier1.verdify.ai-hostregexp` | tier1.verdify.ai-hostregexp | verdify-prod/verdify-tier1-forward (analytics, botauth, logs, mqtt, traefik .verdify.ai) | Hostname tier1.verdify.ai-hostregexp: backend owner VerdifyConsultancy/verdify-platform, route owner VerdifyConsultancy/verdify-platform, DNS owner jvallery/network-infra (R2). | running | verdify-prod/verdify-tier1-forward (analytics, botauth, logs, mqtt, traefik .verdify.ai) | P: `OBSERVABILITY_REGISTRY HTTPRouteEndpoint tier1.verdify.ai-hostregexp`; A: none; M: none; D: none | `cloudflare-edge-local-staging`, `dns:network-infra`, `traefik-apps` | `owned` |

## Workloads

### verdify-prod Deployments and StatefulSet

| ID | Name | Where | What | State | Source of truth | Health | Critical deps | Status (record) / action |
|---|---|---|---|---|---|---|---|---|
| `k8s-workload:verdify-prod/deployment/experiment-v2-freezer` | experiment-v2-freezer | k3s vallery / verdify-prod | Deployment experiment-v2-freezer in verdify-prod running verdify-experiment-v2-orchestrator. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod@main | P: ns endpoint probes (7, `[EP]`); A: none; M: none; D: none | `registry:registry.vallery.net (zot registry-origin)`, `secret:verdify-prod/verdify-experiment-v2-outcome-freezer-db`, `secret:verdify-prod/zot-origin-cluster-pull` | `owned` |
| `k8s-workload:verdify-prod/deployment/experiment-v2-lifecycle` | experiment-v2-lifecycle | k3s vallery / verdify-prod | Deployment experiment-v2-lifecycle in verdify-prod running verdify-experiment-v2-orchestrator. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod@main | P: ns endpoint probes (7, `[EP]`); A: none; M: none; D: none | `registry:registry.vallery.net (zot registry-origin)`, `secret:verdify-prod/verdify-experiment-v2-shadow-scheduler-db`, `secret:verdify-prod/zot-origin-cluster-pull` | `owned` |
| `k8s-workload:verdify-prod/deployment/experiment-v2-selector` | experiment-v2-selector | k3s vallery / verdify-prod | Deployment experiment-v2-selector in verdify-prod running verdify-experiment-v2-orchestrator. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod@main | P: ns endpoint probes (7, `[EP]`); A: none; M: none; D: none | `registry:registry.vallery.net (zot registry-origin)`, `secret:verdify-prod/verdify-experiment-v2-randomizer-db`, `secret:verdify-prod/verdify-hermes`, `secret:verdify-prod/zot-origin-cluster-pull` | `owned` |
| `k8s-workload:verdify-prod/deployment/verdify-api` | verdify-api | k3s vallery / verdify-prod | Deployment verdify-api in verdify-prod running timescaledb:2.25.2-pg16, verdify-api. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod@main | P: ns endpoint probes (7, `[EP]`); A: `[VNS]`; M: none; D: none | `registry:registry.vallery.net (zot registry-origin)`, `secret:verdify-prod/ghcr-jvallery-readonly`, `secret:verdify-prod/verdify-app-secrets`, `secret:verdify-prod/verdify-frigate-vision-auth`, `secret:verdify-prod/zot-origin-cluster-pull` | `owned`; tag-only image(s) (A12) |
| `k8s-workload:verdify-prod/deployment/verdify-db-backup-exporter` | verdify-db-backup-exporter | k3s vallery / verdify-prod | Deployment verdify-db-backup-exporter in verdify-prod running python:3.12-alpine. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod@main | P: ns endpoint probes (7, `[EP]`); A: `[VNS]`; M: none; D: none | `pvc:verdify-prod/verdify-db-dumps` | `owned`; tag-only image(s) (A12) |
| `k8s-workload:verdify-prod/deployment/verdify-grafana` | verdify-grafana | k3s vallery / verdify-prod | Deployment verdify-grafana in verdify-prod running grafana-image-renderer:v5.10.0, grafana:12.4.5. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod@main | P: ns endpoint probes (7, `[EP]`); A: `[VNS]`; M: none; D: none | `pvc:verdify-prod/verdify-grafana-data`, `secret:verdify-prod/verdify-grafana-oauth`, `secret:verdify-prod/verdify-grafana-runtime-db`, `secret:verdify-prod/verdify-grafana-secrets` | `owned` |
| `k8s-workload:verdify-prod/deployment/verdify-grafana-render-cache` | verdify-grafana-render-cache | k3s vallery / verdify-prod | Deployment verdify-grafana-render-cache in verdify-prod running nginx-unprivileged:1.29-alpine. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod@main | P: ns endpoint probes (7, `[EP]`); A: `[VNS]`; M: none; D: none | none recorded | `owned` |
| `k8s-workload:verdify-prod/deployment/verdify-hermes-iris` | verdify-hermes-iris | k3s vallery / verdify-prod | Deployment verdify-hermes-iris in verdify-prod running hermes-agent. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod@main | P: ns endpoint probes (7, `[EP]`); A: `[VNS]`; M: none; D: none | `pvc:verdify-prod/verdify-hermes-iris-data-portable-20260801`, `secret:verdify-prod/ghcr-jvallery-readonly`, `secret:verdify-prod/verdify-hermes`, `secret:verdify-prod/verdify-hermes-slack`, `secret:verdify-prod/zot-origin-cluster-pull` | `owned` |
| `k8s-workload:verdify-prod/deployment/verdify-ingestor` | verdify-ingestor | k3s vallery / verdify-prod | Deployment verdify-ingestor in verdify-prod running postgres:16, timescaledb:2.25.2-pg16, verdify-ingestor. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod@main | P: ns endpoint probes (7, `[EP]`); A: `[VNS]`; M: none; D: none | `pvc:verdify-prod/verdify-ingestor-state`, `registry:registry.vallery.net (zot registry-origin)`, `secret:verdify-prod/ghcr-jvallery-readonly`, `secret:verdify-prod/verdify-app-secrets`, `secret:verdify-prod/verdify-ha-token`, `secret:verdify-prod/verdify-hermes`, `secret:verdify-prod/verdify-slack`, `secret:verdify-prod/zot-origin-cluster-pull` | `owned`; tag-only image(s) (A12) |
| `k8s-workload:verdify-prod/deployment/verdify-lab` | verdify-lab | k3s vallery / verdify-prod | Deployment verdify-lab in verdify-prod running nginx-unprivileged:1.29-alpine, verdify-lab-publisher-k3s. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod@main | P: ns endpoint probes (7, `[EP]`); A: `[VNS]`; M: none; D: none | `pvc:verdify-prod/verdify-lab-site-cache`, `registry:registry.vallery.net (zot registry-origin)`, `secret:verdify-prod/ghcr-jvallery-readonly`, `secret:verdify-prod/zot-origin-cluster-pull` | `owned` |
| `k8s-workload:verdify-prod/deployment/verdify-mcp` | verdify-mcp | k3s vallery / verdify-prod | Deployment verdify-mcp in verdify-prod running verdify-mcp. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod@main | P: ns endpoint probes (7, `[EP]`); A: `[VNS]`; M: none; D: none | `registry:registry.vallery.net (zot registry-origin)`, `secret:verdify-prod/ghcr-jvallery-readonly`, `secret:verdify-prod/verdify-hermes`, `secret:verdify-prod/verdify-mcp-observer`, `secret:verdify-prod/verdify-mcp-runtime-db`, `secret:verdify-prod/zot-origin-cluster-pull` | `owned` |
| `k8s-workload:verdify-prod/deployment/verdify-mqtt` | verdify-mqtt | k3s vallery / verdify-prod | Deployment verdify-mqtt in verdify-prod running eclipse-mosquitto:2. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod@main | P: ns endpoint probes (7, `[EP]`); A: `[VNS]`; M: none; D: none | none recorded | `owned`; tag-only image(s) (A12) |
| `k8s-workload:verdify-prod/deployment/verdify-planner` | verdify-planner | k3s vallery / verdify-prod | Deployment verdify-planner in verdify-prod running timescaledb:2.25.2-pg16, verdify-planner. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod@main | P: ns endpoint probes (7, `[EP]`); A: `[VNS]`; M: none; D: none | `registry:registry.vallery.net (zot registry-origin)`, `secret:verdify-prod/ghcr-jvallery-readonly`, `secret:verdify-prod/verdify-app-secrets`, `secret:verdify-prod/verdify-planner-runtime-db`, `secret:verdify-prod/zot-origin-cluster-pull` | `owned`; tag-only image(s) (A12) |
| `k8s-workload:verdify-prod/deployment/verdify-setpoint-server` | verdify-setpoint-server | k3s vallery / verdify-prod | Deployment verdify-setpoint-server in verdify-prod running timescaledb:2.25.2-pg16, verdify-setpoint-server. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod@main | P: ns endpoint probes (7, `[EP]`); A: `[VNS]`; M: none; D: none | `registry:registry.vallery.net (zot registry-origin)`, `secret:verdify-prod/ghcr-jvallery-readonly`, `secret:verdify-prod/verdify-ha-token`, `secret:verdify-prod/verdify-setpoint-server-runtime-db`, `secret:verdify-prod/zot-origin-cluster-pull` | `owned`; tag-only image(s) (A12) |
| `k8s-workload:verdify-prod/statefulset/verdify-db` | verdify-db | k3s vallery / verdify-prod | StatefulSet verdify-db in verdify-prod running timescaledb:2.25.2-pg16. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod@main | P: ns endpoint probes (7, `[EP]`); A: `[VNS]`; M: none; D: none | `pvc:verdify-prod/db-data`, `secret:verdify-prod/ghcr-jvallery-readonly`, `secret:verdify-prod/verdify-app-secrets`, `secret:verdify-prod/zot-origin-cluster-pull` | `owned`; tag-only image(s) (A12) |

### Scheduled jobs

| ID | Schedule (cluster TZ, UTC) | Purpose | Health | Status (record) / action |
|---|---|---|---|---|
| `alert-path:verdify-writer-watchdog` | see notes | Product watchdogs that write alert_log rows when the Verdify writer or DB dies; documented as the ONLY writer-death alert path. | **none** | `resolve-conflict`; resolved by rule R13 (independent-path list is closed): monitoring-stack `verdify.writer-ownership` alerts replace the watchdogs, then the two CronJobs retire (A10) |
| `k8s-workload:verdify-prod/cronjob/verdify-db-backup` | 17 2 * * * (source `components/db-backup/backup-cronjob.yaml`) | Verdify production DB dump. Target: pv:verdify-db-dumps-prod (/volume2/verdify). | P: none; A: `[VNS]`, `VerdifyDBBackupExporterDown`, `VerdifyDBBackupStale`; M: `observability-registry:cronjob:verdify-prod:verdify-db-backup (owner VerdifyConsultancy/platform)`; D: `vallery-backup` | `owned`; tag-only image(s) (A12) |
| `k8s-workload:verdify-descheduler/cronjob/descheduler` | 15 * * * * | CronJob descheduler in verdify-descheduler running descheduler:v0.36.0. | P: none; A: none; M: `observability-registry:cronjob:verdify-descheduler:descheduler (owner VerdifyConsultancy/platform)`; D: none | `owned`; no failure alert (A13) |
| `k8s-workload:verdify-prod/cronjob/verdify-band-curve-refresh` | 2,32 * * * * | CronJob verdify-band-curve-refresh in verdify-prod running timescaledb:2.25.2-pg16. | P: none; A: `[VNS]`; M: `observability-registry:cronjob:verdify-prod:verdify-band-curve-refresh (owner VerdifyConsultancy/platform)`; D: none | `owned`; tag-only image(s) (A12) |
| `k8s-workload:verdify-prod/cronjob/verdify-db-watchdog` | 1-59/5 * * * * | CronJob verdify-db-watchdog in verdify-prod running python:3.13-alpine. | P: none; A: `[VNS]`; M: `observability-registry:cronjob:verdify-prod:verdify-db-watchdog (owner VerdifyConsultancy/platform)`; D: none | `owned`; tag-only image(s) (A12) |
| `k8s-workload:verdify-prod/cronjob/verdify-firmware-builder` | 0 0 31 2 * (suspended) | CronJob verdify-firmware-builder in verdify-prod running esphome:2026.6.5. | P: none; A: `[VNS]`; M: `observability-registry:cronjob:verdify-prod:verdify-firmware-builder (owner VerdifyConsultancy/platform)`; D: none | `owned` |
| `k8s-workload:verdify-prod/cronjob/verdify-fixed-panel-native-freezer` | 30 0 * * * | CronJob verdify-fixed-panel-native-freezer in verdify-prod running timescaledb:2.25.2-pg16. | P: none; A: `[VNS]`; M: none; D: none | `owned`; tag-only image(s) (A12) |
| `k8s-workload:verdify-prod/cronjob/verdify-ha-gap-backfill` | 23 * * * * | CronJob verdify-ha-gap-backfill in verdify-prod running verdify-ingestor. | P: none; A: `[VNS]`; M: `observability-registry:cronjob:verdify-prod:verdify-ha-gap-backfill (owner VerdifyConsultancy/platform)`; D: none | `owned` |
| `k8s-workload:verdify-prod/cronjob/verdify-lab-publisher` | 5,35 * * * * | CronJob verdify-lab-publisher in verdify-prod running verdify-lab-publisher-k3s. | P: none; A: `[VNS]`; M: `observability-registry:cronjob:verdify-prod:verdify-lab-publisher (owner VerdifyConsultancy/platform)`; D: none | `owned` |
| `k8s-workload:verdify-prod/cronjob/verdify-vision` | 0 15,18,21,0 * * * | CronJob verdify-vision in verdify-prod running verdify-ingestor. | P: none; A: `[VNS]`; M: `observability-registry:cronjob:verdify-prod:verdify-vision (owner VerdifyConsultancy/platform)`; D: none | `owned` |
| `k8s-workload:verdify-prod/cronjob/verdify-winter-feasibility` | 30 0 * * * | CronJob verdify-winter-feasibility in verdify-prod running verdify-api. | P: none; A: `[VNS]`; M: none; D: none | `owned` |
| `k8s-workload:verdify-prod/cronjob/verdify-writer-watchdog` | */5 * * * * | CronJob verdify-writer-watchdog in verdify-prod running python:3.13-alpine, timescaledb:2.25.2-pg16. | P: none; A: `[VNS]`; M: `observability-registry:cronjob:verdify-prod:verdify-writer-watchdog (owner VerdifyConsultancy/platform)`; D: none | `owned`; tag-only image(s) (A12) |
| `k8s-workload:verdify-prod/job/verdify-winter-archive-init` | one-shot | Job verdify-winter-archive-init in verdify-prod running verdify-api. | P: none; A: `[VNS]`; M: none; D: none | `owned`; (one-shot, completed 2026-10-18) |

Full detail for the scheduled jobs above:

| ID | Name | Where | What | State | Source of truth | Health | Critical deps | Status (record) / action |
|---|---|---|---|---|---|---|---|---|
| `alert-path:verdify-writer-watchdog` | verdify-prod writer/db watchdog CronJobs | ns verdify-prod CronJobs verdify-writer-watchdog (*/5) and verdify-db-watchdog (1-59/5) | Product watchdogs that write alert_log rows when the Verdify writer or DB dies; documented as the ONLY writer-death alert path. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod/writer-watchdog.yaml, db-watchdog.yaml | **none** | `namespace:verdify-prod` | `resolve-conflict`; resolved by rule R13 (independent-path list is closed): monitoring-stack `verdify.writer-ownership` alerts replace the watchdogs, then the two CronJobs retire (A10) |
| `k8s-workload:verdify-prod/cronjob/verdify-db-backup` | verdify-db-backup | verdify-prod (17 2 * * *) | Verdify production DB dump. Target: pv:verdify-db-dumps-prod (/volume2/verdify). | running | VerdifyConsultancy/verdify-platform:(Argo app verdify-prod-dark) | P: none; A: `[VNS]`, `VerdifyDBBackupExporterDown`, `VerdifyDBBackupStale`; M: `observability-registry:cronjob:verdify-prod:verdify-db-backup (owner VerdifyConsultancy/platform)`; D: `vallery-backup` | `pv:verdify-db-dumps-prod (/volume2/verdify)`, `pvc:verdify-prod/verdify-db-dumps`, `secret:verdify-prod/ghcr-jvallery-readonly`, `secret:verdify-prod/verdify-app-secrets`, `secret:verdify-prod/zot-origin-cluster-pull` | `owned`; tag-only image(s) (A12) |
| `k8s-workload:verdify-descheduler/cronjob/descheduler` | descheduler | k3s vallery / verdify-descheduler | CronJob descheduler in verdify-descheduler running descheduler:v0.36.0. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/descheduler@b523a9b1ffec | P: none; A: none; M: `observability-registry:cronjob:verdify-descheduler:descheduler (owner VerdifyConsultancy/platform)`; D: none | none recorded | `owned`; no failure alert (A13) |
| `k8s-workload:verdify-prod/cronjob/verdify-band-curve-refresh` | verdify-band-curve-refresh | k3s vallery / verdify-prod | CronJob verdify-band-curve-refresh in verdify-prod running timescaledb:2.25.2-pg16. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod@main | P: none; A: `[VNS]`; M: `observability-registry:cronjob:verdify-prod:verdify-band-curve-refresh (owner VerdifyConsultancy/platform)`; D: none | `secret:verdify-prod/verdify-matview-refresh-runtime-db` | `owned`; tag-only image(s) (A12) |
| `k8s-workload:verdify-prod/cronjob/verdify-db-watchdog` | verdify-db-watchdog | k3s vallery / verdify-prod | CronJob verdify-db-watchdog in verdify-prod running python:3.13-alpine. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod@main | P: none; A: `[VNS]`; M: `observability-registry:cronjob:verdify-prod:verdify-db-watchdog (owner VerdifyConsultancy/platform)`; D: none | none recorded | `owned`; tag-only image(s) (A12) |
| `k8s-workload:verdify-prod/cronjob/verdify-firmware-builder` | verdify-firmware-builder | k3s vallery / verdify-prod | CronJob verdify-firmware-builder in verdify-prod running esphome:2026.6.5. | idle | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod@main | P: none; A: `[VNS]`; M: `observability-registry:cronjob:verdify-prod:verdify-firmware-builder (owner VerdifyConsultancy/platform)`; D: none | `pvc:verdify-prod/verdify-firmware-artifacts`, `secret:verdify-prod/verdify-app-secrets`, `secret:verdify-prod/verdify-firmware-ota`, `secret:verdify-prod/verdify-firmware-wifi`, `secret:verdify-prod/verdify-github-token` | `owned` |
| `k8s-workload:verdify-prod/cronjob/verdify-fixed-panel-native-freezer` | verdify-fixed-panel-native-freezer | k3s vallery / verdify-prod | CronJob verdify-fixed-panel-native-freezer in verdify-prod running timescaledb:2.25.2-pg16. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod@main | P: none; A: `[VNS]`; M: none; D: none | `secret:verdify-prod/verdify-experiment-v2-outcome-freezer-db`, `secret:verdify-prod/zot-origin-cluster-pull` | `owned`; tag-only image(s) (A12) |
| `k8s-workload:verdify-prod/cronjob/verdify-ha-gap-backfill` | verdify-ha-gap-backfill | k3s vallery / verdify-prod | CronJob verdify-ha-gap-backfill in verdify-prod running verdify-ingestor. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod@main | P: none; A: `[VNS]`; M: `observability-registry:cronjob:verdify-prod:verdify-ha-gap-backfill (owner VerdifyConsultancy/platform)`; D: none | `registry:registry.vallery.net (zot registry-origin)`, `secret:verdify-prod/ghcr-jvallery-readonly`, `secret:verdify-prod/verdify-ha-backfill-runtime-db`, `secret:verdify-prod/verdify-ha-token`, `secret:verdify-prod/zot-origin-cluster-pull` | `owned` |
| `k8s-workload:verdify-prod/cronjob/verdify-lab-publisher` | verdify-lab-publisher | k3s vallery / verdify-prod | CronJob verdify-lab-publisher in verdify-prod running verdify-lab-publisher-k3s. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod@main | P: none; A: `[VNS]`; M: `observability-registry:cronjob:verdify-prod:verdify-lab-publisher (owner VerdifyConsultancy/platform)`; D: none | `pvc:verdify-prod/verdify-lab-site-cache`, `registry:registry.vallery.net (zot registry-origin)`, `secret:verdify-prod/ghcr-jvallery-readonly`, `secret:verdify-prod/verdify-lab-publisher-runtime-db`, `secret:verdify-prod/verdify-lab-publisher-s3`, `secret:verdify-prod/zot-origin-cluster-pull` | `owned` |
| `k8s-workload:verdify-prod/cronjob/verdify-vision` | verdify-vision | k3s vallery / verdify-prod | CronJob verdify-vision in verdify-prod running verdify-ingestor. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod@main | P: none; A: `[VNS]`; M: `observability-registry:cronjob:verdify-prod:verdify-vision (owner VerdifyConsultancy/platform)`; D: none | `registry:registry.vallery.net (zot registry-origin)`, `secret:verdify-prod/verdify-frigate-vision-auth`, `secret:verdify-prod/verdify-vision-key`, `secret:verdify-prod/verdify-vision-runtime-db`, `secret:verdify-prod/zot-origin-cluster-pull` | `owned` |
| `k8s-workload:verdify-prod/cronjob/verdify-winter-feasibility` | verdify-winter-feasibility | k3s vallery / verdify-prod | CronJob verdify-winter-feasibility in verdify-prod running verdify-api. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod@main | P: none; A: `[VNS]`; M: none; D: none | `pvc:verdify-prod/verdify-winter-feasibility-archive`, `registry:registry.vallery.net (zot registry-origin)`, `secret:verdify-prod/verdify-agent-secrets`, `secret:verdify-prod/zot-origin-cluster-pull` | `owned` |
| `k8s-workload:verdify-prod/cronjob/verdify-writer-watchdog` | verdify-writer-watchdog | k3s vallery / verdify-prod | CronJob verdify-writer-watchdog in verdify-prod running python:3.13-alpine, timescaledb:2.25.2-pg16. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod@main | P: none; A: `[VNS]`; M: `observability-registry:cronjob:verdify-prod:verdify-writer-watchdog (owner VerdifyConsultancy/platform)`; D: none | `secret:verdify-prod/verdify-writer-watchdog-runtime-db` | `owned`; tag-only image(s) (A12) |
| `k8s-workload:verdify-prod/job/verdify-winter-archive-init` | verdify-winter-archive-init | k3s vallery / verdify-prod | Job verdify-winter-archive-init in verdify-prod running verdify-api. | idle | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod@main | P: none; A: `[VNS]`; M: none; D: none | `pvc:verdify-prod/verdify-winter-feasibility-archive`, `registry:registry.vallery.net (zot registry-origin)`, `secret:verdify-prod/zot-origin-cluster-pull` | `owned`; (one-shot, completed 2026-10-18) |

### verdify-db-rehearsal CNPG cluster

| ID | Name | Where | What | State | Source of truth | Health | Critical deps | Status (record) / action |
|---|---|---|---|---|---|---|---|---|
| `k8s-workload:verdify-db-rehearsal/cluster/verdify-cnpg-s2-integration` | verdify-cnpg-s2-integration | k3s vallery / verdify-db-rehearsal | CNPG TimescaleDB 16.13 rehearsal cluster (3 instances) for Verdify S2 DB migration qualification; Argo OutOfSync. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/cnpg/rehearsal/cluster@main | **none** | `CNPG operator (cnpg-system, agents)`, `pvc:verdify-db-rehearsal/verdify-cnpg-s2-integration-1`, `pvc:verdify-db-rehearsal/verdify-cnpg-s2-integration-2`, `pvc:verdify-db-rehearsal/verdify-cnpg-s2-integration-3` | `owned`; no health signal (A13); D-04 approved 2026-10-19: retire when S2 closes, after a dump and Jason's explicit go (data, R10) (A4) |

## Storage (PVCs)

Data PVCs follow R10: they carry `Prune=false,Delete=false`, and deleting one needs a snapshot and Jason's explicit instruction. Static PV `verdify-db-dumps-prod` (NAS) is storage-infra's (R9); Longhorn PVs and volumes are generated from these claims.

| ID | Name | Where | What | State | Source of truth | Health | Critical deps | Status (record) / action |
|---|---|---|---|---|---|---|---|---|
| `pvc:verdify-db-rehearsal/verdify-cnpg-s2-integration-1` | verdify-cnpg-s2-integration-1 | namespace verdify-db-rehearsal | PVC 20Gi class longhorn-v1-rwo Bound; Longhorn volume pvc-5bc955b4-d4fa-4772-8fb9-ff7b7c902543 (attached/healthy, replicas 2, recurring standard). | running | VerdifyConsultancy/verdify-platform:deploy/k8s/cnpg/rehearsal/cluster | P: none; A: `[LH]`; M: `kubelet_volume_stats_available_bytes`; D: none | `storageclass:cluster/longhorn-v1-rwo`, `storageclass:longhorn-v1-rwo` | `owned`; data, R10; D-04 approved 2026-10-19: retires when S2 closes, after a dump and Jason's explicit go (A4) |
| `pvc:verdify-db-rehearsal/verdify-cnpg-s2-integration-1-wal` | verdify-cnpg-s2-integration-1-wal | namespace verdify-db-rehearsal | PVC 5Gi class longhorn-v1-rwo Bound; Longhorn volume pvc-8ec30e16-a09e-439e-b5a5-b4e201656aa0 (attached/healthy, replicas 2, recurring standard). | running | VerdifyConsultancy/verdify-platform:deploy/k8s/cnpg/rehearsal/cluster | P: none; A: `[LH]`; M: `kubelet_volume_stats_available_bytes`; D: none | `storageclass:cluster/longhorn-v1-rwo`, `storageclass:longhorn-v1-rwo` | `owned`; data, R10; D-04 approved 2026-10-19: retires when S2 closes, after a dump and Jason's explicit go (A4) |
| `pvc:verdify-db-rehearsal/verdify-cnpg-s2-integration-2` | verdify-cnpg-s2-integration-2 | namespace verdify-db-rehearsal | PVC 20Gi class longhorn-v1-rwo Bound; Longhorn volume pvc-4975744d-876f-4f2a-8b44-2ff908ea0431 (attached/healthy, replicas 2, recurring standard). | running | VerdifyConsultancy/verdify-platform:deploy/k8s/cnpg/rehearsal/cluster | P: none; A: `[LH]`; M: `kubelet_volume_stats_available_bytes`; D: none | `storageclass:cluster/longhorn-v1-rwo`, `storageclass:longhorn-v1-rwo` | `owned`; data, R10; D-04 approved 2026-10-19: retires when S2 closes, after a dump and Jason's explicit go (A4) |
| `pvc:verdify-db-rehearsal/verdify-cnpg-s2-integration-2-wal` | verdify-cnpg-s2-integration-2-wal | namespace verdify-db-rehearsal | PVC 5Gi class longhorn-v1-rwo Bound; Longhorn volume pvc-428fa291-e672-44c6-8d44-1b0980dfbcdf (attached/healthy, replicas 2, recurring standard). | running | VerdifyConsultancy/verdify-platform:deploy/k8s/cnpg/rehearsal/cluster | P: none; A: `[LH]`; M: `kubelet_volume_stats_available_bytes`; D: none | `storageclass:cluster/longhorn-v1-rwo`, `storageclass:longhorn-v1-rwo` | `owned`; data, R10; D-04 approved 2026-10-19: retires when S2 closes, after a dump and Jason's explicit go (A4) |
| `pvc:verdify-db-rehearsal/verdify-cnpg-s2-integration-3` | verdify-cnpg-s2-integration-3 | namespace verdify-db-rehearsal | PVC 20Gi class longhorn-v1-rwo Bound; Longhorn volume pvc-6e951ccb-1807-4d40-ac5c-57c7f9b872d8 (attached/healthy, replicas 2, recurring standard). | running | VerdifyConsultancy/verdify-platform:deploy/k8s/cnpg/rehearsal/cluster | P: none; A: `[LH]`; M: `kubelet_volume_stats_available_bytes`; D: none | `storageclass:cluster/longhorn-v1-rwo`, `storageclass:longhorn-v1-rwo` | `owned`; data, R10; D-04 approved 2026-10-19: retires when S2 closes, after a dump and Jason's explicit go (A4) |
| `pvc:verdify-db-rehearsal/verdify-cnpg-s2-integration-3-wal` | verdify-cnpg-s2-integration-3-wal | namespace verdify-db-rehearsal | PVC 5Gi class longhorn-v1-rwo Bound; Longhorn volume pvc-463e7de8-3eaa-4d05-904c-f84f6455e531 (attached/healthy, replicas 2, recurring standard). | running | VerdifyConsultancy/verdify-platform:deploy/k8s/cnpg/rehearsal/cluster | P: none; A: `[LH]`; M: `kubelet_volume_stats_available_bytes`; D: none | `storageclass:cluster/longhorn-v1-rwo`, `storageclass:longhorn-v1-rwo` | `owned`; data, R10; D-04 approved 2026-10-19: retires when S2 closes, after a dump and Jason's explicit go (A4) |
| `pvc:verdify-db-rehearsal/verdify-s2-spool-382-7fa4f802` | verdify-s2-spool-382-7fa4f802 | namespace verdify-db-rehearsal | PVC 2Gi class longhorn-v1-workspace-rwo Bound; Longhorn volume pvc-116e36a0-be6b-42aa-9643-ef4b638492db (detached/unknown, replicas 2, recurring standard). | idle | live-only | P: none; A: `[LH]`; M: `kubelet_volume_stats_available_bytes`; D: none | `storageclass:cluster/longhorn-v1-workspace-rwo`, `storageclass:longhorn-v1-workspace-rwo` | `adopt`; data, R10: D-04 approved 2026-10-19 sets the retirement path; delete only after a snapshot and Jason's explicit go (A3) |
| `pvc:verdify-db-rehearsal/verdify-s2-spool-qualification` | verdify-s2-spool-qualification | namespace verdify-db-rehearsal | PVC 2Gi class longhorn-v1-workspace-rwo Bound; Longhorn volume pvc-a7158960-36dc-439c-bd6f-14f1aa744bab (detached/unknown, replicas 2, recurring standard). | idle | live-only | P: none; A: `[LH]`; M: `kubelet_volume_stats_available_bytes`; D: none | `storageclass:cluster/longhorn-v1-workspace-rwo`, `storageclass:longhorn-v1-workspace-rwo` | `adopt`; data, R10: D-04 approved 2026-10-19 sets the retirement path; delete only after a snapshot and Jason's explicit go (A3) |
| `pvc:verdify-prod/db-data-verdify-db-0` | db-data-verdify-db-0 | namespace verdify-prod | PVC 50Gi class longhorn-v1-workspace-rwo Bound; Longhorn volume pvc-59fe138d-1cc4-4aac-bfcb-3d332f847411 (attached/healthy, replicas 3, recurring standard). | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod | P: none; A: `[LH]`; M: `kubelet_volume_stats_available_bytes`; D: none | `storageclass:cluster/longhorn-v1-workspace-rwo`, `storageclass:longhorn-v1-workspace-rwo` | `owned`; adopt: pre-created by kubectl, bound via volumeClaimTemplate `db-data`; confirm `Prune=false,Delete=false` (A6, R10) |
| `pvc:verdify-prod/verdify-db-dumps` | verdify-db-dumps | namespace verdify-prod | PVC 100Gi class (static binding) Bound; csi-nfs PV verdify-db-dumps-prod -> NAS obsidian `/volume2/verdify` (storage-infra inventory). | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod | P: none; A: `NASNFSServerNotResponding`, `NFSMountUnavailable`; M: `kubelet_volume_stats_available_bytes`; D: none | `pv:cluster/verdify-db-dumps-prod`, `pv:verdify-db-dumps-prod` | `owned`; (PVC); PV `verdify-db-dumps-prod` is storage-infra's (R9) |
| `pvc:verdify-prod/verdify-firmware-artifacts` | verdify-firmware-artifacts | namespace verdify-prod | PVC 5Gi class longhorn-v1-workspace-rwo Bound; Longhorn volume pvc-a19dc6b1-ecd2-413d-a89d-692fd113fa94 (detached/unknown, replicas 2, recurring standard). | idle | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod | P: none; A: `[LH]`; M: `kubelet_volume_stats_available_bytes`; D: none | `storageclass:cluster/longhorn-v1-workspace-rwo`, `storageclass:longhorn-v1-workspace-rwo` | `owned` |
| `pvc:verdify-prod/verdify-firmware-toolchain-cache` | verdify-firmware-toolchain-cache | namespace verdify-prod | PVC 10Gi class longhorn-v1-rebuildable-rwo Bound; Longhorn volume pvc-a889346e-cb48-47ec-bcec-94b1ffa1d732 (detached/unknown, replicas 2, recurring backup-only,default). | idle | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod | P: none; A: `[LH]`; M: `kubelet_volume_stats_available_bytes`; D: none | `storageclass:cluster/longhorn-v1-rebuildable-rwo`, `storageclass:longhorn-v1-rebuildable-rwo` | `owned` |
| `pvc:verdify-prod/verdify-grafana-data` | verdify-grafana-data | namespace verdify-prod | PVC 1Gi class longhorn-v1-workspace-rwo Bound; Longhorn volume pvc-acad919d-b6d2-4824-b7c7-a2b2ab460103 (attached/healthy, replicas 2, recurring standard). | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod | P: none; A: `[LH]`; M: `kubelet_volume_stats_available_bytes`; D: none | `storageclass:cluster/longhorn-v1-workspace-rwo`, `storageclass:longhorn-v1-workspace-rwo` | `owned` |
| `pvc:verdify-prod/verdify-hermes-iris-data-portable-20260801` | verdify-hermes-iris-data-portable-20260801 | namespace verdify-prod | PVC 5Gi class longhorn-v1-rebuildable-rwo Bound; Longhorn volume pvc-6b6b2f30-c414-4325-a803-87b19010851f (attached/healthy, replicas 2, recurring backup-only,default). | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod | P: none; A: `[LH]`; M: `kubelet_volume_stats_available_bytes`; D: none | `storageclass:cluster/longhorn-v1-rebuildable-rwo`, `storageclass:longhorn-v1-rebuildable-rwo` | `owned` |
| `pvc:verdify-prod/verdify-ingestor-state` | verdify-ingestor-state | namespace verdify-prod | PVC 2Gi class longhorn-v1-workspace-rwo Bound; Longhorn volume pvc-68cb2b6c-dac9-494a-9fec-92b800762b5b (attached/healthy, replicas 2, recurring standard). | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod | P: none; A: `[LH]`; M: `kubelet_volume_stats_available_bytes`; D: none | `storageclass:cluster/longhorn-v1-workspace-rwo`, `storageclass:longhorn-v1-workspace-rwo` | `owned` |
| `pvc:verdify-prod/verdify-lab-site-cache` | verdify-lab-site-cache | namespace verdify-prod | PVC 5Gi class longhorn-v1-rebuildable-rwo Bound; Longhorn volume pvc-1bd6ffec-7246-4e15-b080-287a9cf37418 (attached/healthy, replicas 2, recurring backup-only,default). | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod | P: none; A: `[LH]`; M: `kubelet_volume_stats_available_bytes`; D: none | `storageclass:cluster/longhorn-v1-rebuildable-rwo`, `storageclass:longhorn-v1-rebuildable-rwo` | `owned` |
| `pvc:verdify-prod/verdify-winter-feasibility-archive` | verdify-winter-feasibility-archive | namespace verdify-prod | PVC 10Gi class longhorn-v1-workspace-rwo Bound; Longhorn volume pvc-da430cd3-7469-4e36-8ef6-741fa281b753 (detached/unknown, replicas 2, recurring standard). | idle | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod | P: none; A: `[LH]`; M: `kubelet_volume_stats_available_bytes`; D: none | `storageclass:cluster/longhorn-v1-workspace-rwo`, `storageclass:longhorn-v1-workspace-rwo` | `owned` |

## Cluster-scoped objects, RBAC and policy

| ID | Name | Where | What | State | Source of truth | Health | Critical deps | Status (record) / action |
|---|---|---|---|---|---|---|---|---|
| `cluster-object:priorityclass/verdify-batch` | verdify-batch | k3s vallery (cluster-scoped) | PriorityClass value 1000, globalDefault False. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod | **none** | none recorded | `owned` |
| `cluster-object:priorityclass/verdify-data-critical` | verdify-data-critical | k3s vallery (cluster-scoped) | PriorityClass value 800000000, globalDefault False. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod | **none** | none recorded | `owned` |
| `cluster-object:priorityclass/verdify-device-writer` | verdify-device-writer | k3s vallery (cluster-scoped) | PriorityClass value 900000000, globalDefault False. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod | **none** | none recorded | `owned` |
| `cluster-object:priorityclass/verdify-serving` | verdify-serving | k3s vallery (cluster-scoped) | PriorityClass value 100000, globalDefault False. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/overlays/prod | **none** | none recorded | `owned` |
| `cluster-object:rbac/verdify-descheduler` | ClusterRoles/Bindings from verdify-descheduler | k3s vallery (cluster-scoped RBAC) | 2 objects: Cluster:verdify-descheduler, Cluster:verdify-descheduler. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/descheduler | **none** | none recorded | `owned`; registered estate exception (cluster-wide pod delete); record in map (A7) |
| `cluster-object:rbac/verdify-prod-dark` | Retired tier-2 Kubernetes-provider RBAC | k3s vallery | #1006 removed the ClusterRole/Binding and every namespaced grant; file routing has zero Kubernetes access. | retired | deploy/k8s/overlays/prod/traefik | **none** | none | R18 satisfied by zero grants; no replacement Role/Binding exists |
| `cluster-object:rbac/live-verdify-agent` | ClusterRoles/Bindings: live:verdify-agent | k3s vallery (cluster-scoped RBAC) | verdify-agent-cluster-readonly role+binding (seen only in 2026-06-07 audit inventory). 2 objects: Cluster:verdify-agent-cluster-readonly, Cluster:verdify-agent-cluster-readonly. | running | live-only | **none** | none recorded | `adopt`; delete residue after subject check (A8) |

### NetworkPolicy groups and CNPG ObjectStores

| ID | Name | Where | What | State | Source of truth | Health | Critical deps | Status (record) / action |
|---|---|---|---|---|---|---|---|---|
| `cluster-object:verdify-db-rehearsal/barman-objectstore/verdify-cnpg-rehearsal-backup` | verdify-cnpg-rehearsal-backup | k8s ns verdify-db-rehearsal (barmancloud.cnpg.io ObjectStore) | Barman-cloud plugin ObjectStore pointing CNPG WAL/base backups at s3://verdify-cnpg-rehearsal/postgresql (retention 14d). | idle | VerdifyConsultancy/verdify-platform:(Argo app verdify-db-rehearsal-cluster) | P: none; A: `CNPGWALArchivingFailing`; M: none; D: none | `garage S3 bucket verdify-cnpg-rehearsal`, `k8s-workload:cnpg-system/deployment/barman-cloud` | `owned`; live and tracked but no longer in `cnpg/rehearsal/cluster/kustomization.yaml`; retires with the namespace when S2 closes (D-04 approved 2026-10-19, A4) |
| `cluster-object:verdify-db-rehearsal/barman-objectstore/verdify-cnpg-pitr-reader` | verdify-cnpg-pitr-reader | k8s ns verdify-db-rehearsal (barmancloud.cnpg.io ObjectStore) | Barman-cloud plugin ObjectStore pointing CNPG WAL/base backups at s3://verdify-cnpg-rehearsal/postgresql (retention none). | idle | VerdifyConsultancy/verdify-platform:(Argo app verdify-db-rehearsal-cluster) | P: none; A: `CNPGWALArchivingFailing`; M: none; D: none | `garage S3 bucket verdify-cnpg-rehearsal`, `k8s-workload:cnpg-system/deployment/barman-cloud` | `owned`; retires with the namespace when S2 closes (D-04 approved 2026-10-19, A4) |
| `cluster-object:verdify-db-rehearsal/networkpolicies-residue` | hand-created qualification NetworkPolicies in verdify-db-rehearsal (16) | k8s ns verdify-db-rehearsal | Untracked NetworkPolicies whose podSelector matches no running pod (left by qualification/restore/canary runs) (2026-10-02 to 2026-10-06): cnpg-runtime-client-ingress, cnpg-runtime-client-only, cnpg-s2-archive-reader-onl | retired-residue | none | **none** | `namespace:verdify-db-rehearsal` | `retire`; delete residue now (non-data; D-04 approved 2026-10-19: prune Jobs now) (A2) |
| `cluster-object:verdify-prod/networkpolicies-hand-applied` | hand-applied NetworkPolicies in verdify-prod (1) | k8s ns verdify-prod | Untracked NetworkPolicies whose podSelector matches live pods in verdify-prod: allow-db-from-restore. | running | live-only | **none** | `namespace:verdify-prod` | `adopt`; resolved by placement rule 1 (namespace owner): adopt `allow-db-from-restore` into `components/db-backup`, or delete it if restore drills stop using it (A5) |
| `cluster-object:verdify-prod/networkpolicies-residue` | hand-created qualification NetworkPolicies in verdify-prod (36) | k8s ns verdify-prod | Untracked NetworkPolicies whose podSelector matches no running pod (left by qualification/restore/canary runs) (2026-09-26 to 2026-10-11): verdify-backup-pair-restore-20260927-deny-all, verdify-backup-pair-restore-c0255d | retired-residue | none | **none** | `namespace:verdify-prod` | `retire`; delete residue (A1) |

## Objects in other namespaces and cluster-scoped objects

Objects this repo writes outside its own namespaces, and its cluster-scoped objects, as aggregated in the central record. The per-object rows above (`cluster-object:priorityclass/*`, `cluster-object:rbac/*`) carry health and actions; these rows are the record's aggregate view.

| ID | Object kind | Namespace | Objects | Names | Status (record) |
|---|---|---|---:|---|---|
| `cluster:clusterrole/verdifyconsultancy/verdify-platform` | ClusterRole | (cluster-scoped) | 1 | `verdify-descheduler` | `owned` |
| `cluster:clusterrolebinding/verdifyconsultancy/verdify-platform` | ClusterRoleBinding | (cluster-scoped) | 1 | `verdify-descheduler` | `owned` |
| `cluster:priorityclass/verdifyconsultancy/verdify-platform` | PriorityClass | (cluster-scoped) | 4 | `verdify-batch`, `verdify-data-critical`, `verdify-device-writer`, `verdify-serving` | `owned` |
| `contribution:verdify-platform/configmap/verdifyconsultancy/verdify-platform` | ConfigMap | verdify-platform | 4 | `verdify-grafana-dashboards-0`, `verdify-grafana-dashboards-1`, `verdify-grafana-dashboards-2`, `verdify-grafana-dashboards-3` | `owned` |

The four ConfigMaps in `verdify-platform` are residue copies of the `verdify-prod` dashboards and are deleted under A9 (non-data cleanup).

## Secrets (names only)

Secret values are rendered by the jvallery/agents KSOPS lane (R8); this repo owns the name/key contract in `deploy/k8s/SECRETS.md`.

| ID | Name | Where | What | State | Source of truth | Health | Critical deps | Status (record) / action |
|---|---|---|---|---|---|---|---|---|
| `secret:verdify-db-rehearsal/operator-generated/cnpg` | cnpg-generated secrets in verdify-db-rehearsal (5) | k8s ns verdify-db-rehearsal | Secrets generated by cnpg: verdify-cnpg-s2-integration-app, verdify-cnpg-s2-integration-ca, verdify-cnpg-s2-integration-pull, verdify-cnpg-s2-integration-replication, verdify-cnpg-s2-integration-server | running | generated by cnpg from its CRs | **none** | none recorded | `owned` |
| `secret:verdify-db-rehearsal/controller-generated` | verdify-db-rehearsal: controller-generated Secrets (5) | k3s vallery / verdify-db-rehearsal | Secrets generated by cert-manager, CNPG or mariadb-operator for objects in this namespace. | running | operator-generated | **none** | none recorded | `owned` |

## Devices, product Grafana, alert paths and repositories

### Greenhouse hardware (IPs: see network-infra `ipam/allocation.md`)

| ID | Name | Where | What | State | Source of truth | Health | Critical deps | Status (record) / action |
|---|---|---|---|---|---|---|---|---|
| `physical-device:esphome/greenhouse` | ESPHome greenhouse | <IP: ipam> IoT VLAN10 | Espressif esp32dev — ESP32 greenhouse climate controller with Modbus air/soil probes. | running | VerdifyConsultancy/verdify-platform:firmware/greenhouse.yaml | P: `fleet-ip-services:<IP: ipam>`, `fleet-ip-services:<IP: ipam>:3232`, `fleet-ip-services:<IP: ipam>:6053`; A: `FleetHardwareNativeUnavailable`, `FleetIPProbeUnavailable`, `UniFiIoTClientAbsent`; M: `HA ESPHome entities`, `fleet-ip-services probes`; D: none | `vlan:unifi/10`, `vm:oxide/302` | `owned`; product firmware and controller here; IoT device-authority row is jvallery/homeassistant's per D-06 (A15) |
| `physical-device:iot/ha-shelly-2b1c9427` | Shelly Pro EM <IP: ipam> | <IP: ipam> IoT VLAN10; Greenhouse | Shelly Pro EM energy meter measuring greenhouse circuits. | running | jvallery/proxmox-infrastructure:inventory/fleet-hardware.md (HA-SHELLY-2B1C9427); HA Shelly integration | P: `fleet-ip-services:<IP: ipam>`, `fleet-ip-services:http://<IP: ipam>:80/`; A: none; M: none; D: none | `vlan:unifi/10` | `owned`; kept in this lane by the final record; per D-06 (per-lane device authority) the IoT device-authority row is jvallery/homeassistant's and proxmox-infrastructure links to it; add an alert (A13, A15) |
| `physical-device:iot/ha-shelly-b3a979c8` | Shelly Pro EM <IP: ipam> | <IP: ipam> IoT VLAN10; Greenhouse | Shelly Pro EM energy meter measuring greenhouse circuits. | running | jvallery/proxmox-infrastructure:inventory/fleet-hardware.md (HA-SHELLY-B3A979C8); HA Shelly integration | P: `fleet-ip-services:<IP: ipam>`, `fleet-ip-services:<IP: ipam>:502`; A: none; M: none; D: none | `vlan:unifi/10` | `owned`; kept in this lane by the final record; per D-06 (per-lane device authority) the IoT device-authority row is jvallery/homeassistant's and proxmox-infrastructure links to it; add an alert (A13, A15) |
| `physical-device:iot-aggregate/greenhouse-probes` | Greenhouse Modbus probes (9 declared) | property; IoT VLAN10 / Home VLAN40 / bridged | Six air and three soil Modbus probes behind the greenhouse ESPHome controller. | running | jvallery/proxmox-infrastructure:inventory/fleet-hardware.md; HA device registry (jvallery/homeassistant docs/home/catalog/DEVICES.json snapshot 2026-10-15) | P: `fleet-ip-services (where IP-joined)`; A: `FleetHardwareHealthBindingMissing`, `FleetHardwareNativeUnavailable`, `IoTProtocolHealthUnavailable`; M: `ha-hardware-events`; D: `vallery-home` | `physical-device:esphome/greenhouse` | `owned`; probes 04, 08, 09 unavailable (A13, #751); IoT device-authority row is jvallery/homeassistant's per D-06 (A15) |

### Product Grafana and dashboard copies

| ID | Name | Where | What | State | Source of truth | Health | Critical deps | Status (record) / action |
|---|---|---|---|---|---|---|---|---|
| `dashcopy:verdify-platform/product-grafana` | Verdify product Grafana + dashboards | ns verdify-prod Deployment verdify-grafana (+render cache), ConfigMaps verdify-grafana-dashboards-0..3; graphs.verdify.ai | Customer-facing Verdify Grafana with ~25 site dashboards generated from verdify-platform grafana/ trees. | running | VerdifyConsultancy/verdify-platform:deploy/k8s/components/grafana | **none** | `dns:graphs.verdify.ai`, `namespace:verdify-prod` | `owned`; (R13 product exception) |
| `residue:verdify-platform/ns-verdify-platform-dashboard-cms` | verdify-platform/verdify-grafana-dashboards-0..3 ConfigMaps | ns verdify-platform | Four untracked dashboard ConfigMaps in the agent-sessions namespace verdify-platform duplicating verdify-prod ones. | retired-residue | live-only | **none** | `namespace:verdify-platform` | `retire`; delete residue (A9) |
| `residue:monitoring-stack/dashboards-staged-verdify` | monitoring-stack deploy/observability/grafana/dashboards/staged/verdify-prod-site/ (26 files) | jvallery/monitoring-stack@origin/main:deploy/observability/grafana/dashboards/staged/verdify-prod-site/ (26 files) | Staged copy of Verdify product dashboards in monitoring-stack. | declared-not-deployed | jvallery/monitoring-stack:deploy/observability/grafana/dashboards/staged/verdify-prod-site/ (26 files) | **none** | none recorded | `resolve-conflict`; R13 product-dashboard exception: product dashboards live here, so monitoring-stack deletes its staged copy and `Greenhouse/*` provisioning copy (A11) |
| `other:verdify-product-grafana` | Verdify product Grafana (graphs.verdify.ai) | ns verdify-prod | Product dashboards served to users. | running | VerdifyConsultancy/verdify-platform:grafana/, deploy/k8s/components/grafana | **none** | none recorded | `owned` |

### Repositories

| ID | Name | Where | What | State | Source of truth | Health | Critical deps | Status (record) / action |
|---|---|---|---|---|---|---|---|---|
| `repo:verdifyconsultancy/verdify-agent-context` | VerdifyConsultancy/verdify-agent-context | github.com/VerdifyConsultancy/verdify-agent-context (archived, private) | Private Verdify agent context, runbooks, operational handoffs, and sanitized project context. | retired-residue | none | **none** | none recorded | `retire`; retired (archived evidence); no action |
| `repo:verdifyconsultancy/verdify-gravity` | VerdifyConsultancy/verdify-gravity | github.com/VerdifyConsultancy/verdify-gravity (archived, private) | Archived repository (no description). | retired-residue | none | **none** | none recorded | `retire`; retired (archived evidence); no action |
| `repo:verdifyconsultancy/verdify-planner` | VerdifyConsultancy/verdify-planner | github.com/VerdifyConsultancy/verdify-planner (archived, private) | Verdify planner service and planner_graph package source. | retired-residue | none | **none** | none recorded | `retire`; retired (archived evidence; code now in `planner_graph/`); no action |
| `repo:verdifyconsultancy/verdify-platform` | VerdifyConsultancy/verdify-platform | github.com/VerdifyConsultancy/verdify-platform | Verdify greenhouse platform: API, DB, firmware, ingestor, web/SaaS; ns verdify-prod, verdify-descheduler. | running | VerdifyConsultancy/verdify-platform:AGENTS.md | **none** | `repo:jvallery/agents (registry, fleet CI, Argo)` | `owned` |

### Other

| ID | Name | Where | What | State | Source of truth | Health | Critical deps | Status (record) / action |
|---|---|---|---|---|---|---|---|---|
| `k8s-workload:verdify-db-rehearsal/deployment/verdify-s2-grafana-proof` | verdify-s2-grafana-proof | k3s vallery / verdify-db-rehearsal | Deployment verdify-s2-grafana-proof in verdify-db-rehearsal running grafana:12.4.5. | scaled-to-zero | live-only | **none** | `secret:verdify-db-rehearsal/verdify-cnpg-s2-runtime-client-auth` | `retire`; delete residue now (non-data; D-04 approved 2026-10-19: prune Jobs now) (A2) |
| `k8s-workload:verdify-db-rehearsal/job/adhoc-kubectl-jobs` | verdify-db-rehearsal ad-hoc kubectl Jobs (81) | k3s vallery / verdify-db-rehearsal | Aggregate of 81 standalone Jobs (not owned by a CronJob, no Argo tracking) created by hand/agents 2026-10-02..2026-10-12. | retired-residue | live-only | **none** | none recorded | `retire`; delete residue now (non-data; D-04 approved 2026-10-19: prune Jobs now) (A2) |
| `k8s-workload:verdify-prod/job/adhoc-kubectl-jobs` | verdify-prod ad-hoc kubectl Jobs (44) | k3s vallery / verdify-prod | Aggregate of 44 standalone Jobs (not owned by a CronJob, no Argo tracking) created by hand/agents 2026-07-28..2026-10-13. | retired-residue | live-only | **none** | none recorded | `retire`; delete residue (A1) |
| `k8s-workload:verdify-db-rehearsal/pod/standalone-pods` | verdify-db-rehearsal standalone Pods (6) | k3s vallery / verdify-db-rehearsal | Aggregate of 6 bare Pods with no controller owner, created by kubectl for diagnostics/restore drills. | retired-residue | live-only | **none** | none recorded | `retire`; delete residue now (non-data; D-04 approved 2026-10-19: prune Jobs now) (A2) |

## Live objects per namespace

Counts by kind and disposition from the 2026-10-19 readback (objects attributed to this repo). `declared` = Argo-tracked from this repo; `derived:*` folded into `declared`; `residue-delete`, `hand-applied-adopt` and `conflict-needs-decision` are listed in [Open actions](#open-actions).

### verdify-prod

| Kind | Total | By disposition |
|---|---:|---|
| ConfigMap | 59 | declared 20, residue-delete 38, system 1 |
| ControllerRevision | 6 | declared 6 |
| CronJob | 10 | declared 10 |
| Deployment | 15 | declared 15 |
| IngressRoute | 5 | declared 5 |
| Job | 64 | declared 20, residue-delete 44 |
| LimitRange | 1 | declared 1 |
| Middleware | 1 | declared 1 |
| NetworkPolicy | 62 | conflict-needs-decision 1, declared 25, residue-delete 36 |
| PersistentVolumeClaim | 9 | declared 8, hand-applied-adopt 1 |
| Pod | 39 | declared 38, generated-by-owned-component 1 |
| PodDisruptionBudget | 8 | declared 8 |
| ReplicaSet | 139 | declared 139 |
| Role | 4 | declared 3, residue-delete 1 |
| RoleBinding | 4 | declared 3, residue-delete 1 |
| Service | 11 | declared 11 |
| ServiceAccount | 5 | declared 4, system 1 |
| StatefulSet | 1 | declared 1 |

### verdify-descheduler

| Kind | Total | By disposition |
|---|---:|---|
| ConfigMap | 2 | declared 1, system 1 |
| CronJob | 1 | declared 1 |
| Job | 6 | declared 6 |
| Pod | 3 | declared 3 |
| Role | 1 | declared 1 |
| RoleBinding | 1 | declared 1 |
| ServiceAccount | 2 | declared 1, system 1 |

### verdify-db-rehearsal

| Kind | Total | By disposition |
|---|---:|---|
| Cluster | 1 | declared 1 |
| ConfigMap | 17 | residue-delete 16, system 1 |
| Deployment | 1 | residue-delete 1 |
| FailoverQuorum | 1 | declared 1 |
| Job | 81 | residue-delete 81 |
| NetworkPolicy | 20 | declared 4, hand-applied-adopt 2, residue-delete 14 |
| ObjectStore | 2 | declared 2 |
| PersistentVolumeClaim | 8 | declared 6, residue-delete 2 |
| Pod | 10 | declared 3, generated-by-owned-component 1, residue-delete 6 |
| PodDisruptionBudget | 2 | declared 2 |
| PodMetrics | 3 | system 3 |
| ReplicaSet | 1 | hand-applied 1 |
| Role | 2 | declared 2 |
| RoleBinding | 2 | declared 2 |
| Secret | 13 | declared 5, residue-delete 8 |
| Service | 3 | declared 3 |
| ServiceAccount | 2 | declared 1, system 1 |

### verdify-edge

| Kind | Total | By disposition |
|---|---:|---|
| CertificateRequest | 1 | hand-applied 1 |
| ConfigMap | 1 | system 1 |
| Order | 1 | hand-applied 1 |
| Secret | 1 | generated-by-owned-component 1 |
| ServiceAccount | 1 | system 1 |

### verdify-platform

| Kind | Total | By disposition |
|---|---:|---|
| ConfigMap | 4 | residue-delete 4 |

### cluster-scoped

| Kind | Total | By disposition |
|---|---:|---|
| ClusterRole | 2 | declared 2 |
| ClusterRoleBinding | 2 | declared 2 |
| Namespace | 3 | declared 2, hand-applied-adopt 1 |
| PersistentVolume | 13 | declared 13 |
| PriorityClass | 4 | declared 4 |

PersistentVolumes provisioned dynamically by Longhorn follow their claims; the static NAS PV `verdify-db-dumps-prod` and every other static PV are jvallery/storage-infra's (R9), whatever this readback count attributes.

### longhorn-system

| Kind | Total | By disposition |
|---|---:|---|
| Engine | 13 | declared 13 |
| Replica | 26 | declared 26 |
| Snapshot | 22 | declared 22 |
| Volume | 13 | declared 13 |
| VolumeAttachment | 13 | declared 13 |

Longhorn Engine/Replica/Snapshot/Volume/VolumeAttachment objects are generated by Longhorn (storage-infra) for this repo's 13 Argo-derived PVCs. They live in jvallery/storage-infra's namespace and are storage-infra's objects; they are counted here only because they derive from this repo's claims, and this repo changes them only through its PVCs. The PVs behind `db-data-verdify-db-0` (`pvc-59fe138d-...`) and the two spool PVCs (`pvc-116e36a0-...`, `pvc-a7158960-...`) are attributed elsewhere by the readback; they follow their claims.

### Itemized declared objects

**verdify-prod**

- ConfigMap (20): `experiment-v2-direct-launch-bootstrap`, `experiment-v2-orchestrator-config`, `verdify-c0-boundary-contract-240-248-c67h948tfk`, `verdify-config`, `verdify-grafana-canvas-css`, `verdify-grafana-dashboards-0`, `verdify-grafana-dashboards-1`, `verdify-grafana-dashboards-2`, `verdify-grafana-dashboards-3`, `verdify-grafana-provisioning`, `verdify-grafana-render-cache-config-87mt2bb78k`, `verdify-ha-gap-backfill-script`, `verdify-hermes-iris-config`, `verdify-ingestor-gather-script`, `verdify-iris-inventory`, `verdify-lab-nginx-config`, `verdify-lab-publisher-config`, `verdify-mqtt-config`, `verdify-vision-src`, `verdify-winter-feasibility-runner`
- CronJob (10): `verdify-band-curve-refresh`, `verdify-db-backup`, `verdify-db-watchdog`, `verdify-firmware-builder`, `verdify-fixed-panel-native-freezer`, `verdify-ha-gap-backfill`, `verdify-lab-publisher`, `verdify-vision`, `verdify-winter-feasibility`, `verdify-writer-watchdog`
- Deployment (15): `experiment-v2-freezer`, `experiment-v2-lifecycle`, `experiment-v2-selector`, `verdify-api`, `verdify-db-backup-exporter`, `verdify-grafana`, `verdify-grafana-render-cache`, `verdify-hermes-iris`, `verdify-ingestor`, `verdify-lab`, `verdify-mcp`, `verdify-mqtt`, `verdify-planner`, `verdify-setpoint-server`, `verdify-traefik`
- IngressRoute (5): `verdify-t2-api`, `verdify-t2-graphs`, `verdify-t2-lab`, `verdify-t2-mcp`, `verdify-tier1-forward`
- Job (20): CronJob-owned runs and Argo one-shots: `verdify-db-backup-29871857`, `verdify-db-backup-29873297`, `verdify-db-watchdog-29873751`, `verdify-fixed-panel-native-freezer-29873190`, `verdify-ha-gap-backfill-29873663`, `verdify-ha-gap-backfill-29873723`, `verdify-lab-publisher-29862110`, `verdify-lab-publisher-29873705`, `verdify-lab-publisher-29873735`, `verdify-vision-29852820`, `verdify-vision-29853000`, `verdify-vision-29853900`, `verdify-vision-29872620`, `verdify-vision-29872800`, `verdify-vision-29872980`, `verdify-vision-29873160`, `verdify-winter-archive-init`, `verdify-winter-feasibility-29871750`, `verdify-winter-feasibility-29873190`, `verdify-writer-watchdog-29873750`
- LimitRange (1): `verdify-prod-defaults`
- Middleware (1): `verdify-strip-identity-headers`
- NetworkPolicy (25): `allow-api-from-ingress`, `allow-backup-exporter-scrape`, `allow-db-from-agent`, `allow-db-from-app`, `allow-db-from-backup`, `allow-db-from-experiment-v2-orchestrator`, `allow-db-from-grafana`, `allow-db-from-ha-gap-backfill`, `allow-db-from-setpoint-server`, `allow-db-from-winter-feasibility`, `allow-firmware-builder-egress`, `allow-grafana-from-ingress`, `allow-grafana-render-cache-from-ingress`, `allow-hermes-from-ingestor`, `allow-ingestor-device-egress`, `allow-mcp-internal`, `allow-metrics-scrape`, `allow-mqtt-fanout`, `allow-verdify-traefik-from-apps-ingress`, `allow-web-tiers-from-verdify-traefik`, `default-deny-ingress`, `experiment-v2-freezer`, `experiment-v2-lifecycle`, `experiment-v2-selector`, `verdify-winter-feasibility-db-only`
- PersistentVolumeClaim (8): `verdify-db-dumps`, `verdify-firmware-artifacts`, `verdify-firmware-toolchain-cache`, `verdify-grafana-data`, `verdify-hermes-iris-data-portable-20260801`, `verdify-ingestor-state`, `verdify-lab-site-cache`, `verdify-winter-feasibility-archive`
- PodDisruptionBudget (8): `verdify-api`, `verdify-grafana`, `verdify-grafana-render-cache`, `verdify-ingestor`, `verdify-lab`, `verdify-mcp`, `verdify-planner`, `verdify-traefik`
- Role (3): `verdify-db-watchdog`, `verdify-ingestor-writer-lease`, `verdify-writer-watchdog`
- RoleBinding (3): `verdify-db-watchdog`, `verdify-ingestor-writer-lease`, `verdify-writer-watchdog`
- Service (11): `verdify-api`, `verdify-db`, `verdify-grafana`, `verdify-grafana-render-cache`, `verdify-hermes-iris`, `verdify-lab`, `verdify-mcp`, `verdify-mqtt`, `verdify-planner`, `verdify-setpoint-server`, `verdify-traefik`
- ServiceAccount (4): `verdify-db-watchdog`, `verdify-ingestor`, `verdify-traefik`, `verdify-writer-watchdog`
- StatefulSet (1): `verdify-db`

**verdify-descheduler**

- ConfigMap (1): `descheduler-policy`
- CronJob (1): `descheduler`
- Job (6): CronJob-owned runs and Argo one-shots: `descheduler-29800020`, `descheduler-29801310`, `descheduler-29801340`, `descheduler-29873595`, `descheduler-29873655`, `descheduler-29873715`
- Role (1): `descheduler`
- RoleBinding (1): `descheduler`
- ServiceAccount (1): `descheduler`

**verdify-db-rehearsal**

- Cluster (1): `verdify-cnpg-s2-integration`
- FailoverQuorum (1): `verdify-cnpg-s2-integration`
- NetworkPolicy (4): `cnpg-s2-integration-runtime-client-ingress`, `cnpg-s2-integration-runtime-client-only`, `deny-all-rehearsal`, `verdify-cnpg-rehearsal-isolation`
- ObjectStore (2): `verdify-cnpg-pitr-reader`, `verdify-cnpg-rehearsal-backup`
- PersistentVolumeClaim (6): `verdify-cnpg-s2-integration-1`, `verdify-cnpg-s2-integration-1-wal`, `verdify-cnpg-s2-integration-2`, `verdify-cnpg-s2-integration-2-wal`, `verdify-cnpg-s2-integration-3`, `verdify-cnpg-s2-integration-3-wal`
- PodDisruptionBudget (2): `verdify-cnpg-s2-integration`, `verdify-cnpg-s2-integration-primary`
- Role (2): `verdify-cnpg-s2-integration`, `verdify-cnpg-s2-integration-barman-cloud`
- RoleBinding (2): `verdify-cnpg-s2-integration`, `verdify-cnpg-s2-integration-barman-cloud`
- Secret (5): `verdify-cnpg-s2-integration-app`, `verdify-cnpg-s2-integration-ca`, `verdify-cnpg-s2-integration-pull`, `verdify-cnpg-s2-integration-replication`, `verdify-cnpg-s2-integration-server`
- Service (3): `verdify-cnpg-s2-integration-r`, `verdify-cnpg-s2-integration-ro`, `verdify-cnpg-s2-integration-rw`
- ServiceAccount (1): `verdify-cnpg-s2-integration`

**cluster-scoped**: PriorityClass `verdify-batch` (1000), `verdify-serving` (100000), `verdify-data-critical` (800000000), `verdify-device-writer` (900000000); ClusterRole and ClusterRoleBinding `verdify-traefik` (Argo `verdify-prod-dark`) and `verdify-descheduler` (Argo `verdify-descheduler`, registered exception); Namespace `verdify-prod` and `verdify-db-rehearsal` (declared), `verdify-descheduler` (hand-applied, A7).

### Secrets in owned namespaces (names only)

| Namespace | Secret | Rendered by | Disposition |
|---|---|---|---|
| verdify-db-rehearsal | `verdify-cnpg-rehearsal-api-client-auth` | agents KSOPS `verdify-db-rehearsal-secrets` | cross-lane-legit |
| verdify-db-rehearsal | `verdify-cnpg-rehearsal-executor-client-auth` | agents KSOPS `verdify-db-rehearsal-secrets` | cross-lane-legit |
| verdify-db-rehearsal | `verdify-cnpg-rehearsal-ingestor-client-auth` | agents KSOPS `verdify-db-rehearsal-secrets` | cross-lane-legit |
| verdify-db-rehearsal | `verdify-cnpg-rehearsal-lifecycle-client-auth` | agents KSOPS `verdify-db-rehearsal-secrets` | cross-lane-legit |
| verdify-db-rehearsal | `verdify-cnpg-rehearsal-mcp-client-auth` | agents KSOPS `verdify-db-rehearsal-secrets` | cross-lane-legit |
| verdify-db-rehearsal | `verdify-cnpg-rehearsal-s3-reader` | agents KSOPS `verdify-db-rehearsal-secrets` | cross-lane-legit |
| verdify-db-rehearsal | `verdify-cnpg-rehearsal-s3-region` | agents KSOPS `verdify-db-rehearsal-secrets` | cross-lane-legit |
| verdify-db-rehearsal | `verdify-cnpg-rehearsal-s3-writer` | agents KSOPS `verdify-db-rehearsal-secrets` | cross-lane-legit |
| verdify-db-rehearsal | `verdify-cnpg-s2-api-client-auth` | kubectl create during S2 runtime/PITR qualification (2026-10-06) | residue-delete |
| verdify-db-rehearsal | `verdify-cnpg-s2-equipment-source-client-auth` | kubectl create during S2 runtime/PITR qualification (2026-10-06) | residue-delete |
| verdify-db-rehearsal | `verdify-cnpg-s2-ingestor-client-auth` | kubectl create during S2 runtime/PITR qualification (2026-10-06) | residue-delete |
| verdify-db-rehearsal | `verdify-cnpg-s2-integration-app` | CNPG operator (from Cluster `verdify-cnpg-s2-integration`) | derived:declared |
| verdify-db-rehearsal | `verdify-cnpg-s2-integration-ca` | CNPG operator (from Cluster `verdify-cnpg-s2-integration`) | derived:declared |
| verdify-db-rehearsal | `verdify-cnpg-s2-integration-pull` | CNPG operator (from Cluster `verdify-cnpg-s2-integration`) | derived:declared |
| verdify-db-rehearsal | `verdify-cnpg-s2-integration-replication` | CNPG operator (from Cluster `verdify-cnpg-s2-integration`) | derived:declared |
| verdify-db-rehearsal | `verdify-cnpg-s2-integration-server` | CNPG operator (from Cluster `verdify-cnpg-s2-integration`) | derived:declared |
| verdify-db-rehearsal | `verdify-cnpg-s2-mcp-client-auth` | kubectl create during S2 runtime/PITR qualification (2026-10-06) | residue-delete |
| verdify-db-rehearsal | `verdify-cnpg-s2-pitr-a-frozen-equipment-source-client-auth` | kubectl create during S2 runtime/PITR qualification (2026-10-06) | residue-delete |
| verdify-db-rehearsal | `verdify-cnpg-s2-pitr-a-frozen-runtime-client-auth` | kubectl create during S2 runtime/PITR qualification (2026-10-06) | residue-delete |
| verdify-db-rehearsal | `verdify-cnpg-s2-pitr-b-frozen-runtime-client-auth` | kubectl create during S2 runtime/PITR qualification (2026-10-06) | residue-delete |
| verdify-db-rehearsal | `verdify-cnpg-s2-runtime-client-auth` | kubectl create during S2 runtime/PITR qualification (2026-10-06) | residue-delete |
| verdify-db-rehearsal | `zot-origin-cluster-pull` | reflector copy of `agent-fleet-runners/zot-origin-cluster-pull` | generated-by-owned-component |
| verdify-descheduler | `zot-origin-cluster-pull` | reflector copy of `agent-fleet-runners/zot-origin-cluster-pull` | generated-by-owned-component |
| verdify-prod | `ghcr-jvallery-readonly` | out-of-band kubectl (no KSOPS source) | hand-applied-adopt |
| verdify-prod | `verdify-agent-secrets` | out-of-band kubectl (no KSOPS source) | hand-applied-adopt |
| verdify-prod | `verdify-app-secrets` | agents KSOPS `verdify-prod-secrets-local-prod` | cross-lane-legit |
| verdify-prod | `verdify-experiment-v2-outcome-freezer-db` | agents KSOPS `verdify-prod-secrets-local-prod` | cross-lane-legit |
| verdify-prod | `verdify-experiment-v2-randomizer-db` | agents KSOPS `verdify-prod-secrets-local-prod` | cross-lane-legit |
| verdify-prod | `verdify-experiment-v2-selector-provider` | agents KSOPS `verdify-prod-secrets-local-prod` | cross-lane-legit |
| verdify-prod | `verdify-experiment-v2-shadow-scheduler-db` | agents KSOPS `verdify-prod-secrets-local-prod` | cross-lane-legit |
| verdify-prod | `verdify-firmware-ota` | out-of-band kubectl (no KSOPS source) | hand-applied-adopt |
| verdify-prod | `verdify-firmware-wifi` | agents KSOPS `verdify-prod-secrets-local-prod` | cross-lane-legit |
| verdify-prod | `verdify-frigate-vision-auth` | agents KSOPS `verdify-prod-secrets-local-prod` | cross-lane-legit |
| verdify-prod | `verdify-github-token` | out-of-band kubectl (no KSOPS source) | hand-applied-adopt |
| verdify-prod | `verdify-grafana-oauth` | agents KSOPS `verdify-prod-secrets-local-prod` | cross-lane-legit |
| verdify-prod | `verdify-grafana-runtime-db` | agents KSOPS `verdify-prod-secrets-local-prod` | cross-lane-legit |
| verdify-prod | `verdify-grafana-secrets` | agents SOPS reconciler | generated-by-owned-component |
| verdify-prod | `verdify-ha-backfill-runtime-db` | agents KSOPS `verdify-prod-secrets-local-prod` | cross-lane-legit |
| verdify-prod | `verdify-ha-token` | agents KSOPS `verdify-prod-secrets-local-prod` | cross-lane-legit |
| verdify-prod | `verdify-hermes` | agents KSOPS `verdify-prod-secrets-local-prod` | cross-lane-legit |
| verdify-prod | `verdify-lab-publisher-runtime-db` | agents KSOPS `verdify-prod-secrets-local-prod` | cross-lane-legit |
| verdify-prod | `verdify-lab-publisher-s3` | out-of-band kubectl (no KSOPS source) | hand-applied-adopt |
| verdify-prod | `verdify-matview-refresh-runtime-db` | agents KSOPS `verdify-prod-secrets-local-prod` | cross-lane-legit |
| verdify-prod | `verdify-mcp-observer` | agents KSOPS `verdify-prod-secrets-local-prod` | cross-lane-legit |
| verdify-prod | `verdify-mcp-runtime-db` | agents KSOPS `verdify-prod-secrets-local-prod` | cross-lane-legit |
| verdify-prod | `verdify-planner-runtime-db` | agents KSOPS `verdify-prod-secrets-local-prod` | cross-lane-legit |
| verdify-prod | `verdify-setpoint-server-runtime-db` | agents KSOPS `verdify-prod-secrets-local-prod` | cross-lane-legit |
| verdify-prod | `verdify-slack` | agents KSOPS `verdify-prod-secrets-local-prod` | cross-lane-legit |
| verdify-prod | `verdify-vision-key` | out-of-band kubectl (no KSOPS source) | hand-applied-adopt |
| verdify-prod | `verdify-vision-runtime-db` | agents KSOPS `verdify-prod-secrets-local-prod` | cross-lane-legit |
| verdify-prod | `verdify-writer-watchdog-runtime-db` | agents KSOPS `verdify-prod-secrets-local-prod` | cross-lane-legit |
| verdify-prod | `zot-origin-cluster-pull` | reflector copy of `agent-fleet-runners/zot-origin-cluster-pull` | cross-lane-legit |

### Cross-lane objects inside owned namespaces

- `kepler-estate-operations` Role and RoleBinding in every Verdify namespace: jvallery/kepler (Argo `kepler-estate`).
- ConfigMap `cnpg-default-monitoring` in `verdify-db-rehearsal`: CNPG operator (agents).
- `zot-origin-cluster-pull` reflector copies and the KSOPS-rendered Secrets above: jvallery/agents.
- `kube-root-ca.crt` ConfigMaps and `default` ServiceAccounts: Kubernetes system.

## Container images

Argo-declared containers (one row per workload container). Digest-pinned = image reference carries `@sha256`.

| Namespace | Workload | Container | Type | Image | Digest-pinned |
|---|---|---|---|---|---|
| verdify-descheduler | CronJob/descheduler | descheduler | container | `registry.k8s.io/descheduler/descheduler:v0.36.0@sha256:7ca92c0a7b4f1c51d306409da6b832005fcadb224e384795646376726734c6e5` | yes |
| verdify-prod | CronJob/verdify-band-curve-refresh | refresh | container | `timescale/timescaledb:2.25.2-pg16` | **no** |
| verdify-prod | CronJob/verdify-db-backup | pg-dump | container | `timescale/timescaledb:2.25.2-pg16` | **no** |
| verdify-prod | CronJob/verdify-db-watchdog | watchdog | container | `python:3.13-alpine` | **no** |
| verdify-prod | CronJob/verdify-firmware-builder | builder | container | `ghcr.io/esphome/esphome:2026.6.5@sha256:ffe23402d169fc9b8ff29fc3c9fc13b3c47e8a53726fce4569e2280bd534c84c` | yes |
| verdify-prod | CronJob/verdify-fixed-panel-native-freezer | freeze-native-route-day | container | `timescale/timescaledb:2.25.2-pg16` | **no** |
| verdify-prod | CronJob/verdify-ha-gap-backfill | backfill | container | `registry.vallery.net/verdifyconsultancy/verdify-ingestor@sha256:4b4994bc68c9e0966d2665d4d4da3e4e7e5d1c2dc8b2694f95db1fa629dc6000` | yes |
| verdify-prod | CronJob/verdify-lab-publisher | prepare-private-work-root | initContainer | `registry.vallery.net/verdifyconsultancy/verdify-lab-publisher-k3s@sha256:e8f799091468f6bcd849b52aa3c620e7245edbd1dc3c466aa3dfad02891f944a` | yes |
| verdify-prod | CronJob/verdify-lab-publisher | publisher | container | `registry.vallery.net/verdifyconsultancy/verdify-lab-publisher-k3s@sha256:e8f799091468f6bcd849b52aa3c620e7245edbd1dc3c466aa3dfad02891f944a` | yes |
| verdify-prod | CronJob/verdify-vision | vision | container | `registry.vallery.net/verdifyconsultancy/verdify-ingestor@sha256:4b4994bc68c9e0966d2665d4d4da3e4e7e5d1c2dc8b2694f95db1fa629dc6000` | yes |
| verdify-prod | CronJob/verdify-winter-feasibility | collect | container | `registry.vallery.net/verdifyconsultancy/verdify-api@sha256:75f510f52d71846197fdc1ddabead87912d8a31070439a29747e4ec36dc37624` | yes |
| verdify-prod | CronJob/verdify-writer-watchdog | lease-check | initContainer | `python:3.13-alpine` | **no** |
| verdify-prod | CronJob/verdify-writer-watchdog | watchdog | container | `timescale/timescaledb:2.25.2-pg16` | **no** |
| verdify-prod | Deployment/experiment-v2-freezer | freezer | container | `registry.vallery.net/verdifyconsultancy/verdify-experiment-v2-orchestrator@sha256:c4fe058ac2a5e21d4d699c5f2f7f95be210b31a35c9e1fcf7385d8c193c8dee0` | yes |
| verdify-prod | Deployment/experiment-v2-lifecycle | lifecycle | container | `registry.vallery.net/verdifyconsultancy/verdify-experiment-v2-orchestrator@sha256:c4fe058ac2a5e21d4d699c5f2f7f95be210b31a35c9e1fcf7385d8c193c8dee0` | yes |
| verdify-prod | Deployment/experiment-v2-selector | selector | container | `registry.vallery.net/verdifyconsultancy/verdify-experiment-v2-orchestrator@sha256:c4fe058ac2a5e21d4d699c5f2f7f95be210b31a35c9e1fcf7385d8c193c8dee0` | yes |
| verdify-prod | Deployment/verdify-api | api | container | `registry.vallery.net/verdifyconsultancy/verdify-api@sha256:701b4dfe22bde2d80d39f5dbb5e18efdf268484634b886f7220d1dbe496c38a4` | yes |
| verdify-prod | Deployment/verdify-api | wait-for-db | initContainer | `timescale/timescaledb:2.25.2-pg16` | **no** |
| verdify-prod | Deployment/verdify-db-backup-exporter | exporter | container | `python:3.12-alpine` | **no** |
| verdify-prod | Deployment/verdify-grafana | grafana | container | `grafana/grafana:12.4.5@sha256:26b8f35a9e4e4431995cf64c3f396505a4faf17bcfc19f9ed84943ec6bfd5ecd` | yes |
| verdify-prod | Deployment/verdify-grafana | inject-canvas-css | initContainer | `grafana/grafana:12.4.5@sha256:26b8f35a9e4e4431995cf64c3f396505a4faf17bcfc19f9ed84943ec6bfd5ecd` | yes |
| verdify-prod | Deployment/verdify-grafana | renderer | container | `grafana/grafana-image-renderer:v5.10.0@sha256:c0eb7b915a181c7bbe451718f9b633843678bef93703b5ed5fda2f28fa508986` | yes |
| verdify-prod | Deployment/verdify-grafana-render-cache | render-cache | container | `nginxinc/nginx-unprivileged:1.29-alpine@sha256:0c79d56aee561a1d81c63f00eee5fb5fe29279560cdc55e91425133104c7fbe6` | yes |
| verdify-prod | Deployment/verdify-hermes-iris | hermes-iris | container | `nousresearch/hermes-agent@sha256:a7111ab1cc43b5a1bc76090a505d6462aa1af4b43f603f0113bf5eb121aec72e` | yes |
| verdify-prod | Deployment/verdify-hermes-iris | seed-config | initContainer | `nousresearch/hermes-agent@sha256:a7111ab1cc43b5a1bc76090a505d6462aa1af4b43f603f0113bf5eb121aec72e` | yes |
| verdify-prod | Deployment/verdify-ingestor | ingestor | container | `registry.vallery.net/verdifyconsultancy/verdify-ingestor@sha256:4b4994bc68c9e0966d2665d4d4da3e4e7e5d1c2dc8b2694f95db1fa629dc6000` | yes |
| verdify-prod | Deployment/verdify-ingestor | install-psql | initContainer | `postgres:16` | **no** |
| verdify-prod | Deployment/verdify-ingestor | wait-for-db | initContainer | `timescale/timescaledb:2.25.2-pg16` | **no** |
| verdify-prod | Deployment/verdify-lab | lab-site | container | `nginxinc/nginx-unprivileged:1.29-alpine@sha256:0c79d56aee561a1d81c63f00eee5fb5fe29279560cdc55e91425133104c7fbe6` | yes |
| verdify-prod | Deployment/verdify-lab | prepare-private-public-root | initContainer | `registry.vallery.net/verdifyconsultancy/verdify-lab-publisher-k3s@sha256:e8f799091468f6bcd849b52aa3c620e7245edbd1dc3c466aa3dfad02891f944a` | yes |
| verdify-prod | Deployment/verdify-mcp | mcp | container | `registry.vallery.net/verdifyconsultancy/verdify-mcp@sha256:ae8a367035a3ae8a06ab2384bc166f444b3decad0da21e5c70e53e76203d9c73` | yes |
| verdify-prod | Deployment/verdify-mqtt | mosquitto | container | `eclipse-mosquitto:2` | **no** |
| verdify-prod | Deployment/verdify-planner | planner | container | `registry.vallery.net/verdifyconsultancy/verdify-planner@sha256:61b1e7aaf510edb4feee38f6c39a949758d50f2aa8a572ef50851d2c4c70b66a` | yes |
| verdify-prod | Deployment/verdify-planner | wait-for-db | initContainer | `timescale/timescaledb:2.25.2-pg16` | **no** |
| verdify-prod | Deployment/verdify-setpoint-server | setpoint-server | container | `registry.vallery.net/verdifyconsultancy/verdify-setpoint-server@sha256:21336cd82de4b9545b67beb84a6f61fc3022fb7bb1c0754a97310a7fb4c28491` | yes |
| verdify-prod | Deployment/verdify-setpoint-server | wait-for-db | initContainer | `timescale/timescaledb:2.25.2-pg16` | **no** |
| verdify-prod | Deployment/verdify-traefik | traefik | container | `docker.io/traefik:v3.7.1` | **no** |
| verdify-prod | StatefulSet/verdify-db | postgres | container | `timescale/timescaledb:2.25.2-pg16` | **no** |

Tag-only images (A12): `docker.io/traefik:v3.7.1`, `eclipse-mosquitto:2`, `postgres:16`, `python:3.12-alpine`, `python:3.13-alpine`, `timescale/timescaledb:2.25.2-pg16`.

Images in Job and bare-Pod residue (hand-applied, all removed with A1/A2): 

| Namespace | Image | Containers |
|---|---|---:|
| verdify-db-rehearsal | `ghcr.io/cloudnative-pg/plugin-barman-cloud-sidecar:v0.14.0@sha256:9880817c285c7afa4d195da2145064d21907405489ed6ec39abe59b1feb558a4` | 2 |
| verdify-db-rehearsal | `grafana/grafana:12.4.5@sha256:26b8f35a9e4e4431995cf64c3f396505a4faf17bcfc19f9ed84943ec6bfd5ecd` | 1 |
| verdify-db-rehearsal | `mirror.gcr.io/grafana/grafana@sha256:26b8f35a9e4e4431995cf64c3f396505a4faf17bcfc19f9ed84943ec6bfd5ecd` | 9 |
| verdify-db-rehearsal | `registry.vallery.net/verdifyconsultancy/verdify-api@sha256:1301683223b2324d6243dbd5fc56ae9f5be515b201e864cd9e758966d824d9fc` | 2 |
| verdify-db-rehearsal | `registry.vallery.net/verdifyconsultancy/verdify-api@sha256:3d5d02f2b22a76b2434367bc51e37672ffd7358ecdc41786faa9120eba162778` | 8 |
| verdify-db-rehearsal | `registry.vallery.net/verdifyconsultancy/verdify-api@sha256:54aaa818bd2d315541ce403f34e3c84ffd6ab3a8e530aa93e56e58dbbcc5e22e` | 1 |
| verdify-db-rehearsal | `registry.vallery.net/verdifyconsultancy/verdify-api@sha256:bdefe3f604c6b6988dbcc45b99742b16a22e39a6778af6dd18c4b74bf1a65194` | 5 |
| verdify-db-rehearsal | `registry.vallery.net/verdifyconsultancy/verdify-ingestor@sha256:2813240dbd5ce3c644d0285fd4603901eef9ae38ad00d28f4e64deb05f46ad2d` | 3 |
| verdify-db-rehearsal | `registry.vallery.net/verdifyconsultancy/verdify-ingestor@sha256:31e88928086b352d61689d2353d48c8b0e4338655d08d71fea1f73987b6a2184` | 1 |
| verdify-db-rehearsal | `registry.vallery.net/verdifyconsultancy/verdify-ingestor@sha256:7649c61d90f2cfac2617d963d214ee70a1f4d61198ef162ce4176608d8f045bb` | 2 |
| verdify-db-rehearsal | `registry.vallery.net/verdifyconsultancy/verdify-ingestor@sha256:7ebdf39e742f834c7f0d04ef9574d793b6c97f7aa2507293daad64d8dc05cce6` | 1 |
| verdify-db-rehearsal | `registry.vallery.net/verdifyconsultancy/verdify-ingestor@sha256:a3492427afab2978d05409f70dbe6342fd4a4cc6cd2bdd2769173bf078f24344` | 18 |
| verdify-db-rehearsal | `registry.vallery.net/verdifyconsultancy/verdify-ingestor@sha256:cbdd4419dd74d9857092a8e7f44f0a7a94d4916b1a6b0a94f3593b81cb70481b` | 7 |
| verdify-db-rehearsal | `registry.vallery.net/verdifyconsultancy/verdify-ingestor@sha256:ce2399170bb9406cd9b90a1b6eac3cd5a483faec72ee00bf67d317daf0508e67` | 5 |
| verdify-db-rehearsal | `registry.vallery.net/verdifyconsultancy/verdify-ingestor@sha256:fc8fe790a9478a463d5e0a94892220e79d92bb1d0ce41abcbf05a5dd71e985e2` | 2 |
| verdify-db-rehearsal | `registry.vallery.net/verdifyconsultancy/verdify-lab-publisher-k3s@sha256:3a678ee015274bd9429a7c2b71361a9ecf7bd96f974fa291d203f731f67d5b52` | 1 |
| verdify-db-rehearsal | `registry.vallery.net/verdifyconsultancy/verdify-lab-publisher-k3s@sha256:b91acc449e447272c332c9084162c393915689de52b99e2646f29defd7524e7e` | 3 |
| verdify-db-rehearsal | `registry.vallery.net/verdifyconsultancy/verdify-mcp@sha256:3092daedf1a4855a6e8e415be505f02e8da11d41d7c37edc63abfef5145dab93` | 8 |
| verdify-db-rehearsal | `registry.vallery.net/verdifyconsultancy/verdify-mcp@sha256:5add35d8bed6195cfae93c8b4aa9dc45ab6c5b42b70cc94973f145a9d0955d2c` | 2 |
| verdify-db-rehearsal | `registry.vallery.net/verdifyconsultancy/verdify-mcp@sha256:8288a08caf448869bfe81b94c0097285b1ba3eccc0be9ef04bdae752ea8f25fe` | 2 |
| verdify-db-rehearsal | `registry.vallery.net/verdifyconsultancy/verdify-mcp@sha256:ff75870eed641608663bfabd012921f4afcff7c30cb33e26b0c84dda8ce769d6` | 1 |
| verdify-db-rehearsal | `registry.vallery.net/verdifyconsultancy/verdify-planner@sha256:61b1e7aaf510edb4feee38f6c39a949758d50f2aa8a572ef50851d2c4c70b66a` | 5 |
| verdify-db-rehearsal | `registry.vallery.net/verdifyconsultancy/verdify-setpoint-server@sha256:6f2fef6a11c7ba67e0b297807c415fda39aa1f2977dcfa170248689087174952` | 5 |
| verdify-db-rehearsal | `registry.vallery.net/verdifyconsultancy/verdify-timescaledb-cnpg:16.13-ts2.25.2@sha256:8b461e37d18aa049704eb6a9cde2ba9af0f955f8d3725e921070d2450bb2f137` | 3 |
| verdify-prod | `nginxinc/nginx-unprivileged:1.29-alpine@sha256:0c79d56aee561a1d81c63f00eee5fb5fe29279560cdc55e91425133104c7fbe6` | 6 |
| verdify-prod | `registry.vallery.net/verdifyconsultancy/verdify-api@sha256:75f510f52d71846197fdc1ddabead87912d8a31070439a29747e4ec36dc37624` | 1 |
| verdify-prod | `registry.vallery.net/verdifyconsultancy/verdify-ingestor@sha256:2813240dbd5ce3c644d0285fd4603901eef9ae38ad00d28f4e64deb05f46ad2d` | 2 |
| verdify-prod | `registry.vallery.net/verdifyconsultancy/verdify-lab-publisher-k3s@sha256:3a678ee015274bd9429a7c2b71361a9ecf7bd96f974fa291d203f731f67d5b52` | 7 |
| verdify-prod | `registry.vallery.net/verdifyconsultancy/verdify-lab-publisher-k3s@sha256:7ee33ce0fb1142c760dbef86d7ddaa560bd79c1dd640db15825abaf414e78b66` | 1 |
| verdify-prod | `registry.vallery.net/verdifyconsultancy/verdify-lab-publisher-k3s@sha256:b91acc449e447272c332c9084162c393915689de52b99e2646f29defd7524e7e` | 6 |
| verdify-prod | `registry.vallery.net/verdifyconsultancy/verdify-lab-publisher-k3s@sha256:c6085836d0cb3a5bd083add258989502c84f5a07a6182f0e501aee0ffac4cee0` | 1 |
| verdify-prod | `registry.vallery.net/verdifyconsultancy/verdify-migrate@sha256:28f00b1d67caee5e1216510028f4eb67d755b09e225b3466ae9c9084a5f9c3fc` | 1 |
| verdify-prod | `timescale/timescaledb:2.25.2-pg16` | 1 |
| verdify-prod | `timescale/timescaledb:2.25.2-pg16@sha256:0af03ecf697825f6ddae76fd275d16bf46007bed6d00eb3d754779cb7db96fa6` | 32 |

The CNPG instance pods in `verdify-db-rehearsal` (`verdify-timescaledb-cnpg:16.13-ts2.25.2@sha256:8b461e37...` and the `plugin-barman-cloud-sidecar:v0.14.0` sidecar) are operator-generated from Cluster `verdify-cnpg-s2-integration`, not hand-applied, although the container readback marks them so.

## Coverage gaps

Items with no probe, alert, metric or dashboard:

- `cluster-object:verdify-db-rehearsal/networkpolicies-residue`: `retire`; delete residue now (non-data; D-04 approved 2026-10-19: prune Jobs now) (A2)
- `cluster-object:verdify-prod/networkpolicies-hand-applied`: `adopt`; resolved by placement rule 1 (namespace owner): adopt `allow-db-from-restore` into `components/db-backup`, or delete it if restore drills stop using it (A5)
- `cluster-object:verdify-prod/networkpolicies-residue`: `retire`; delete residue (A1)
- `repo:verdifyconsultancy/verdify-agent-context`: `retire`; retired (archived evidence); no action
- `repo:verdifyconsultancy/verdify-gravity`: `retire`; retired (archived evidence); no action
- `repo:verdifyconsultancy/verdify-planner`: `retire`; retired (archived evidence; code now in `planner_graph/`); no action
- `repo:verdifyconsultancy/verdify-platform`: `owned`
- `middleware:verdify-prod/aggregate`: `owned`
- `secret:verdify-db-rehearsal/operator-generated/cnpg`: `owned`
- `cluster-object:priorityclass/verdify-batch`: `owned`
- `cluster-object:priorityclass/verdify-data-critical`: `owned`
- `cluster-object:priorityclass/verdify-device-writer`: `owned`
- `cluster-object:priorityclass/verdify-serving`: `owned`
- `cluster-object:rbac/verdify-descheduler`: `owned`; registered estate exception (cluster-wide pod delete); record in map (A7)
- `cluster-object:rbac/verdify-prod-dark`: `owned`; record cluster-scoped `verdify-traefik` ClusterRole/Binding in map (A7)
- `cluster-object:rbac/live-verdify-agent`: `adopt`; delete residue after subject check (A8)
- `alert-path:verdify-writer-watchdog`: `resolve-conflict`; resolved by rule R13 (independent-path list is closed): monitoring-stack `verdify.writer-ownership` alerts replace the watchdogs, then the two CronJobs retire (A10)
- `dashcopy:verdify-platform/product-grafana`: `owned`; (R13 product exception)
- `residue:verdify-platform/ns-verdify-platform-dashboard-cms`: `retire`; delete residue (A9)
- `residue:monitoring-stack/dashboards-staged-verdify`: `resolve-conflict`; R13 product-dashboard exception: product dashboards live here, so monitoring-stack deletes its staged copy and `Greenhouse/*` provisioning copy (A11)
- `namespace:verdify-db-rehearsal`: `adopt`; D-04 approved 2026-10-19: experiment namespace owned by this repo (standing rule); label disposable with an expiry, add to the lane table, retire when S2 closes (A4)
- `other:verdify-product-grafana`: `owned`
- `k8s-workload:verdify-db-rehearsal/cluster/verdify-cnpg-s2-integration`: `owned`; no health signal (A13); D-04 approved 2026-10-19: retire when S2 closes, after a dump and Jason's explicit go (data, R10) (A4)
- `k8s-workload:verdify-db-rehearsal/deployment/verdify-s2-grafana-proof`: `retire`; delete residue now (non-data; D-04 approved 2026-10-19: prune Jobs now) (A2)
- `k8s-workload:verdify-db-rehearsal/job/adhoc-kubectl-jobs`: `retire`; delete residue now (non-data; D-04 approved 2026-10-19: prune Jobs now) (A2)
- `k8s-workload:verdify-prod/job/adhoc-kubectl-jobs`: `retire`; delete residue (A1)
- `k8s-workload:verdify-db-rehearsal/pod/standalone-pods`: `retire`; delete residue now (non-data; D-04 approved 2026-10-19: prune Jobs now) (A2)
- `secret:verdify-db-rehearsal/controller-generated`: `owned`

Items with a signal but no alert (probe or metric only):

- `physical-device:iot/ha-shelly-2b1c9427`: `owned`; kept in this lane by the final record; per D-06 (per-lane device authority) the IoT device-authority row is jvallery/homeassistant's and proxmox-infrastructure links to it; add an alert (A13, A15)
- `physical-device:iot/ha-shelly-b3a979c8`: `owned`; kept in this lane by the final record; per D-06 (per-lane device authority) the IoT device-authority row is jvallery/homeassistant's and proxmox-infrastructure links to it; add an alert (A13, A15)
- `namespace:verdify-descheduler`: `adopt`; P: none; A: none; M: `OBSERVABILITY_REGISTRY namespace:verdify-descheduler tier 1 owner VerdifyConsultancy/platform`, `observability-registry:namespace:verdify-descheduler (owner VerdifyConsultancy/platform)`; D: none
- `namespace:verdify-platform`: P: none; A: none; M: `OBSERVABILITY_REGISTRY namespace:verdify-platform tier 1 owner VerdifyConsultancy/platform`, `observability-registry:namespace:verdify-platform (owner VerdifyConsultancy/platform)`; D: none
- `endpoint:lab.verdify.ai`: `owned`; P: `OBSERVABILITY_REGISTRY HTTPRouteEndpoint lab.verdify.ai`; A: none; M: none; D: none
- `endpoint:labs.verdify.ai`: `owned`; P: `OBSERVABILITY_REGISTRY HTTPRouteEndpoint labs.verdify.ai`; A: none; M: none; D: none
- `endpoint:api.verdify.ai`: `owned`; P: `OBSERVABILITY_REGISTRY HTTPRouteEndpoint api.verdify.ai`; A: none; M: none; D: none
- `endpoint:mcp.verdify.ai`: `owned`; P: `OBSERVABILITY_REGISTRY HTTPRouteEndpoint mcp.verdify.ai`; A: none; M: none; D: none
- `endpoint:graphs.verdify.ai`: `owned`; P: `OBSERVABILITY_REGISTRY HTTPRouteEndpoint graphs.verdify.ai`; A: none; M: none; D: none
- `endpoint:tier1.verdify.ai-hostregexp`: `owned`; P: `OBSERVABILITY_REGISTRY HTTPRouteEndpoint tier1.verdify.ai-hostregexp`; A: none; M: none; D: none
- `k8s-workload:verdify-descheduler/cronjob/descheduler`: `owned`; P: none; A: none; M: `observability-registry:cronjob:verdify-descheduler:descheduler (owner VerdifyConsultancy/platform)`; D: none
- `k8s-workload:verdify-prod/deployment/experiment-v2-freezer`: `owned`; P: ns endpoint probes (7, `[EP]`); A: none; M: none; D: none
- `k8s-workload:verdify-prod/deployment/experiment-v2-lifecycle`: `owned`; P: ns endpoint probes (7, `[EP]`); A: none; M: none; D: none
- `k8s-workload:verdify-prod/deployment/experiment-v2-selector`: `owned`; P: ns endpoint probes (7, `[EP]`); A: none; M: none; D: none

Gaps that matter most (A13): the rehearsal CNPG Cluster `verdify-cnpg-s2-integration` has no CNPG alert binding while it holds 6 data PVCs; the descheduler CronJob (cluster-wide evictions) has no failure alert; the two greenhouse Shelly meters have probes but no alert; the hostname rows for api/lab/labs/mcp/graphs carry only a registry probe entry while their routes carry the real probes and alerts; workload rows inherit only namespace-level `[VNS]` alerts, so per-Deployment availability (for example `verdify-mqtt`, `verdify-hermes-iris`, `experiment-v2-*`) has no dedicated alert. Alert rules themselves are delivered in jvallery/monitoring-stack (R13); this repo supplies metric names and probe keys.

## Decisions applied (2026-10-19)

Jason's decisions of 2026-10-19 override earlier recommendations in this file. Dispositions D-09 to D-21 are approved; non-data cleanups proceed now, while deleting data, revoking credentials or changing domains still needs Jason's explicit go.

- **D-04 (approved 2026-10-19), experiment namespaces.** Standing rule: an experiment namespace is owned by the repo whose session created it, labelled disposable with an expiry, and deleted after its evidence is committed. `verdify-db-rehearsal` is this repo's: prune its Jobs and other non-data residue now (A2); retire the namespace when S2 closes (A4); the CNPG data PVCs, spool PVCs (A3) and the `verdify-cnpg-rehearsal` bucket go only after a dump/snapshot and Jason's explicit go.
- **D-06, per-lane device authority.** Device-authority rows are per lane: IoT (Powerwall, Ring chimes, appliances, Shelly meters, ESP32 controllers) → jvallery/homeassistant; cameras → jvallery/sentinel; network gear → jvallery/network-infra; compute and NAS hardware → jvallery/proxmox-infrastructure, which links to the others. The final record keeps the greenhouse controller, probes and the two Shelly meters owned here for product purposes; their device rows are homeassistant's (A15).
- **D-03, provider keys and zot credentials.** ROOT-minted B2/Garage provider keys are an R8 exception owned by jvallery/storage-infra (escrow in recovery-key-custody); zot htpasswd/robot credentials move to the jvallery/agents KSOPS lane (a). The zot pull secrets in `verdify-prod` and `verdify-edge` follow that lane; unconsumed copies are deleted under SEC-02 (credential revocation needs Jason's go).
- **D-16 (approved 2026-10-19), dead hostnames.** `analytics`, `logs`, `botauth` and `traefik.verdify.ai` (404), the `verdify-edge-canary` Worker, the `cf-*-canary.verdify.ai` CNAMEs and tunnel `verdify-gateway-cutover` retire through jvallery/network-infra. This repo only drops them from the tier-1 HostRegexp exclusion list if listed there; the DNS/domain changes need Jason's explicit go (A14).
- **D-08.** monitoring.coreos.com CRDs are jvallery/monitoring-stack's shared-API record; a `PrometheusRule` here stays inert and forbidden.
- **Resolved by rule.** Watchdogs (R13): replace with monitoring-stack alerts (A10). NetworkPolicy `allow-db-from-restore`: namespace owner, this repo (A5). `verdify-prod-dark` adopted off `app-test` (agents#4461).

## Open actions

Delete actions never remove a data PVC, PV or database without a snapshot and Jason's explicit instruction (R10). Before deleting a NetworkPolicy, re-check that its selector still matches no pod. Keep any Job log that is cited as evidence before deleting the Job.

### A1 Delete verdify-prod run residue

Items: `k8s-workload:verdify-prod/job/adhoc-kubectl-jobs`, `cluster-object:verdify-prod/networkpolicies-residue`. No data PVCs.

<details><summary>Objects</summary>

- Job (44): `greenhouse-quiet-60m`, `verdify-backup-pair-diagnose-20260927`, `verdify-backup-pair-restore-20260927`, `verdify-backup-pair-restore-c0255diff0928`, `verdify-backup-pair-restore-c0255digest0928`, `verdify-backup-pair-restore-c0255exactdb0928`, `verdify-backup-pair-restore-c0261q0928`, `verdify-backup-pair-restore-c0full0928`, `verdify-backup-pair-restore-c0target0928`, `verdify-backup-pair-restore-c5owner0927`, `verdify-backup-pair-restore-climate2661001a`, `verdify-backup-pair-restore-climate2661001b`, `verdify-backup-pair-restore-diag-0927`, `verdify-backup-pair-restore-ediff-0927`, `verdify-backup-pair-restore-effacl-0927`, `verdify-backup-pair-restore-observation2671001a`, `verdify-backup-pair-restore-observation2671001b`, `verdify-backup-pair-restore-owner217-0927`, `verdify-backup-pair-restore-owner672-0927`, `verdify-backup-pair-restore-parity-0927`, `verdify-backup-pair-restore-role2681001a`, `verdify-backup-pair-restore-role2681001b`, `verdify-backup-pair-restore-role2681001c`, `verdify-backup-pair-restore-role2691001a`, `verdify-backup-pair-restore-scheduled-0927`, `verdify-backup-pair-restore-v2-20260927`, `verdify-backup-pair-restore-v2cur2540928`, `verdify-backup-pair-restore-v2cur2550928`, `verdify-backup-pair-restore-v2int0928`, `verdify-backup-pair-restore-verify3`, `verdify-backup-role-parity-diagnose-20260927`, `verdify-c0-restore-probe-20260926`, `verdify-db-backup-pair-verify-20260927`, `verdify-lab-receipt-singlelink-repair-20261013`, `verdify-logical-restore-audit-20260926`, `verdify-s2-lab-image-warm-node4-20261006`, `verdify-s2-lab-image-warm-node4-20261011`, `verdify-s2-lab-image-warm-node5-20261006`, `verdify-s2-lab-image-warm-node5-20261011`, `verdify-s2-lab-image-warm-node6-20261006`, `verdify-s2-lab-image-warm-node6-20261011`, `verdify-s2-role-ha-backfill`, `verdify-s2-role-lab-publisher`, `verdify-s2-role-vision`
- ConfigMap (38): `operator-rehearsal-probe-files`, `operator-rehearsal-probe-script`, `verdify-backup-pair-restore-20260927-scripts`, `verdify-backup-pair-restore-c0255diff0928-scripts`, `verdify-backup-pair-restore-c0255digest0928-scripts`, `verdify-backup-pair-restore-c0255exactdb0928-scripts`, `verdify-backup-pair-restore-c0261q0928-scripts`, `verdify-backup-pair-restore-c0full0928-scripts`, `verdify-backup-pair-restore-c0target0928-scripts`, `verdify-backup-pair-restore-c5owner0927-scripts`, `verdify-backup-pair-restore-climate2661001a-scripts`, `verdify-backup-pair-restore-climate2661001b-scripts`, `verdify-backup-pair-restore-diag-0927-scripts`, `verdify-backup-pair-restore-ediff-0927-scripts`, `verdify-backup-pair-restore-effacl-0927-scripts`, `verdify-backup-pair-restore-observation2671001a-scripts`, `verdify-backup-pair-restore-observation2671001b-scripts`, `verdify-backup-pair-restore-owner217-0927-scripts`, `verdify-backup-pair-restore-owner217-0927-source`, `verdify-backup-pair-restore-owner672-0927-scripts`, `verdify-backup-pair-restore-parity-0927-scripts`, `verdify-backup-pair-restore-role2681001a-scripts`, `verdify-backup-pair-restore-role2681001b-scripts`, `verdify-backup-pair-restore-role2681001c-scripts`, `verdify-backup-pair-restore-role2691001a-scripts`, `verdify-backup-pair-restore-scheduled-0927-scripts`, `verdify-backup-pair-restore-v2-20260927-scripts`, `verdify-backup-pair-restore-v2cur2540928-scripts`, `verdify-backup-pair-restore-v2cur2550928-scripts`, `verdify-backup-pair-restore-v2int0928-scripts`, `verdify-backup-pair-restore-verify3-scripts`, `verdify-c0-entrypoint-contract-20260926`, `verdify-c0-restore-probe-20260926-script`, `verdify-c0-restore-probe-20260926-sql`, `verdify-logical-restore-audit-20260926-audit-sql`, `verdify-logical-restore-audit-20260926-script`, `verdify-runtime-login-probe-20261011`, `verdify-runtime-qualification-s2`
- NetworkPolicy (36): `verdify-backup-pair-restore-20260927-deny-all`, `verdify-backup-pair-restore-c0255diff0928-deny-all`, `verdify-backup-pair-restore-c0255digest0928-deny-all`, `verdify-backup-pair-restore-c0255exactdb0928-deny-all`, `verdify-backup-pair-restore-c0261q0928-deny-all`, `verdify-backup-pair-restore-c0full0928-deny-all`, `verdify-backup-pair-restore-c0target0928-deny-all`, `verdify-backup-pair-restore-c5owner0927-deny-all`, `verdify-backup-pair-restore-climate2661001a-deny-all`, `verdify-backup-pair-restore-climate2661001b-deny-all`, `verdify-backup-pair-restore-diag-0927-deny-all`, `verdify-backup-pair-restore-ediff-0927-deny-all`, `verdify-backup-pair-restore-effacl-0927-deny-all`, `verdify-backup-pair-restore-observation2671001a-deny-all`, `verdify-backup-pair-restore-observation2671001b-deny-all`, `verdify-backup-pair-restore-owner217-0927-deny-all`, `verdify-backup-pair-restore-owner672-0927-deny-all`, `verdify-backup-pair-restore-parity-0927-deny-all`, `verdify-backup-pair-restore-role2681001a-deny-all`, `verdify-backup-pair-restore-role2681001b-deny-all`, `verdify-backup-pair-restore-role2681001c-deny-all`, `verdify-backup-pair-restore-role2691001a-deny-all`, `verdify-backup-pair-restore-scheduled-0927-deny-all`, `verdify-backup-pair-restore-v2-20260927-deny-all`, `verdify-backup-pair-restore-v2cur2540928-deny-all`, `verdify-backup-pair-restore-v2cur2550928-deny-all`, `verdify-backup-pair-restore-v2int0928-deny-all`, `verdify-backup-pair-restore-verify3-deny-all`, `verdify-c0-contract-clone-deny-all-20260926`, `verdify-c0-entrypoint-clone-deny-all-20260926`, `verdify-c0-main-entrypoint-clone-20260926-deny-all`, `verdify-c0-restore-probe-20260926-deny-all`, `verdify-logical-restore-audit-20260926-deny-all`, `verdify-runtime-login-probe-20261011`, `verdify-s2-lab-image-warm-deny-all-20261006`, `verdify-s2-lab-image-warm-deny-all-20261011`

</details>

### A2 Delete verdify-db-rehearsal run residue (non-data; approved 2026-10-19, D-04)

Items: `k8s-workload:verdify-db-rehearsal/job/adhoc-kubectl-jobs`, `k8s-workload:verdify-db-rehearsal/pod/standalone-pods`, `k8s-workload:verdify-db-rehearsal/deployment/verdify-s2-grafana-proof`, `cluster-object:verdify-db-rehearsal/networkpolicies-residue`.

<details><summary>Objects</summary>

- Job (81): `cnpg-runtime-api-202610020212`, `cnpg-runtime-api-202610020229`, `cnpg-runtime-api-20261002030039`, `cnpg-runtime-api-20261002031631`, `cnpg-runtime-api-20261002032535`, `cnpg-runtime-api-20261002035514`, `cnpg-runtime-api-20261002041608`, `cnpg-runtime-api-20261002070752`, `cnpg-runtime-ingestor-202610020212`, `cnpg-runtime-ingestor-202610020229`, `cnpg-runtime-ingestor-20261002031632`, `cnpg-runtime-ingestor-20261002035515`, `cnpg-runtime-ingestor-20261002070753`, `cnpg-runtime-mcp-202610020212`, `cnpg-runtime-mcp-202610020229`, `cnpg-runtime-mcp-20261002031633`, `cnpg-runtime-mcp-20261002035516`, `cnpg-runtime-mcp-20261002070755`, `s2-current274-20261006t053219-6a881d23-markers`, `s2-current274-20261006t053802-07219146-markers`, `s2-failover-20261006t063201-once`, `verdify-cnpg-s2-archive-recheck-20261012`, `verdify-cnpg-s2-protected-archive-reader`, `verdify-s2-business-api`, `verdify-s2-business-api-transport`, `verdify-s2-business-ingestor-transport`, `verdify-s2-business-ingestor-transport-typed`, `verdify-s2-business-mcp-transport-typed`, `verdify-s2-config-api-before`, `verdify-s2-config-api-refuse`, `verdify-s2-config-api-restored`, `verdify-s2-config-ingestor-before`, `verdify-s2-config-ingestor-before-natural`, `verdify-s2-config-ingestor-refuse`, `verdify-s2-config-ingestor-restored`, `verdify-s2-config-ingestor-restored-cached`, `verdify-s2-config-mcp-before`, `verdify-s2-config-mcp-refuse`, `verdify-s2-config-mcp-restored`, `verdify-s2-duty-grafana-transport`, `verdify-s2-duty-ha-backfill-transport`, `verdify-s2-duty-lab-publisher-transport`, `verdify-s2-duty-planner`, `verdify-s2-duty-setpoint-server`, `verdify-s2-duty-setpoint-server-transport`, `verdify-s2-duty-vision-transport`, `verdify-s2-endpoint-api-all`, `verdify-s2-endpoint-api-all-a275-p2`, `verdify-s2-endpoint-api-all-a2b3`, `verdify-s2-endpoint-grafana-before`, `verdify-s2-endpoint-grafana-before-a275-p2`, `verdify-s2-endpoint-grafana-before-a2b3`, `verdify-s2-endpoint-grafana-restored`, `verdify-s2-endpoint-grafana-restored-a275-p2`, `verdify-s2-endpoint-grafana-restored-a2b3`, `verdify-s2-endpoint-grafana-target`, `verdify-s2-endpoint-grafana-target-a275-p2`, `verdify-s2-endpoint-grafana-target-a2b3`, `verdify-s2-endpoint-ha-backfill-all`, `verdify-s2-endpoint-ha-backfill-all-a275-p2`, `verdify-s2-endpoint-ha-backfill-all-a2b3`, `verdify-s2-endpoint-ingestor-all`, `verdify-s2-endpoint-ingestor-all-a275-p2`, `verdify-s2-endpoint-ingestor-all-a2b3`, `verdify-s2-endpoint-lab-publisher-all`, `verdify-s2-endpoint-lab-publisher-all-a275-p2`, `verdify-s2-endpoint-lab-publisher-all-a2b3`, `verdify-s2-endpoint-mcp-all`, `verdify-s2-endpoint-mcp-all-a275-p2`, `verdify-s2-endpoint-mcp-all-a2b3`, `verdify-s2-endpoint-mcp-all-a2b3-alias-fix`, `verdify-s2-endpoint-planner-all`, `verdify-s2-endpoint-planner-all-a275-p2`, `verdify-s2-endpoint-planner-all-a2b3`, `verdify-s2-endpoint-planner-all-a2b3-uri-fix`, `verdify-s2-endpoint-setpoint-server-all`, `verdify-s2-endpoint-setpoint-server-all-a275-p2`, `verdify-s2-endpoint-setpoint-server-all-a2b3`, `verdify-s2-endpoint-vision-all`, `verdify-s2-endpoint-vision-all-a275-p2`, `verdify-s2-endpoint-vision-all-a2b3`
- Pod (6): `verdify-s2-promoted-spool-image-metadata`, `verdify-s2-promoted-spool-qualification`, `verdify-s2-promoted-spool-qualification-v2`, `verdify-s2-promoted-spool-qualification-v3`, `verdify-s2-promoted-spool-qualification-v4`, `verdify-s2-spool-recover`
- Deployment (1): `verdify-s2-grafana-proof`
- ConfigMap (16): `verdify-s2-business-qualification`, `verdify-s2-business-qualification-transport`, `verdify-s2-business-qualification-transport-typed`, `verdify-s2-duty-qualification`, `verdify-s2-duty-qualification-transport`, `verdify-s2-endpoint-reversal`, `verdify-s2-endpoint-reversal-a275-p2`, `verdify-s2-endpoint-reversal-a2b3`, `verdify-s2-endpoint-reversal-a2b3-mcp-alias-fix`, `verdify-s2-endpoint-reversal-a2b3-planner-uri-fix`, `verdify-s2-grafana-proof`, `verdify-s2-promoted-spool-runner`, `verdify-s2-promoted-spool-runner-v2`, `verdify-s2-promoted-spool-runner-v3`, `verdify-s2-promoted-spool-runner-v4`, `verdify-s2-spool-fault-runner`
- NetworkPolicy (14): `cnpg-runtime-client-ingress`, `cnpg-runtime-client-only`, `cnpg-s2-archive-reader-only`, `cnpg-s2-frozen-b-failover-client-ingress`, `cnpg-s2-frozen-b-failover-client-only`, `verdify-a275-endpoint-source-egress`, `verdify-a275-endpoint-source-ingress`, `verdify-s2-endpoint-b-egress`, `verdify-s2-endpoint-b-egress-a275-p2`, `verdify-s2-endpoint-b-egress-a2b3`, `verdify-s2-endpoint-b-ingress`, `verdify-s2-endpoint-b-ingress-a275-p2`, `verdify-s2-endpoint-b-ingress-a2b3`, `verdify-s2-spool-device-denied`
- Secret (8): `verdify-cnpg-s2-api-client-auth`, `verdify-cnpg-s2-equipment-source-client-auth`, `verdify-cnpg-s2-ingestor-client-auth`, `verdify-cnpg-s2-mcp-client-auth`, `verdify-cnpg-s2-pitr-a-frozen-equipment-source-client-auth`, `verdify-cnpg-s2-pitr-a-frozen-runtime-client-auth`, `verdify-cnpg-s2-pitr-b-frozen-runtime-client-auth`, `verdify-cnpg-s2-runtime-client-auth`

</details>

### A3 Delete rehearsal spool PVCs (data, R10)

- PersistentVolumeClaim (2): `verdify-s2-spool-382-7fa4f802`, `verdify-s2-spool-qualification`. D-04 (approved 2026-10-19) sets the retirement path, but these hold data: snapshot first; delete only on Jason's explicit go.

### A4 Retire verdify-db-rehearsal when S2 closes (approved 2026-10-19, D-04)

Approved 2026-10-19 (D-04): `verdify-db-rehearsal` stays this repo's experiment namespace until S2 closes, then retires.

Items: `namespace:verdify-db-rehearsal`, `argo-app:argocd/verdify-db-rehearsal-namespace`, `argo-app:argocd/verdify-db-rehearsal-cluster` (OutOfSync), `k8s-workload:verdify-db-rehearsal/cluster/verdify-cnpg-s2-integration`, both ObjectStores, the six CNPG PVCs, both Secret groups. Now: label the namespace disposable with an expiry, add it to the lane table, sync to adopt NetworkPolicy (2) `cnpg-s2-runtime-client-ingress`, `cnpg-s2-runtime-client-only`, and add CNPG alert coverage while it holds data. At S2 close: dump, then on Jason's explicit go delete the Cluster, its PVCs and the `verdify-cnpg-rehearsal` Garage bucket (storage-infra); agents retires the two Applications, AppProject `app-verdify-cnpg-rehearsal` and the 8 KSOPS Secrets.

### A5 Adopt NetworkPolicy allow-db-from-restore

- NetworkPolicy (1): `allow-db-from-restore` (label `managed-by=laptop-root-interim`; companion of `db/restore-job.yaml`). Resolved by placement rule 1: this repo owns it as namespace owner. Adopt into `deploy/k8s/components/db-backup` while restore drills use it; delete it when they stop.

### A6 Adopt the verdify-db data PVC

- PersistentVolumeClaim (1): `db-data-verdify-db-0`: pre-created by kubectl and bound by the `db-data` volumeClaimTemplate. Declared in `deploy/k8s/overlays/prod/db-data-protected-pvc.yaml` with `Prune=false,Delete=false`, retaining its exact 50Gi Longhorn class and existing bound volume. The central map records it as protected; verify live annotation/UID after adoption (#994). Never prune it (R10); neither claim deletion nor StatefulSet recreation is a rollback.

### A7 Adopt the verdify-descheduler Namespace and record cluster-scoped objects

- Namespace (1): `verdify-descheduler` (excluded from the #802 seven-object adoption). Record PriorityClasses `verdify-*` and ClusterRole/Binding `verdify-descheduler` in the central map (R1 cluster-scoped entries). #1006 replaces the tier-2 Kubernetes provider with Git-generated file routing: ServiceAccount `verdify-traefik` has token automount disabled and no Role/Binding; retire the old same-named ClusterRole/Binding only after the tokenless rollout is verified.

### A8 Delete verdify-agent RBAC residue

- `cluster-object:rbac/live-verdify-agent`: `adopt`; ClusterRole and ClusterRoleBinding `verdify-agent-cluster-readonly` (live-only).
- Role (1): `verdify-agent-prod-restricted`; RoleBinding (1): `verdify-agent-prod-restricted` (laptop-root apply 2026-05-31).

### A9 Delete dashboard ConfigMap copies in ns verdify-platform

- ConfigMap (4): `verdify-grafana-dashboards-0`, `verdify-grafana-dashboards-1`, `verdify-grafana-dashboards-2`, `verdify-grafana-dashboards-3`. The namespace itself and `ci-dbg-*` residue are jvallery/agents'.

### A10 Replace the writer/DB watchdogs with monitoring-stack alerts

- `alert-path:verdify-writer-watchdog`: resolved by rule R13 (the independent-path list is closed). monitoring-stack adds `verdify.writer-ownership` rules covering writer and DB death; once they fire in a test, retire CronJobs `verdify-writer-watchdog` and `verdify-db-watchdog` here (non-data). Tracked with [monitoring-stack#834](https://github.com/jvallery/monitoring-stack/issues/834).

### A11 Remove the monitoring-stack staged dashboard copy

- `residue:monitoring-stack/dashboards-staged-verdify`: `resolve-conflict`; R13 product-dashboard exception: product dashboards live here, so monitoring-stack deletes its staged copy and `Greenhouse/*` provisioning copy (A11)

### A12 Pin tag-only images by digest

- `docker.io/traefik:v3.7.1`, `eclipse-mosquitto:2`, `postgres:16`, `python:3.12-alpine`, `python:3.13-alpine`, `timescale/timescaledb:2.25.2-pg16` in `verdify-db`, `verdify-api`, `verdify-ingestor`, `verdify-planner`, `verdify-setpoint-server`, `verdify-mqtt`, `verdify-db-backup-exporter`, `verdify-traefik` and CronJobs `verdify-band-curve-refresh`, `verdify-db-backup`, `verdify-db-watchdog`, `verdify-fixed-panel-native-freezer`, `verdify-writer-watchdog`.

## Meter source clock (#981)

The read-only ingestor consumer uses the HA-owned Shelly source contract with explicit `shelly_modbus_source_v2` evidence. Original channel IDs, signed power, source/observation clocks and missingness are preserved. Device identity/endpoint remain owned by homeassistant; the consumer and its exact runtime release pin belong here. See `docs/operations/SHELLY_SOURCE_CLOCK_CONSUMER.md`.

### A13 Close health coverage gaps

- See [Coverage gaps](#coverage-gaps); also probes 04, 08 and 09 of `physical-device:iot-aggregate/greenhouse-probes` are unavailable (product issue #751).

### A14 Transitions tracked elsewhere

- `argo-app:argocd/verdify-prod-dark` → agents local-prod catalog ([agents#4461](https://github.com/jvallery/agents/issues/4461)).
- Out-of-band Secrets in `verdify-prod` → agents KSOPS lane ([agents#4470](https://github.com/jvallery/agents/issues/4470)): `ghcr-jvallery-readonly`, `verdify-agent-secrets`, `verdify-firmware-ota`, `verdify-github-token`, `verdify-lab-publisher-s3`, `verdify-vision-key`.
- Namespace `verdify-edge`: the Namespace object is this repo's (`retire`); its wildcard Certificate children (CertificateRequest `wildcard-verdify-ai-3`, Order `wildcard-verdify-ai-3-528873999`, Secret `wildcard-verdify-ai-tls`) are removed by jvallery/agents under [agents#4468](https://github.com/jvallery/agents/issues/4468); then delete the namespace (non-data).
- zot pull credentials → jvallery/agents KSOPS lane (D-03, approved 2026-10-19); unconsumed copies deleted under SEC-02 only with Jason's go for revocation.
- Dead `*.verdify.ai` hostnames and canaries (D-16, approved 2026-10-19) → jvallery/network-infra; domain/DNS changes need Jason's explicit go. Drop any matching entries from the tier-1 HostRegexp exclusion list here.

### A15 Greenhouse device rows under per-lane device authority (D-06)

- `physical-device:iot/ha-shelly-2b1c9427`, `physical-device:iot/ha-shelly-b3a979c8`: the final record keeps the greenhouse meters in this lane (status `owned`). Per D-06 the IoT device-authority rows (Shellys and the ESP32 controller/probes) belong to jvallery/homeassistant, with proxmox-infrastructure linking to them; this file links rather than copies. Remaining work: add an alert for the meters (A13).

### S6 saved public Grafana (#1002)

The 20 `site-*` dashboards and the existing Greenhouse: Weather dashboard use
native externally shared saved queries (21 total, explicitly approved);
`verdify_public/grafana_catalog.json` records approved public links and panel IDs.
The API filters public panel metadata and uses the existing renderer credential
for bounded PNG generation. Renderer Service port 8081 admits API pods only;
Grafana administration stays on existing SSO. No datasource credentials or
arbitrary render URLs are exposed. See `docs/runbooks/grafana-saved-public.md`.

## Contact verification (#1008)

The Lab contact form renders the managed Turnstile widget for `lab.verdify.ai`; the API checks the token, hostname and `contact` action before acquiring a database connection. Central credential custody is `jvallery/agents`, Secret `verdify-prod/verdify-contact-turnstile`. Cloudflare widget and camera edge rules belong to `jvallery/network-infra`.


## Rehearsal non-data cleanup (#991)

The 81 inactive standalone Jobs, six terminal bare Pods, zero-replica proof Deployment, 16 runner ConfigMaps, 14 orphan policies and eight untracked client-auth copies were removed after exact preconditions and private evidence preservation. All eight PVCs, the CNPG Cluster, two protected runtime policies, and both Applications remain unchanged. See `planning/execution/2026-10-20-followthrough/LIVE-RECEIPTS.md`; #992 remains subject to verified dump/snapshot custody.
