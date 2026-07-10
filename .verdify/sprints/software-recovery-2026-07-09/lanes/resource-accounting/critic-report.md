# Resource-accounting independent critic record

Independent data-integrity review returned `REQUEST_CHANGES` at `722a90c`. The implementation was not merged. Schema correction `4b27f47` and consumer correction `23340fe` addressed that first review.

1. Incomplete ledger scoring: `v_water_meter_daily` now requires the materializer watermark to cover the final raw sample and emits `ledger_incomplete`; the disposable fixture reproduces and rejects the prior false-eligible case.
2. Free resource score/scalars: daily cost writes, `v_daily_kpi`, `v_planner_performance`, `fn_planner_scorecard`, API/MCP schemas, and Grafana now gate resource scalars. Missing/uncertain resources receive zero score weight, not free efficiency points; the climate-only score is explicitly labeled.
3. Event/run mismatch: meter intervals use interval overlap, and materialization counts relay episodes rather than distinct relay names. Two same-relay runs in one coarse interval are ambiguous at both layers.
4. Fertilizer proof: wall-fertigation scope requires wall-fert relay evidence, fertilizer-master overlap, and current commissioning eligibility. Missing proof becomes unsupported/degraded.
5. Energy acceptance: current partial-meter health exposes fresh/stale/unavailable; fixtures cover full, partial, modeled-only, stale, and unavailable cases. Missing runtime/coefficient evidence produces a null whole-model scalar and incomplete coverage.
6. Alias uniqueness: cross-table triggers reject canonical/alias namespace collisions; the view gives defensive canonical precedence; the fixture inventories recently observed telemetry rather than a fixed expected list.
7. False precision/history: unisolated operator/catalog electric points have bounds; daily and 30-minute historical models select the coefficient revision valid for the historical time instead of the current revision.
8. Ownership: adjacent schema/ingestor/test paths and controller grants for generated Grafana and the fail-closed renderer are recorded in `lane.yaml`.
9. Raw conflicts: contradictory totalizer values at one timestamp create a `source_conflict` event and reset the baseline without accepting invented gallons.
10. Gas provenance: the equipment renderer selects the applicable electric or gas coefficient, so `heat2` no longer loses its gas/nameplate evidence.

The independent re-review at `b03c417` also returned `REQUEST_CHANGES`. It confirmed the watermark, repeated-run ambiguity, primary API/MCP/scorecard consumers, collision triggers, energy-health states, historical selection, gas rendering, migration safety, and schema restore, but found eight residual defects:

1. Fertilizer-master evidence proved presence, not positive-duration overlap with the wall-fert relay.
2. Runtime energy still accepted populated `daily_summary` fields rather than complete transition evidence.
3. Contradictory raw meter samples could leave ledger health fresh and scoring-eligible.
4. Conflicting relay states exactly at an interval boundary could seed nondeterministically.
5. Historical `legacy_catalog_085` coefficients remained exact-looking and scoring-eligible.
6. Additional Grafana, snapshot-writer, and Prometheus paths still emitted legacy or zero-filled resource scalars.
7. Alias inventory evidence was circular instead of an independently captured physical-output inventory.
8. The lane omitted `verdify_schemas/tests/test_mcp_responses.py` from ownership and validation scope.

Schema correction `78fcc00` and consumer correction `6167e3b` close those residuals. Wall fertigation now requires actual positive-duration master overlap; runtime energy uses only complete `v_equipment_runtime_daily` evidence; conflicts degrade ledger health; boundary seeds require one unambiguous state; every historical catalog point has conservative bounds; all site resource panels, the snapshot writer, Prometheus exporter, and legacy cost compatibility view preserve unavailability; the fixture independently inventories 39 observed slugs and classifies the 18 physical outputs; and MCP response tests are explicitly owned and green.

Disposable SQL fixtures, fresh schema restore, idempotence, outer rollback proofs, production-shaped replay, focused tests, generated-dashboard parity, and lint are green. The optimized from-zero replay processed 107,467 samples into 20,544 events in 33.33 seconds; an immediate rerun processed zero rows in 0.11 seconds.

The third review found two additional cross-surface blockers before approval:

1. At `77fba8b`, `scripts/brand-grafana-embeds.py` still rewrote corrected resource panels back to raw `daily_summary` scalars and zero-filled monthly costs. `675cb96` moves all three mutating SQL constants to `v_daily_kpi`, preserves `resource_terms_available` and `NULL` months, and adds source-versus-normalized generator parity checks.
2. At `675cb96`, `v_daily_kpi`, `v_planner_performance`, and `fn_planner_scorecard` could expose `therms`/`cost_gas` while aggregate resource evidence was unavailable. `6ea8ab1` gates both gas scalars and adds an adversarial fixture with nonzero legacy therms/gas cost plus unavailable evidence.

The independent critic returned **APPROVE** for exact implementation head `6ea8ab18633fbfd9638899495cdaaabb0ffa9fcf`. It independently verified migrations/fixtures/idempotence/safety, fresh schema restore, conservation and ambiguity behavior, fertilizer overlap, transition-only energy, gas nulling, Grafana generator/ConfigMap parity, exact remote head, and fully green PR checks. Production remained untouched; release-control still owns migration, restart, rollout, and live acceptance.
