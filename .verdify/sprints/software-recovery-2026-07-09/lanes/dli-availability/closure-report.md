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
product views. Migration 195 adds the operator validity ledger and seeds the
known invalid interval without rewriting history. Legacy aggregate/reporting
surfaces keep their existing schemas but return typed null DLI fields.

Firmware accumulation now uses actual elapsed milliseconds for any future
validated sensor, while the current numeric product sensor publishes `NaN` and
six companion text entities publish reason/provenance/revision/validity. Native
tests and a 296,698-row baseline replay prove no relay, qualified-light-minute,
or photoperiod divergence.

## Coordination and ownership

The software-recovery controller granted DLI-only ownership for:

- `verdify_schemas/mcp_responses.py` and focused response tests;
- the daily-plan, Vault-daily, public-sample, and lifecycle export consumers;
- the generated Grafana ConfigMaps and firmware-twin mirrors;
- the deployed ingestor gather-script ConfigMap after the adversarial scan
  found its stale numeric correction block.

Every change preserves non-DLI response fields, frontmatter keys, CSV columns,
routes, helper behavior, and runtime control contracts. Prohibited dispatcher,
ESP32 push, firmware tunable/global, and `planner_graph` paths were not edited.

## Verification summary

- Migration 195: safe-to-wrap; fresh baseline apply, idempotent reruns,
  boundary/overlap/provenance cases, future valid-day case, and exact raw
  before/after count/sum preservation pass in disposable PG16/TimescaleDB.
- Schema: generated dump restores into a second blank disposable database.
- Python: lint passes; focused DLI/schema/MCP suite passes 35 with one inherited
  skip.
- Firmware: 269 native tests pass; all 296,698 invariant rows pass; replay from
  `0a9a19a840be6bae1beba604497d880b3b74b1ef` reports zero divergence; ESPHome
  compile passes at 33.3% RAM and 59.2% flash.
- Lighting/site: 27 static lighting contracts pass with three inherited
  generated-content warnings; TypeScript/Prettier and 69 Quartz tests pass; an
  in-directory Quartz build succeeds without the production content mount.
- Generated artifacts: Grafana regeneration is idempotent; JSON/YAML parses;
  deployed planner script/helper blocks have byte parity; numeric leakage scan
  passes.
- GitHub CI: the initial run found one format-only Ruff mismatch in the new
  focused test. Ruff 0.15.21 remediation changed no semantics, and exact-head
  PR checks at `0f68aac0c9af50159e11bd938411afccf2a221fe` completed with no
  failures or pending checks, including all firmware/schema/migration/safety
  gates and applicable build-without-publish jobs.
- Monolithic `make test`: 725 passed; remaining failures/errors require the
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
separate operator task. Until that happens and a new operator-validated
interval is recorded, the correct product answer is unavailable.
