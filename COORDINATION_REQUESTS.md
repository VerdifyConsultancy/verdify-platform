# Verdify Platform Coordination Requests

Last updated: 2026-06-13

Agent name: `verdify-platform`

Use this file for out-of-lane asks. Keep each request scoped to the minimum
action and create/update a GitHub issue when the request blocks delivery.

| Requesting agent | Target agent | Needed action | Blocking? | Deadline | Minimal access/action |
|---|---|---|---|---|---|
| `verdify-platform` | Board owner | Expose or identify `Agent Command Center Kanban` owner/project number, or confirm Verdify should use `Verdify Platform` project #1. | Yes for Project field completion | Before claiming board setup complete | Project metadata/write access only |
| `verdify-platform` | `storage-infra` | Confirm current storage ownership for `synology-iscsi-ssd`, prod/dev DB PVCs, backup PVCs, and CNPG/PITR prerequisites. | Yes for storage readiness claims | Before data/storage epic closure | Read-only PVC/PV/StorageClass status and backup policy |
| `verdify-platform` | `network-infra` | Confirm public route ownership for `*.verdify.ai`, `*.k3s.verdify.ai`, app Traefik routes, and ESP32/device VLAN egress gates. | Yes for route/device-path changes | Before ingress or device-network changes | Named route/firewall status only |
| `verdify-platform` | `monitoring-stack` | Confirm which Grafana/Prometheus/alerting resources are shared-stack owned versus repo-owned app dashboards. | No for docs; yes for shared telemetry changes | Before observability epic closure | Dashboard/alert inventory for Verdify resources |
| `verdify-platform` | `cortex-ai-compute` | Confirm GPU/AI runtime availability only if Fable or planner work introduces an in-repo runtime dependency. | No current blocker | When a concrete runtime dependency appears | Runtime requirements review only |
| `verdify-platform` | Jason | Approve any firmware OTA, prod ArgoCD sync touching the live writer, device VLAN action, prod-destructive DB operation, credential rotation, or outward-facing DNS/edge/org change. | Yes for gated operations | Before action | Explicit approval; no standing broad access |
| `verdify-platform` | Secret-delivery owner | Verify Secret presence by name/key in `verdify-dev` and `verdify-prod` without exposing values. | Yes before live readiness claims | Before secret audit closure | Secret metadata/status only |
