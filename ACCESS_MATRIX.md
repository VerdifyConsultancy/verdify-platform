# Verdify Access Matrix

Last updated: 2026-06-12

This matrix follows the least-privilege lane model. The objective did not supply
a concrete namespace, so live Kubernetes access is listed as missing rather than
assumed.

| Resource | Current access | Required access | Reason | Scope | Risk | Owner | Status |
|---|---|---|---|---|---|---|---|
| Primary repo `VerdifyConsultancy/verdify-platform` | Local read/write checkout | Read/write to repo files | Maintain app source, manifests, docs, CI | Single repo | Medium: can change deployable app code | Jason / repo admins | Granted locally |
| Git history/status | Local read | Read | Understand branch, pending changes, provenance | Single repo | Low | Jason / repo admins | Granted locally |
| GitHub Issues/PRs for this repo | Not used in this pass | Repo-scoped read/write when needed | Tracker, PR/issue handoff, CI triage | Single repo | Medium | Repo admins | Not exercised |
| GitHub Actions for this repo | Repo files read locally | Repo-scoped read/dispatch when needed | Inspect and trigger app CI/CD | Single repo | Medium if dispatching deploy workflows | Repo admins | Files granted; dispatch not exercised |
| GHCR packages for Verdify images | Manifest refs only | Repo/org package read; package write only via CI | Pull/publish app images | Verdify packages only | Medium | Repo admins / CI | Not directly exercised |
| Assigned Kubernetes namespace | None; namespace placeholder unresolved | Read namespace-local resources only | Verify live workloads, Services, ConfigMaps, Secret names, PVCs | One assigned namespace | Low for read-only | Orbit / platform agent | Missing |
| Namespace-local Kubernetes writes | None | Only explicit resource-level write after approval | Apply app config/manifests in assigned namespace | One assigned namespace | High in prod/device path | Orbit / Jason gate | Missing |
| `verdify-dev` namespace | Repo manifests only | Read if assigned | Verify proving env | Namespace only | Low for read-only | Orbit / platform agent | Pending assignment |
| `verdify-prod` namespace | Repo manifests only | Read if assigned; writes Jason-gated | Verify prod app state | Namespace only | High: live greenhouse | Orbit + Jason | Pending assignment |
| Kubernetes Secrets in assigned namespace | Names/keys from manifests only; no values | List/get metadata only; no values unless explicitly needed by workload automation | Confirm presence, consumers, schema | Namespace Secrets metadata | High if values exposed | Root / Orbit | Missing |
| SOPS/Age private key | No access | No access requested | Secret values are out of lane | None | Critical | Root / Orbit | Not requested |
| DNS/Cloudflare/tunnels | No access | No direct access; coordination request only | Public routes depend on platform edge | None | High | Network Infra / Orbit | Out of scope |
| Ingress controller/shared Traefik | No live access | No direct access; coordination request only | App IngressRoutes depend on shared edge | None | High | Network Infra / Orbit | Out of scope |
| Storage backends / StorageClasses / PVs | Repo manifests only | No direct access; coordination request only | PVC binding and backups need storage platform | None | High | Storage Infra / Orbit | Out of scope |
| Shared monitoring platform | Repo Grafana manifests only | No direct access; coordination request only | Dashboards/alerts may need shared platform | None | Medium | Monitoring agent / Orbit | Out of scope |
| ESP32/device VLAN | No action taken | No direct access except Jason-approved diagnostics | Live device writer path | Device-specific | Critical | Jason + Network Infra | Out of scope/gated |
| Home Assistant | Secret refs and URL only | No direct access unless task-specific and approved | HA telemetry and grow-light path | HA API only | High for device-adjacent writes | Jason / platform owner | Out of scope/gated |
| OpenAI/Slack accounts | Secret refs only | No direct account access | Planner and alert integrations | App credentials only | Medium | Jason / Root | Out of scope |
