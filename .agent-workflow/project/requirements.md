# Verdify platform requirements

**Status:** approved

The canonical requirements and traceability are in `project-definition.yaml`.

## Recovery outcomes

- Preserve deterministic local safety and exactly one authorized ESP32 writer under planner, network, service, and batch-cancellation failures.
- Distinguish true transport reconnect from ordinary cfg readback changes; compare normalized confirmed values by actual wire ID; write only changed values; confirm only successful writes; prevent long batches from starving alerts or planner heartbeats.
- Treat center mist as the sole climate/VPD wet actuator. Keep center drip and all non-wall fertilizer disabled. South and west mist require explicit intentional irrigation.
- Run automatic wall fertigation at most once per solar-week interval only after commissioning is complete. Convert prewet, feed, and immediate postflush liters to bounded durations from calibrated aggregate flow. Never use a fixed 90-minute post-feed hold.
- Represent interior crop DLI as unavailable across firmware, database, planner context, API, and human consumers until sensor validity is explicitly restored. Preserve independent photoperiod and qualified-minute lighting behavior.
- Gate night dry-out by firmware night solar phase plus existing temperature, humidity, wind, weather, dwell, and equipment safeguards, with admission and stop evidence.
- Restore MCP tool liveness, one expiring active plan, intersected bounds, classified neutral fallback, and immediate active operation after acceptance.
- Retire the live June band experiment by setting `band_track_fraction=0` through the audited plan path.

## Acceptance summary

Runtime proof must show zero stable-connection full reconcile batches, one authorized writer, correct actual readbacks, and no task-loop starvation. Firmware tests and live evidence must show center-only climate mist, no automatic south/west water, no non-wall fertilizer, solar-phase-only dry-out, and explicit unavailable DLI. A commissioned wall-feed run must prove weekly exact-once admission, liters-to-time conversion, safe sequencing, and immediate complete flush; incomplete commissioning must not actuate. Planner triggers must terminate in a bounded full plan or explicitly classified neutral fallback with healthy MCP tool availability.

All applicable lint, tests, migration safety, firmware unit/invariant/replay/check, CI, promotion, ArgoCD, alert, weekly OTA, bake, and runtime checks remain mandatory.
