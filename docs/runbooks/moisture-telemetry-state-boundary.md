# Moisture-exchange state and detail (#963)

The visible ESPHome entity `climate_moisture_exchange` publishes one short action:
`none`, `vent_dehum`, `heat_assist`, or `vent_humidify`. The existing HA entity name
is retained. Full estimator JSON is published on the new native-API text entity
`climate_moisture_exchange_details`, with `disabled_by_default: true`.

[ESPHome's text-sensor contract](https://esphome.io/components/text_sensor/#base-text-sensor-configuration)
defines that flag as a frontend default. It does not make the component internal.
The ingestor enumerates text entities independently of this flag and receives the
full payload. Keep the detail entity disabled in HA; manually enabling it would
again expose a state longer than HA's limit. Consumers needing detail use the
existing structured database/API contract, rather than enabling that HA entity.

The ingestor accepts both the old firmware's original JSON entity and the new
detail entity into the canonical `climate_moisture_exchange` system-state key.
Short actions from the original entity are stored separately as
`climate_moisture_exchange_action`; either callback order preserves full detail.
JSON field names, missingness, unknown future fields, migration 187's view, and
the MCP estimator contract remain unchanged. No migration is required.

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
