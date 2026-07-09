# Runtime map — software recovery 2026-07-09

Captured from the `vallery` Kubernetes context and production database on 2026-07-09 around 21:25 UTC.

## Environment and identity

- Sole environment: `verdify-prod`; ArgoCD app `verdify-prod-dark`.
- Accepted source revision: `0a9a19a840be6bae1beba604497d880b3b74b1ef`.
- Argo: `Healthy / OutOfSync`, revision `0a9a19a`; drifted resources include Grafana dashboard ConfigMap, namespace metadata, ingestor PVC, HA gap-backfill CronJob, and migrate Job.
- Core API, ingestor, planner, MCP, Hermes, DB, Grafana, setpoint server, lab, MQTT, and ingress pods are running. Public API/lab/graphs return HTTP 200.
- Ingestor image: digest prefix `175e5ec`; firmware: `2026.7.3.1931.ab18fe8`.

## Control and planner health

- ESPHome transport is stable after startup, but ingestor logs show `direct-pushed 69/69` about every 5–6 minutes through the fresh snapshot.
- One critical alert is open: `7676 planner_required_plan_missed`; 90 warnings are open.
- Latest `plan_journal` entry is 2026-06-25. Hermes has a verified tool-dead failure mode despite green TCP/pod health.
- Effective device/default `band_track_fraction` is 0, while `v_active_plan` still returns stale operator intent 0.25.

## Crop-care and evidence health

- Climate currently rotates wet-assist into center, south, and west; current user contract is center-only.
- Center irrigation enable is live/on despite an unconnected center drip. Wall/center daily schedules are 10:30 and are dropped after the 06:00–09:00 feed window.
- Current wall feed queues fertilizer paths for wall plus south/west. No scheduled wall/center fertilizer runtime occurred in the audited 30-day window because admission is accidentally closed.
- Interior light reads 0 lx, yet numeric DLI near 79 mol/m2/day is published and consumed.
- Firmware dry-out uses solar phase already; DB solar function remains the pre-migration-186 implementation, and realized episode effectiveness is not durable.

## Other runtime drift

The out-of-band `verdify-vision` CronJob has not succeeded since July 4 and is currently `ImagePullBackOff` on a private GHCR digest with a 401. It is tracked in #436 and deferred from this control recovery.

## Protected release state

Jason approved this recovery's implementation, prod delivery, and OTA. Deterministic CI, migration, single-writer, alert, firmware replay/invariant/check, weekly OTA, bake, and rollback gates remain mandatory. Credential rotation is separately gated.
