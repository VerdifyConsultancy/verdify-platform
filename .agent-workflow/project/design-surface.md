# Verdify platform design surfaces

**Status:** approved

## Deterministic control and device writer

Firmware owns high-frequency safety and relay decisions. One ingestor owns device writes. A transport generation identifies true reconnect; cfg readbacks update confirmed state without forcing reconnect. Actual ESPHome wire IDs, normalized values, and post-success confirmation define no-op suppression and retry behavior.

## Planner and MCP

Hermes health includes Verdify MCP tool liveness, not TCP process health alone. Planner values are accepted only inside the intersection of all applicable bounds. One expiring active plan is written atomically; one-shot tools cannot masquerade as a full plan; failure becomes a visible neutral fallback. After acceptance checks, the repaired planner is active.

## Irrigation and fertigation

Climate VPD demand can open only the center mister. South and west mist require explicit intentional irrigation. Center drip and non-wall fertilizer stay represented for future topology but disabled by policy and defaults.

Automatic wall feed is a solar-relative weekly state machine: validate commissioning and exactly-once eligibility, prewet with clean water, inject the configured dose, stop injection, immediately flush with clean water to the commissioned distal endpoint, and record requested/delivered volume plus terminal disposition. Durations are derived from liters and calibrated flow. Any missing or invalid commissioning field fails closed.

## Evidence and DLI

Telemetry distinguishes intent, command, readback, and physical outcome. Interior crop DLI is an availability-bearing measurement; while the sensor is broken, it is unavailable everywhere and cannot be replaced by outdoor irradiance or a broken LDR proxy. Lighting controls that rely on photoperiod or qualified minutes remain separate.

## Night dry-out

Dry-out uses the existing firmware night solar phase. It may actuate only when all temperature, humidity, weather, wind, dwell, and equipment guards pass. Diagnostics expose solar phase, eligibility, admission, block, and stop reason.

## Delivery

GitHub, CI, GHCR, ArgoCD, and the firmware OTA workflow preserve revision-to-runtime traceability. Jason's July 9 approval covers this recovery's production sync and OTA, while automated safety gates remain fail-closed.
