# Resource accounting recovery handoff — 2026-07-10

Issue [#437](https://github.com/VerdifyConsultancy/verdify-platform/issues/437) is implemented at schema head `16c904b` and consumer head `a3aa154`. This is a code/data-contract handoff only: no production migration, service restart, dashboard rollout, or device action occurred.

## What changed

- `equipment` is the active product catalog. Live `grow_light_main` and `grow_light_grow` identities are canonical; `gl1`, `gl2`, and `grow_light` are explicit inactive aliases. The legacy `equipment_assets` table remains only for `maintenance_log` FK compatibility, with product reads moved to `v_equipment_assets_compat` or the canonical resource catalog.
- Resource coefficients now carry low/nominal/high values, source, revision, evidence reference, validity, and selected-model status. The July fan/fog/heat meter fits remain provisional bounded evidence; historical catalog points remain queryable rather than being silently overwritten.
- `materialize_water_meter_events()` checkpoints every raw cumulative-meter sample incrementally. It is idempotent and persists resets, phantom zeros, large deltas, gaps, relay candidates, attribution class, and quality. The ingestor runs it every minute.
- Accepted meter deltas partition into `meter_attributed`, `ambiguous_overlap`, or `manual_or_unattributed`. Observed wet-relay runs with no accepted meter delta are `command_only` with `NULL` gallons. Daily conservation must be exact before the term is scoring-eligible.
- Runtime-modeled electric energy has low/nominal/high kWh plus coefficient revisions. The two-channel Shelly integration remains separately labeled `partial_shelly_two_channels` with temporal coverage. No comparison claims either is a facility total.
- Daily summaries, MCP, API, Grafana, and the generated equipment page consume the new scope/quality contract. Stale or low-confidence terms are visible but excluded from scalar scoring.

## Current evidence

Before implementation, production raw totalizer telemetry was current while `water_meter_events` stopped at 2026-05-01. Recent modeled energy was approximately 13–19 kWh/day while partial Shelly integration was approximately 2–6 kWh/day; these are different scopes, not a divergence verdict.

A read-only production-shaped local replay copied 107,467 water samples, 97,033 equipment-state rows, and 21,080 energy samples. Catch-up created or updated 20,544 event rows in about 21.5 seconds, reported `fresh`, and an immediate rerun processed zero rows in about 0.1 seconds. Recent complete-day conservation error is exactly zero. Representative July 8 output was 147 accepted gallons = 9 single-run attributed + 136 ambiguous + 2 manual/unattributed; wall irrigation/fertigation was zero, matching the disabled/no-delivery baseline. The daily query completes in about 0.3 seconds. Energy reconciliation completes in about 0.03 seconds and keeps the runtime model bounded and the partial meter independently covered.

Both migrations classify safe-to-wrap, pass explicit outer rollback proofs, rerun idempotently, and pass fixed SQL fixtures. The generated schema restores cleanly. Ruff, focused contract tests, dashboard JSON/ConfigMap parity, compilation, and `git diff --check` pass. The laptop-wide `make test` result is 715 passed, 138 failed, 6 skipped, 10 errors; failures are the inherited retired laptop/live-service assumptions documented in lane evidence, not focused lane regressions.

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
