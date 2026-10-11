# Moisture-exchange state and detail (#963)

The visible ESPHome entity `climate_moisture_exchange` publishes one short action:
`none`, `vent_dehum`, `heat_assist`, or `vent_humidify`. The existing HA entity name
is retained. Full estimator detail is published as compact versioned JSON on the
new native-API text entity `climate_moisture_exchange_details`, with
`disabled_by_default: true` to keep backend diagnostics hidden initially.

[ESPHome's text-sensor contract](https://esphome.io/components/text_sensor/#base-text-sensor-configuration)
defines that flag as a frontend default. It does not make the component internal.
The ingestor enumerates text entities independently of this flag and receives the
full detail. Both visible and detail states fit HA's limit even if the detail
entity is manually enabled. Consumers retain the existing structured database/API
contract.

The ingestor accepts both the old firmware's original JSON entity and the new
detail entity into the canonical `climate_moisture_exchange` system-state key.
Short actions from the original entity are stored separately as
`climate_moisture_exchange_action`; either callback order preserves full detail.
Compact detail has `_mx:1` and the closed alias map `MX_COMPACT_WIRE_KEYS` in
`verdify_schemas.telemetry`. Expansion happens before spooling or storing. All
canonical field names, migration 187's view, and the MCP estimator contract remain
unchanged. Legacy payloads retain missingness and unknown future fields; compact
wire additions require an explicit versioned contract. No migration is required.

The detail formatter uses a256-byte buffer, including its terminating NUL. If an
extreme value exceeds it, the controller publishes the short JSON diagnostic
`{"raw":"moisture_exchange_payload_unavailable"}` instead of partial JSON.
An unsupported version or incomplete/ambiguous compact shape becomes an explicit
missing-detail diagnostic, rather than retaining stale structured data as current.
The native formatter test exercises the actual ESPHome format/guard with uint32
maximum age/timer and extreme floating-point values.

Deliver the compatible ingestor before any firmware rollout. Its image adoption
requires the usual attended production promotion and singleton Recreate checks.
The firmware source change requires native tests, invariants, zero-divergence
replay, and an ESPHome compile. These checks do not flash the controller.

OTA is outside the S2 delivery scope. After a separately authorized OTA, verify
the controller's native firmware version, the visible HA state and absence of
new length errors, the detail entity's disabled registry state, and fresh
structured observations in the database. Preserve the last-good artifact and
the ingestor's old-firmware compatibility for rollback. Until those observations
exist, #963 remains open and HA recovery is unproven.
