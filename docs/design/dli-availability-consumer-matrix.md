# DLI availability consumer matrix

Issue `#435` establishes one product truth: interior crop DLI is unavailable
while the physical interior light sensor is broken. Legacy numeric values remain
queryable only as explicitly forensic proxy history. Outdoor irradiance,
fixture runtime, and a numeric zero are not substitutes for measured crop DLI.

| Surface | Product value while invalid | Availability evidence | Independent behavior retained |
| --- | --- | --- | --- |
| ESP32 firmware | Numeric DLI sensor publishes `NaN`; the internal legacy accumulator is forensic only | Six text entities publish availability, reason, provenance, revision, valid-from, and valid-to | Qualified-light minutes, photoperiod, relay decisions, and fixture out-of-service logic are unchanged |
| Firmware twin | Byte-identical `greenhouse_logic.h` and `greenhouse_types.h` mirror the device source | Same `DliEvidence` helper and unavailable constants as device source | Replay/twin relay decisions remain identical |
| Raw database history | `climate.dli_today` and `daily_summary.dli_final` retain their original numeric proxy values for forensics | Column/view comments label the proxy invalid; `v_dli_forensic_history` carries reason/provenance/interval | No raw row or value is rewritten or deleted |
| Product database views | `v_dli_current.crop_dli_mol_m2_day` and `v_dli_daily.crop_dli_mol_m2_day` are always `NULL` under migration 195 | `availability`, explicit invalid-source/day-completeness reason, provenance, revision, and interval | Migration 195 rejects ordinary DML that attempts `available`; activation requires a separately validated migration/contract change |
| Legacy `v_estimated_dli` | `est_natural_dli` is always `NULL`; the outdoor-lux/glazing calculation is retired | Explicit unavailable reason/provenance/revision/interval are appended; outdoor reading count remains diagnostic | No outdoor irradiance or glazing model is relabeled as measured interior crop DLI |
| Live lighting status/traceability | `dli_today` and `dli_below_target` are `NULL` in circuit/status/minutes/traceability views | The six DLI availability/provenance columns are appended to every named view | `expected_on`, qualified minutes, photoperiod, lux hysteresis, occupancy task light, firmware state, and cfg readbacks remain independent |
| Sensor registry/staleness/alerts | Every `sensor_registry` row mapped to `climate.dli_today` is inactive and the matching greenhouse mapping is not required | Mapping-based migration notes record the broken-sensor disposition | `v_sensor_staleness`, required coverage, and alert polling cannot report the invalid accumulator as an active healthy/stale physical sensor; raw history remains intact |
| Legacy DB reports | Weekly/monthly/period, harvest/economics, water-efficiency, KPI, estimated-DLI, and forecast DLI fields are typed `NULL` | Product callers are directed to `v_dli_current`/`v_dli_daily` | Climate, water, energy, cost, harvest, and lighting-runtime fields retain their existing contracts |
| Iris planning context | No scalar, correction factor, forecast proxy, or seven-day numeric DLI is emitted | Context prints the unavailable reason/provenance/revision/interval and an explicit no-inference directive | DLI-independent qualified-light-minute planning remains available |
| Planner prompt | DLI cannot be inferred, scored, or used for a recommendation | Standing directive identifies the broken sensor and invalid sources | Climate safety and DLI-independent lighting levers are unchanged |
| MCP `outcome_kpi` | `dli.value_mol_m2_day` is `null` and DLI coverage is unavailable | Typed `DliEvidence` response exposes the full validity contract | Every non-DLI KPI/resource response field remains compatible |
| API `/api/v1/dli` and greenhouse route | `value_mol_m2_day` is `null` | Typed `DliEvidence` response exposes the full validity contract even if no climate row exists | Existing API routes and schemas are additive/unchanged outside DLI |
| Grafana source and provisioned ConfigMaps | No DLI panel queries raw proxy scalars | Panels show unavailable reason, provenance/revision, and validity interval from product views | Outdoor solar, relay/runtime, and qualified-minute panels remain independent |
| Daily plan and Vault daily note | Legacy frontmatter key is retained with null value | Rendered Interior Light Evidence section shows reason/provenance/interval | Non-DLI frontmatter keys and page routes remain stable |
| Public sample and lifecycle exports | Existing DLI CSV column is intentionally blank | README text records reason, provenance, revision, and interval | CSV shape and all non-DLI columns remain stable |
| Deployed gather-script ConfigMap | No raw/max/corrected numeric DLI reaches the planner | ConfigMap is byte-parity generated from the corrected source script | Database transport helper and all non-DLI context blocks are unchanged |
| Planner learned knowledge | The live `sensor_dli × 3.5 + grow_light_hours × 0.8` lesson and future equivalent proxy/correction text cannot be active or retrieved | Migration retires matching rows without deleting/rewriting them; DB constraint, gather, MCP search, embedding search, and generator filters are defense in depth | Direct DB rows remain available for forensic inspection only |

## Validity transition

Migration 195 seeds the half-open `vallery` interval beginning
`2024-01-01T00:00:00Z` as unavailable with reason
`interior_light_sensor_broken`, provenance
`legacy_invalid_exterior_proxy_plus_fixture_estimate`, and revision
`dli-validity-v1`. Migration 195 adds a fail-closed schema invariant that
permits only unavailable, non-operator-validated intervals. This is not a
GRANT/REVOKE security boundary because the production application role owns
the table. A future calibrated source can become available only through a
separately validated migration/contract change. Migration 195 deliberately does
not define future source revision, calibration, cadence/gap completeness, or
day-closure semantics; those belong to the later sensor-activation migration
and its own fixtures.

Replacing or calibrating the physical sensor is outside this software lane.
Migration application, service restarts, dashboard/site publication, and any
firmware OTA remain controlled by technical preflight, an explicit live flag,
telemetry verification, and rollback readiness.
