# Resource-accounting independent critic record

Independent data-integrity review returned `REQUEST_CHANGES` at `722a90c`. The implementation was not merged. Schema correction `4b27f47` and consumer correction `23340fe` close the findings as follows.

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

Disposable SQL fixtures, schema restore, idempotence, rollback proofs, source-shaped replay, focused tests, and lint are green. Re-review of `23340fe` or later is required before merge.
