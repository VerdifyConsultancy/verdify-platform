# Native route measurement followup for #371

Migration 273 exposes a bounded ordinary-role reader of the existing native callback ledger. The SQL/API/MCP/public scorecard retains `native_fixed_panel_route_evidence`; the added `route_measurement` variant carries independent temperature and VPD eligibility, shared six-field joint bins, in-band percentages, high/low misses and mean distance, worst measured source route, exact prospective target/source lineage, and explicit missingness over all 72 expected daytime bins. It does not qualify `physical_crop_band_evidence`.

## Reproduced historical acceptance

The final migration was rehearsed after the exact 271/272 sources in one rollback-only production transaction on ledger 270. Its predecessor and successor catalog seals passed. API and MCP ordinary login roles both read September 29 successfully within the reader's three-second statement budget. Rollback readback returned ledger 270 and no new reader function.

The real September 29 aggregate, retained in `tests/fixtures/native-route-sep29-measurement.json`, has:

| Axis | Eligible / expected | In band | High / low misses | Mean outside distance | Worst source route |
| --- | --- | --- | --- | --- | --- |
| Temperature | 72 / 72 | 47 (65.2778%) | 25 / 0 | 0.5744447 °F | north |
| VPD | 72 / 72 | 72 (100%) | 0 / 0 | 0 kPa | north (stable tie) |
| Joint | 72 / 72 | 47 (65.2778%) | — | — | — |

The joint counts and lineage exactly match existing frozen native receipt 1: target revision 2, binding 1, contributor revisions `[5,4,6]`, projection SHA256 `2837c7a22a4d23eefca47ff24bf9a276a9fd461401dd61f5919832cb78f75074`. This adds the missing axes/distance/missingness; it does not replace the historical frozen receipt or claim a newly frozen full-window observation.

The October 5 rehearsal returned `missing_or_ambiguous_prospective_target`. No current target, contributor or binding was invented or backdated. Physical hardware identity, actual crop placement, Modbus poll time, physical proof, experiment endpoint eligibility and causal effect all remain false.

## Current-day delivery procedure

After the final collector release is running, choose a future quarter-hour boundary with enough time to capture and declare its source before that boundary. Prepare only the remaining bins today:

```sh
python research/planner-efficacy/freeze_route_day_target.py \
  --day 2026-10-05 --effective-from '<future ISO8601 timestamp with offset>' \
  --out '<new private evidence directory>'
```

The helper reads the actual profile/assignment catalog and revisions, freezes exact profile/assignment hashes and bands, and emits an insert-only target declaration. It refuses a started, naive, unaligned or out-of-day interval. Its target is an analysis reference; it does not change controller setpoints.

Declare the target, three `route_only` contributors and one native source binding with the same future interval, before its start. Bind the actual final collector source revision and device-reported firmware, the exact north/east/west route manifest and its source artifact hash. Retain the byte-hashed source artifact privately and publish its nonsensitive metadata. Neither revision IDs nor hashes authenticate a physical sensor serial, installation position, crop inventory or poll time. The existing source-binding and contributor guards enforce prospective timestamps; use their insert-only contracts. An expired declaration must be replaced by a later prospective interval, never backdated.

After the first completed bin with enough fresh callback minutes, capture the SQL reader and every existing consumer through `scripts/scorecard-consumer-acceptance.py`, binding the final API/MCP/build revisions and publisher generation. Compare the entire typed native evidence, not just legacy scalar credit. Verify current-day missingness sums to `72 - eligible_bins` for each axis, past undeclared bins remain unavailable, future bins remain not elapsed, and independent missing humidity leaves VPD/joint unavailable while fresh temperature can remain measured. Verify September 29 parity simultaneously.

The reader rejects session invalidation, connection/gap markers, source-sequence holes, mixed transport generations, wrong firmware/collector/route identity, callback skew over 30 seconds, ambiguous targets/bindings, and bins without at least 12 fresh minutes. Missing observations are unavailable, never zero-success measurements. All endpoints retain the existing fail-closed physical publication.

## Remaining acceptance

These are source and rollback rehearsal receipts. They do not establish production deployment, a live current-day declaration or post-release consumer equality. Root delivery must capture those end states after rollout. #371 physical crop outcome qualification remains unavailable until its independently authenticated inventory/commissioning evidence exists; route diagnostics must not be relabeled as that outcome. No waiting for November 2 is needed to expose honest current source-route diagnostics.

## Product Grafana and planner consumer boundary

The planning-quality dashboard now has native source eligibility/distance/severity and prospective-lineage panels near the top. Both call the same bounded native reader for the Denver day at the selected time-range end; each axis includes its own denominator, units, both miss directions, mean distances, worst source route and missingness. Nulls render as `Unavailable`. Every row explicitly labels physical crop outcome `unqualified`; the lineage panel retains the definition, hashes, revision IDs and partial-window status. Existing binary reading fractions and graded credit remain separate.

The unapplied migration 273 was amended to give only the dedicated Grafana duty role access in addition to API/MCP. Its API and ingestor successor catalog seals were remeasured and the receipts advance atomically; the MCP seal remains unchanged. Exact dashboard SQL executed as effective `verdify_grafana_runtime_login` in the final rollback rehearsal, with a three-second statement timeout, for both September 29 and October 5. A separate read-only synthetic missing-humidity projection retained measured temperature while preserving null VPD/joint percentages and distances. The recorded query hashes and effective-role capability readback are bound to source in `tests/fixtures/native-route-grafana-query-receipt.json`. No raw native table access was granted. These are rehearsal/query receipts; live Grafana adoption and rendered labels require post-sync readback.

The standalone graph is a caller-supplied context boundary: `planner_graph/api.py` copies `PlannerContextPack.scorecard_summary` directly into the graph state. Repository-wide source inspection finds no caller of the retained optional `VerdifyReadClient.build_context()` method; the graph uses its DB client for execution verification instead. That unused method was not changed, and the planner role was not granted a new reader capability. The actual Iris planner obtains its typed current/historical scorecard through MCP; acceptance must capture that real consumer rather than present an unused helper or caller-authored fixture as a production planner observation.
