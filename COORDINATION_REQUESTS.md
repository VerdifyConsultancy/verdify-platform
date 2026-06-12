# Verdify Coordination Requests

Last updated: 2026-06-12

Use this file for out-of-lane asks. Do not request broad privileges; keep each
ask scoped to the minimum action.

| Requesting agent | Target agent | Needed action | Context | Blocking? | Deadline | Minimal required access |
|---|---|---|---|---|---|---|
| Verdify app agent | Orbit/user | Provide concrete assigned namespace and environment for this lane (`verdify-dev` or `verdify-prod`, or another explicit namespace). | The lane prompt used placeholders, so live namespace discovery was intentionally skipped. | Yes, for live inventory verification | Before any live Kubernetes discovery | User confirmation only |
| Verdify app agent | Platform GitOps agent | Confirm which ArgoCD app currently owns the assigned namespace and whether `prod-dark` is still active or only legacy. | Repo has `verdify-dev`, `verdify-prod`, `verdify-prod-dark`, and retired staging manifests. | No for repo docs; yes for live reconciliation work | Before deploy/sync work | Read-only app metadata for the assigned namespace/app |
| Verdify app agent | Root / secret-delivery agent | Verify Secret presence by name in the assigned namespace without exposing values: `verdify-app-secrets`, image pull secret, and any env-specific HA/Hermes/Grafana secrets. | `SECRETS_AUDIT.md` is repo-derived only. | Yes, for live readiness claims | Before claiming namespace-ready | Namespace-local Secret metadata only; no values |
| Verdify app agent | Network Infra agent | Confirm public route ownership for assigned env domains and ingress-controller prerequisites. | IngressRoutes reference `*.verdify.ai` and `*.k3s.verdify.ai`; DNS/Cloudflare/Traefik are out of lane. | No for docs; yes for route changes | Before public route changes | Route status for named hosts only |
| Verdify app agent | Storage Infra agent | Confirm PVC/PV bindings and backup storage policy for assigned namespace. | Prod uses `verdify-db-dumps`, `verdify-hermes-iris-data`, `verdify-ingestor-state`; dev uses prod-dumps RO restore PVC. | Yes, for storage/backup readiness | Before DB/storage changes | Namespace PVC status and named PV/storageclass status |
| Verdify app agent | Monitoring agent | Confirm which app dashboards/alerts are owned here versus shared monitoring. | Repo includes Grafana component and public graphs route, but shared monitoring is out of lane. | No | Before alert/dashboard expansion | Read-only dashboard/alert inventory for Verdify surfaces |
| Verdify app agent | Jason + Network Infra agent | Approve any ESP32/device VLAN action or prod device-write egress change. | Prod overlay includes `allow-ingestor-device-egress`; device actions are Jason-gated. | Yes, for any device-impacting work | Before device-write changes | Explicit approval; no standing broad access |
| Verdify app agent | Jason + Root / secret-delivery agent | Confirm and seal ESP32/HA/OpenAI/Hermes secret changes if ever needed. | Secret values and rotations are out of lane and must not be printed. | Yes, when credentials change | Before runtime secret changes | Secret-delivery action by owner; app agent gets names/status only |
