## Problem

The interior light sensor is broken and currently reads 0 lx, so the greenhouse has no measured interior crop DLI. Software nevertheless publishes about 79 mol/m²/day and feeds it into planner, scoring, database views, dashboards, and site surfaces.

Verified defects compound the false claim:

- the one-second firmware loop credits five seconds per accumulation call;
- firmware takes a maximum of scaled indoor LDR and an exterior Tempest proxy, then adds estimated fixture light;
- planner context multiplies the result by 3.5 again and adds grow-light estimate again;
- MCP metric status supports only `available|pending`, not explicit unavailable;
- outcome/composite views treat the proxy as crop evidence.

Jason owns replacement of the sensor. Physical work is outside this software recovery.

## Desired outcome

Interior/crop DLI is explicitly unavailable, with reason and provenance, across firmware, database, planner, MCP, API, dashboards, and generated sites until a replacement sensor passes an operator-validity/calibration contract. DLI-independent lighting control remains operational.

## Acceptance intent

- [ ] Schema supports nullable value plus `unavailable`, reason, provenance, validity revision, and valid interval.
- [ ] Raw invalid/proxy values may remain forensic but are marked invalid and excluded from planner recommendations, outcome grading, and composite scores.
- [ ] Firmware emits unavailable while sensor validity is false and uses real elapsed time if/when accumulation is re-enabled.
- [ ] Planner context, MCP outcome KPI, API, daily summaries, DB views, dashboards, and sites never publish a numeric crop DLI while invalid.
- [ ] Qualified-light-minute and photoperiod lighting logic remains green and independent of DLI.
- [ ] A consumer-matrix test covers every active surface and historical invalid interval.
- [ ] Schema/migration lands before consumers; required ingestor/MCP service restarts are documented.

## Non-goals

- Replacing or calibrating the physical sensor.
- Estimating measured interior DLI from outdoor irradiance.
- Disabling grow lights or independent lighting policy.
- Rewriting historical raw telemetry as if a valid measurement had existed.

## Dependencies and related issues

- Outcome issues #365 and #371 must exclude unavailable DLI until this contract is valid.
- Planner recovery #427 consumes availability-bearing context.
- The firmware publication change is bundled into the one approved recovery OTA.

## Initial risk

High data-integrity and crop-decision risk. Current product surfaces present a confident physical quantity that is not measured.

## Affected surfaces

Firmware DLI accumulator/status, telemetry and MCP response schemas, serialized migration/schema dump, ingestor daily paths, planner context, MCP outcome KPI, API, dashboards, and site generators.

### Triage investigation

- Existing issue search: #365/#371 use DLI as an outcome but no issue owns invalid-sensor availability end to end.
- Evidence inspected: live Home Assistant values, firmware accumulation code, planner context script, migration/view SQL, MCP schemas/server, daily consumers.
- Reproduction: compare live 0-lx interior sensor with published numeric DLI and trace transformations.
- Likely cause: a provisional exterior proxy became mislabeled as measured interior evidence and accumulated multiple arithmetic transformations.
- Potential fix options: schema-first availability contract, validity-gated firmware signal, consumer guards, historical invalid provenance.
- Adversarial audit: preserve independent lighting actuation; distinguish forensic proxy from product truth.
- Confidence: high.
- Remaining unknowns: replacement sensor calibration thresholds are a future operator decision, not a software blocker.
