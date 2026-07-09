# Recovery north-star architecture

Status: approved. Baseline: `0a9a19a840be6bae1beba604497d880b3b74b1ef`.

The live system has one deterministic ESP32, one remote writer, one TimescaleDB evidence authority, a bounded Hermes/MCP planner path, read-only product consumers, and one immutable delivery path. The recovery does not add a component; it makes boundaries truthful.

The writer observes a monotonic transport generation and actual canonical cfg readbacks. Ordinary cfg drift updates confirmed state but cannot impersonate reconnect. A bounded queue yields to other periodic work, and requested, sent, failed, cancelled, confirmed, and superseded states are recorded only when they happen.

The planner is healthy only when the Verdify MCP tool is usable. Terminal polling records the actual action. Full plans use the intersection of every applicable bound and one effective expiring plan; failure is an explicit neutral state. The repaired path becomes active after its acceptance tests, as approved.

Firmware has one relay resolver. Climate VPD demand can select only center. South and west require explicit enabled irrigation intent. Fertilizer can select only wall. Weekly automatic wall feed is admitted only with current commissioning and executes liters-derived prewet, injection, fertilizer-off, and immediate clean flush with exact-once evidence. Dormant plumbing remains represented but disabled.

Interior DLI is an availability-bearing metric. While the sensor is invalid, firmware, database, planner, API, and sites say unavailable; raw history remains forensic. Dry-out remains firmware solar-night behavior and gains a realized-response contract rather than another speculative control rewrite.

Delivery is schema-first and migration-serialized, followed by service promotion, stale-plan retirement, and one combined firmware OTA. All normal CI, device, alert, weekly, bake, replay, invariant, heap, runtime, and rollback gates remain fail-closed.
