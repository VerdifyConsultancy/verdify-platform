# Verdify Platform Coordination Requests

Last updated: 2026-06-16

Agent name: `verdify-platform`

Use this file for out-of-lane asks. Keep each request scoped to the minimum
action and create/update a GitHub issue when the request blocks delivery.

| Requesting lane | Target owner | Needed action | Blocking? | Deadline | Minimal access/action |
|---|---|---|---|---|---|
| L1 Architecture Audit (#343) | `storage-infra` | Confirm current storage ownership for prod DB PVCs, backup PVCs, NAS/Synology storage paths, and CNPG/PITR prerequisites. | Yes for storage readiness claims | Before closing G0/G2 storage sections | Read-only PVC/PV/StorageClass status and backup policy |
| L1 Architecture Audit (#343) | `network-infra` | Confirm public route ownership for `*.verdify.ai`, app Traefik routes, Cloudflare tunnel path, ESP32/device VLAN egress gates, and Frigate/Home Assistant route assumptions. | Yes for route/device-path changes | Before ingress, device-network, or L7 integration changes | Named route/firewall/status review only |
| L6 Observability (#348) | `monitoring-stack` | Confirm shared Grafana/Prometheus/alert ownership versus repo-owned app dashboard manifests and drift/data-hole alert responsibilities. | No for docs; yes for shared telemetry changes | Before L6 closure | Dashboard/alert inventory for Verdify resources |
| L2/L3/L7/L8/L10 | Jason | Approve any firmware OTA, prod ArgoCD sync touching the live writer, device VLAN action, prod-destructive DB operation, credential rotation, or outward-facing DNS/edge/org change. | Yes for gated operations | Before action | Explicit approval; no standing broad access |
| L8 Irrigation (#350) | Jason / horticulture decision | Decide fertilizer-tank routing: wall drip/fertigation loop versus orchids/manual care, including whether fish-based fertilizer belongs in automated loop. | Yes for L8 implementation | Before irrigation/fertilization control changes | Human horticulture decision and physical routing confirmation |
| L7 Lighting/Occupancy (#349) | Jason / integration owners | Choose/confirm the occupancy event path to firmware: MQTT, Home Assistant API, direct Frigate, or Kubernetes-local bridge. | Yes for L7 implementation | Before occupancy firmware/service changes | Contract review; no credential values |
| L9 Lab Notebook (#351) | Secret-delivery owner | Verify S3 lab publisher Secret presence by name/key only and document which repo/system owns content/public/state prefixes. | Yes before L9 publish-path closure | Before lab publishing audit closure | Secret metadata/status only |
| L1/L5 | Secret-delivery owner | Verify prod Secret presence by name/key in `verdify-prod` without exposing values. | Yes before live readiness claims | Before secret audit closure | Secret metadata/status only |

## Resolved / No Current Blocker

| Area | Resolution |
|---|---|
| Project Board | Exact `verdify-platform` org Project #5 exists. The 2026-06-16 replan added milestones G0-G3 and issue cards #343-#352 with field metadata. Revisit only if project permissions drift. |
