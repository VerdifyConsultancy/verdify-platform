# Resource accounting recovery handoff — 2026-07-10

Issue [#437](https://github.com/VerdifyConsultancy/verdify-platform/issues/437) is implemented through final correction heads `78fcc00` (schema) and `6167e3b` (consumers). This is a code/data-contract handoff only: no production migration, service restart, dashboard rollout, or device action occurred.

## What changed

- `equipment` is the active product catalog. Live `grow_light_main` and `grow_light_grow` identities are canonical; `gl1`, `gl2`, and `grow_light` are explicit inactive aliases. The legacy `equipment_assets` table remains only for `maintenance_log` FK compatibility, with product reads moved to `v_equipment_assets_compat` or the canonical resource catalog.
- Resource coefficients now carry low/nominal/high values, source, revision, evidence reference, validity, and selected-model status. The July fan/fog/heat meter fits remain provisional bounded evidence; historical catalog points remain queryable rather than being silently overwritten.
- Alias/canonical slugs are disjoint by trigger and defensive view precedence. Historical energy selects the coefficient revision valid on that local day; operator/catalog light and vent points carry uncertainty rather than exact-looking bounds. The equipment renderer includes both electric and gas coefficient provenance.
- `materialize_water_meter_events()` checkpoints every raw cumulative-meter sample incrementally. It is idempotent and persists resets, phantom zeros, large deltas, gaps, relay candidates, attribution class, and quality. The ingestor runs it every minute.
- Accepted meter deltas partition into `meter_attributed`, `ambiguous_overlap`, or `manual_or_unattributed`. Observed wet-relay runs with no accepted meter delta are `command_only` with `NULL` gallons. Daily conservation must be exact before the term is scoring-eligible.
- Complete raw coverage is insufficient by itself: daily water remains `ledger_incomplete` until the materializer watermark covers the final raw sample. Two sequential runs of one relay inside a coarse meter interval are ambiguous at event and run level. Wall-fertigation attribution requires wall-fert relay evidence, fertilizer-master overlap, and a current `fertigation_commissioning_eligible` state; otherwise it is unsupported/degraded.
- Runtime-modeled electric energy has low/nominal/high kWh plus coefficient revisions and accepts only complete transition-derived runtime. Populated legacy summary fields are never proof. The two-channel Shelly integration remains separately labeled `partial_shelly_two_channels` with temporal coverage. No comparison claims either is a facility total.
- Daily summaries, MCP, API, every site Grafana resource panel, Prometheus, and the generated equipment page consume the new scope/quality contract. The legacy snapshot writer no longer writes resource fields, and `v_cost_today` preserves unavailable values as `NULL`. Stale or low-confidence terms are visible but excluded from scalar scoring.
- The planner score no longer treats missing resources as free. It uses the established 80/20 climate/cost formula only when conserved water and the whole-runtime energy model are eligible; otherwise it emits a labeled climate-only normalized score with zero resource weight and `NULL` resource costs.

## Current evidence

Before implementation, production raw totalizer telemetry was current while `water_meter_events` stopped at 2026-05-01. Recent modeled energy was approximately 13–19 kWh/day while partial Shelly integration was approximately 2–6 kWh/day; these are different scopes, not a divergence verdict.

A read-only production-shaped local replay copied 107,467 water samples, 97,033 equipment-state rows, and 21,080 energy samples. After the boundary-conflict and true fertilizer-overlap fixes, a from-zero catch-up created or updated 20,544 event rows in 33.33 seconds; an immediate rerun processed zero rows in 0.11 seconds. Fertilizer-overlap proof runs only when wall fertigation is an actual candidate; steady-state work remains incremental. Recent complete-day conservation error is exactly zero. Representative July 8 output remains 147 accepted gallons = 9 single-run attributed + 136 ambiguous + 2 manual/unattributed; wall irrigation/fertigation was zero, matching the disabled/no-delivery baseline. The daily query completes in about 0.3 seconds. Energy reconciliation completes in about 0.03 seconds and keeps the runtime model bounded and the partial meter independently covered.

Both migrations classify safe-to-wrap, pass explicit outer rollback proofs, rerun idempotently, and pass fixed SQL fixtures. The generated schema restores cleanly. The expanded fixture proves incomplete-watermark, repeated-run and boundary-state ambiguity, positive-duration fertilizer-master overlap, contradictory-source degradation, historical uncertainty, transition-backed full/partial/modeled-only/stale/unavailable energy, and no-free-resource-score cases. Ruff, 253 focused tests collected (252 passed, one skipped), six targeted disposable-schema tests, dashboard JSON/ConfigMap parity, compilation, and `git diff --check` pass. The current-head laptop-wide `make test` result is 717 passed, 141 failed, 6 skipped, 10 errors; failures are the inherited retired laptop/live-service assumptions plus DB-backed checks that pass against the disposable restored schema, not focused lane regressions.

The required independent critic returned `REQUEST_CHANGES` at `722a90c`, then again at re-review head `b03c417` after finding residual overlap, transition-proof, conflict-health, boundary-seed, historical-precision, consumer, inventory, and ownership defects. All second-review findings are addressed in `78fcc00` and `6167e3b`; the item-by-item record and third-review gate are in the lane `critic-report.md`.

## Release order and gates

1. Merge only after independent data-integrity review and green GitHub checks.
2. Apply migrations `193` then `194` to production. Do not wrap a self-committing migration; both new files are classified non-self-transactional and safe-to-wrap.
3. Restart `verdify-ingestor`, `verdify-mcp`, and `verdify-api` so the minute materializer and typed consumers load. Roll out the generated Grafana ConfigMap/API image after migration.
4. Verify `v_water_ledger_health.ledger_status = 'fresh'`, checkpoint lag at or below five minutes, and a second materializer call processes zero already-checkpointed rows.
5. Verify current complete-day conservation, API `/api/v1/resources/daily`, MCP `outcome_kpi.resource_evidence`, and Grafana labels. Do not promote partial-day or discontinuous water, uncertain runtime coefficients, or partial Shelly energy as a precise scalar.

## Deliberate limitations

- No physical sensor/meter is added. Coefficient fits are not billing grade and remain scoring-ineligible while bounded/uncertain.
- The totalizer reports in coarse increments, so short wet-relay runs can be command-only and a meter increment can overlap several climate relays. The software preserves that ambiguity.
- Historical pre-migration ledger events lack source intervals. Their accepted volume remains conservative `manual_or_unattributed` with degraded evidence instead of a fabricated relay match.
- Center drip/fertigation policy is unchanged here. The observed recent wall irrigation/fertigation delivery is zero; this lane accounts for evidence and does not authorize or infer delivery.
