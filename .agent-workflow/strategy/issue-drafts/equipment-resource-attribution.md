## Problem

Verdify has two competing equipment catalogs and no durable provenance contract for modeled resource use:

- Migration 085 declares `equipment` canonical, but `v_equipment_runtime_daily` consumers, `fn_runtime_power_30m`, runtime electric cost, operational-health checks, and public panels still join `equipment_assets`.
- The two tables can drift silently. Current rows happen to repeat several values, but neither expresses whether a number is nameplate, operator-entered, meter-fit, measured, inferred, or uncertain.
- Active lighting telemetry uses `grow_light_main` and `grow_light_grow`, which exist only in the legacy catalog; canonical rows use `gl1`, `gl2`, and `grow_light`. Switching consumers without an explicit alias/migration would silently drop lighting energy.
- The July 9 multivariate meter audit estimates roughly 102–124 W per exhaust fan, 315–620 W for the fog circuit depending on overlap/model, and about 1,436 W for electric heat. Those are useful bounded observations, not billing-grade constants, and conflict with current 52 W fan / 1,644 W fog point values.
- The Shelly covers only part of the building. Recent `v_energy_estimate_reconciliation` rows are consistently `meter_runtime_delta` or `meter_runtime_divergence`, so measured and modeled scopes must not be presented as interchangeable totals.
- `v_water_budget` attributes climate wetting and known irrigation runs, but recent days still leave substantial `unaccounted_gal`; manual use, meter quality, and missing intent/run correlation are not distinguished.
- The incremental water ledger is stale: `water_meter_events` stopped on 2026-05-01 although raw totalizer telemetry remains current. Daily processing silently falls back to raw max/min, and existing health tests do not detect the stale materializer.
- Live `v_daily_kpi` is still on the pre-migration-152 scope-collapsing definition and can prefer partial Shelly kWh over the whole-control runtime model without source or coverage metadata.

The July 9 review asks for better energy/water attribution using existing telemetry only. New meters or sensors are explicitly outside the current recovery.

## Desired outcome

One canonical, provenance-bearing equipment/resource contract drives runtime energy and water attribution. Product surfaces expose modeled, measured, and unaccounted quantities with scope and quality flags instead of a false single number.

## Acceptance intent

- [ ] `equipment` is the only primary catalog for active product/runtime consumers; `equipment_assets` is migrated, compatibility-viewed, or explicitly retired without breaking maintenance history.
- [ ] Canonical equipment includes every active telemetry slug, especially both lighting circuits; aliases and maintenance compatibility are explicit and tested before any legacy consumer is switched.
- [ ] Power/flow coefficients carry source (`nameplate`, `operator`, `meter_fit`, `measured`, or `unknown`), valid interval/revision, uncertainty or bounded range, and evidence reference.
- [ ] Current conflicting fan/fog/heat evidence is preserved as bounded/provisional; software does not silently replace a nameplate with one fitted point estimate.
- [ ] Runtime power and daily cost surfaces use the canonical coefficient revision and report modeled scope separately from partial Shelly-measured scope.
- [ ] Water attribution correlates quality-filtered meter deltas with climate wetting, wall irrigation/fertigation intent and relay episodes, plus an explicit `manual_or_unattributed` remainder; it never invents delivery for relay runtime alone.
- [ ] An incremental idempotent water-event materializer catches up after interruption and exposes freshness failure when it lags current raw totalizer telemetry; daily water never silently falls back to raw max/min.
- [ ] Each run is classified as meter-attributed, ambiguous overlap, command-only, or manual/unattributed; relay runtime alone is never presented as delivered gallons.
- [ ] For every complete fixture day, attributed plus ambiguous plus unattributed water conserves the quality-filtered meter total within tolerance.
- [ ] Energy/water outputs include quality, coverage, and uncertainty fields; planner/outcome scoring excludes unavailable or low-confidence resource terms rather than treating them as precise.
- [ ] Energy surfaces separately expose runtime-modeled kWh, partial Shelly-measured kWh, coefficient revision/range, meter coverage, and quality; KPI consumers never collapse them into an unlabeled scalar.
- [ ] Fixed-fixture SQL tests prove catalog convergence, coefficient provenance, energy-scope separation, and conservation (`attributed + unattributed = quality-filtered total` within tolerance).
- [ ] Existing dashboards/API/site labels distinguish measured, runtime-modeled, and unattributed quantities.
- [ ] Migration rollback safety, schema drift guards, MCP/ingestor restart documentation, and live read-only reconciliation checks pass.

## Non-goals

- Installing whole-building, circuit, manifold, gas, or flow meters.
- Claiming billing-grade total facility energy or per-zone delivered water.
- Optimizing actuation policy from uncertain coefficients in this issue.
- Reconstructing undocumented manual watering as an automatic event.

## Dependencies and related issues

- #371 is the broader follow-up outcome consumer; this recovery updates only consumers necessary to preserve resource availability/scope truth.
- #389 supplies trustworthy relay runtime and rising-edge counts.
- #434 supplies explicit irrigation/fertigation intent and exact-once run evidence.
- #348 is the observability umbrella.
- This issue can land schema-first in the recovery data lane; resource scoring remains availability/confidence-gated.

## Initial risk

High data-integrity risk, low direct actuation risk. The dangerous failure is confident optimization or reporting from incomparable scopes and drifting catalogs.

## Affected surfaces

`db/migrations`, `db/schema.sql`, daily materialization, runtime-power functions/views, water-budget/accountability views, MCP outcome context, API/site/Grafana labels, topology import/renderers, and contract tests.

### Triage investigation

- Existing issue search: #371 requests energy/water outcomes and #348 is an umbrella, but neither owns catalog convergence, coefficient provenance, scope separation, and conservation end to end.
- Evidence inspected: live `equipment`, `equipment_assets`, `v_energy_estimate_reconciliation`, `v_water_budget`; migrations 020/085/131/132/133/134; July 9 30-day equipment/resource audit.
- Reproduction: read-only comparison of live catalogs and recent reconciliation quality flags.
- Likely cause: the topology catalog was introduced after legacy runtime-cost views and consumers were never migrated; provisional coefficients became unlabeled point truth.
- Potential fix: additive provenance/revision schema, canonical compatibility layer, serialized consumer migration, availability/confidence-bearing resource views.
- Adversarial audit: preserve measurement/model scope; do not tune control or fabricate manual events; make uncertainty machine-readable.
- Confidence: high on the contract defect, medium on current equipment coefficients pending isolated response evidence.
- Remaining unknowns: exact coefficient revisions can be selected from existing evidence during implementation; future physical calibration is follow-up and not a software blocker.
