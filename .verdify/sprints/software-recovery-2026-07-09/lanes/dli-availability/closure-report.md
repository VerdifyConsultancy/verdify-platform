# DLI availability lane closeout

## Outcome

The source implementation for issue `#435` is ready for independent review.
Interior crop DLI now fails closed to unavailable across firmware, database,
planner, MCP, API, Grafana, and generated/public site paths while the physical
interior light sensor is broken. No outdoor proxy, correction factor, fixture
runtime estimate, cached legacy scalar, or numeric zero is presented as
measured crop DLI.

Raw `climate.dli_today` and `daily_summary.dli_final` history is preserved for
forensics, explicitly marked invalid, and isolated behind availability-bearing
product views. Migration 195 adds the validity ledger and seeds the known
invalid interval without rewriting history. It is fail closed at the schema
level: ordinary DML cannot mark DLI available, and a future activation requires
a separately reviewed migration/contract change. This is an application/schema
invariant, not a GRANT/REVOKE security boundary, because the production
application role owns the table.

Firmware accumulation now uses wrap-safe raw elapsed milliseconds for any
future validated sensor while DLI-independent control time retains both its
legacy zero-on-`millis()`-wrap behavior and 5-second cap.
The current numeric product sensor publishes `NaN` and
six companion text entities publish reason/provenance/revision/validity. Native
tests and a 296,698-row baseline replay prove no relay, qualified-light-minute,
or photoperiod divergence.

## Critic remediation

The changes-required review identified seven real gaps, all closed at source:

1. The live `sensor_dli × 3.5 + grow_light_hours × 0.8` lesson is retired but
   preserved, equivalent future text cannot be active, and gather/MCP/embedding/
   public-generator paths all filter it.
2. Circuit, minutes, one-row status, and traceability views expose nullable DLI
   plus provenance without a zero sentinel; expected lighting behavior remains
   qualified-minute/photoperiod/lux driven.
3. Weekly/monthly electricity rollups retain exact actual-first
   `COALESCE(kwh_total, kwh_estimated, 0)` semantics.
4. The validity ledger is fail closed and documentation does not overclaim
   role-based security.
5. Firmware separates wrap-safe raw elapsed time from capped control time,
   preserves legacy zero-on-wrap control behavior, and tests long gaps, jitter
   partitions, and `uint32_t` wrap.
6. Current and daily product evidence is categorically unavailable. Migration
   195 makes no unproved promise about future cadence/source completeness.
7. Current/daily products and Pydantic reject missing, NaN, infinity, negative,
   and out-of-range values with explicit unavailable reasons.

Renewed review found four additional provenance gaps plus one rollover
neutrality edge; these are also closed:

1. Legacy `v_estimated_dli.est_natural_dli` is typed NULL with explicit
   reason/provenance. Its non-DLI `readings` count retains the exact prior
   `lead(ts)`/`dt_sec` and `0 < dt_sec < 900` semantics.
2. The fixture again proves exact raw climate/daily pre/post row counts and
   sums, half-open adjacent interval resolution, overlap rejection, and
   idempotent reruns.
3. Future daily completeness is explicitly deferred to a later reviewed
   sensor-activation migration instead of inferred from endpoints.
4. All registry rows mapped to `climate.dli_today` are inactive and the
   greenhouse mapping is not required, removing the broken sensor from
   staleness/required-coverage alert inputs without deleting history.
5. At `uint32_t` rollover, raw DLI elapsed time remains wrap-safe while
   DLI-independent control elapsed time remains the legacy zero.

## Coordination and ownership

The software-recovery controller granted DLI-only ownership for:

- `verdify_schemas/mcp_responses.py` and focused response tests;
- the daily-plan, Vault-daily, public-sample, and lifecycle export consumers;
- the generated Grafana ConfigMaps and firmware-twin mirrors;
- the deployed ingestor gather-script ConfigMap after the adversarial scan
  found its stale numeric correction block; and
- `scripts/generate-lessons-page.py` plus focused tests, to suppress invalid
  proxy/correction guidance from both public and noindex generated artifacts.

Every change preserves non-DLI response fields, frontmatter keys, CSV columns,
routes, helper behavior, and runtime control contracts. Prohibited dispatcher,
ESP32 push, firmware tunable/global, and `planner_graph` paths were not edited.

## Verification summary

- Migration 195: safe-to-wrap; fresh generated-schema restore, true no-op
  reruns, fail-closed insert/update, exact/future lesson retirement and
  preservation, exact raw climate/daily pre/post row+sum equality, half-open
  boundaries, overlap rejection, source-validity cases, `v_estimated_dli`
  count parity, registry/staleness correction, whole-schema proxy sweep,
  live-lighting leakage checks, and actual-first kWh parity pass in disposable
  PG16/TimescaleDB.
- Schema: generated dump restores into a second blank disposable database.
- Python: lint passes; focused DLI/schema/MCP suite passes 46 with one inherited
  skip.
- Firmware: 272 native tests pass; all 296,698 invariant rows pass; replay from
  `0a9a19a840be6bae1beba604497d880b3b74b1ef` reports zero divergence; ESPHome
  compile is delegated to exact-head CI because the laptop lacks the prescribed
  ESPHome secrets path (the earlier implementation head compiled at 33.3% RAM
  and 59.2% flash).
- Lighting/site: 27 static lighting contracts pass with three inherited
  generated-content warnings; TypeScript/Prettier and 69 Quartz tests pass; an
  in-directory Quartz build succeeds without the production content mount.
- Generated artifacts: Grafana regeneration is idempotent; JSON/YAML parses;
  deployed planner script/helper blocks have byte parity; numeric leakage scan
  passes.
- GitHub CI: renewed implementation head
  `03946b7da51f59c0a820d9cd64ac64249d669f33` is fully green across CI,
  manifest, promotion guard, and build-without-publish workflows: 26 success,
  8 intentional skips, 0 failure/pending.
- Monolithic `make test`: 732 passed and 6 skipped; remaining 141 failures and
  10 setup errors require the
  retired laptop live stack or unrelated historical paths. The one lane-caused
  static comment regression it exposed was fixed and retested.

## Release boundary

This lane made no production mutation. It did not apply migration 195, restart
services, publish dashboards/sites, sync ArgoCD, write a device, or perform an
OTA. After independent approval and merge, release-control must apply migration
195 before restarting `verdify-mcp`, `verdify-ingestor`, and `verdify-api`, then
publish the Grafana/site consumers and perform live unavailable-DLI acceptance.
The later firmware-control/release lanes own the combined reviewed image and
the one human-gated OTA.

Replacing and validating the broken physical interior light sensor remains a
separate operator task. Even after replacement, migration 195 cannot be
activated by ordinary DML: a separately reviewed activation migration and
contract validation are required. Until then, the correct product answer is
unavailable.
