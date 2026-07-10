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
| Product database views | `v_dli_current.crop_dli_mol_m2_day` and `v_dli_daily.crop_dli_mol_m2_day` are `NULL` unless a full operator-validated interval applies | `availability`, `unavailable_reason`, `provenance`, `validity_revision`, `valid_from`, and `valid_to` | A future calibrated sensor can become numeric only through the validity ledger |
| Legacy DB reports | Weekly/monthly/period, harvest/economics, water-efficiency, KPI, estimated-DLI, and forecast DLI fields are typed `NULL` | Product callers are directed to `v_dli_current`/`v_dli_daily` | Climate, water, energy, cost, harvest, and lighting-runtime fields retain their existing contracts |
| Iris planning context | No scalar, correction factor, forecast proxy, or seven-day numeric DLI is emitted | Context prints the unavailable reason/provenance/revision/interval and an explicit no-inference directive | DLI-independent qualified-light-minute planning remains available |
| Planner prompt | DLI cannot be inferred, scored, or used for a recommendation | Standing directive identifies the broken sensor and invalid sources | Climate safety and DLI-independent lighting levers are unchanged |
| MCP `outcome_kpi` | `dli.value_mol_m2_day` is `null` and DLI coverage is unavailable | Typed `DliEvidence` response exposes the full validity contract | Every non-DLI KPI/resource response field remains compatible |
| API `/api/v1/dli` and greenhouse route | `value_mol_m2_day` is `null` | Typed `DliEvidence` response exposes the full validity contract even if no climate row exists | Existing API routes and schemas are additive/unchanged outside DLI |
| Grafana source and provisioned ConfigMaps | No DLI panel queries raw proxy scalars | Panels show unavailable reason, provenance/revision, and validity interval from product views | Outdoor solar, relay/runtime, and qualified-minute panels remain independent |
| Daily plan and Vault daily note | Legacy frontmatter key is retained with null value | Rendered Interior Light Evidence section shows reason/provenance/interval | Non-DLI frontmatter keys and page routes remain stable |
| Public sample and lifecycle exports | Existing DLI CSV column is intentionally blank | README text records reason, provenance, revision, and interval | CSV shape and all non-DLI columns remain stable |
| Deployed gather-script ConfigMap | No raw/max/corrected numeric DLI reaches the planner | ConfigMap is byte-parity generated from the corrected source script | Database transport helper and all non-DLI context blocks are unchanged |

## Validity transition

Migration 195 seeds the half-open `vallery` interval beginning
`2024-01-01T00:00:00Z` as unavailable with reason
`interior_light_sensor_broken`, provenance
`legacy_invalid_exterior_proxy_plus_fixture_estimate`, and revision
`dli-validity-v1`. A future available interval must be explicitly
operator-validated and must cover an entire Denver-local day before a daily
numeric value is exposed.

Replacing or calibrating the physical sensor is outside this software lane.
Migration application, service restarts, dashboard/site publication, and any
firmware OTA remain release-control/human-gated work.
