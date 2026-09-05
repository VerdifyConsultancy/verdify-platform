# Observed-minute diagnostics — not crop or experiment endpoints

Definition `house-average-observed-minute-v1` adds a separately named JSON field,
`daily_summary.climate_observed_minute_metrics`. It fixes row-count/missingness
problems for this diagnostic without changing legacy binary/graded fields,
resource calculations, controller settings or locked experiment endpoints.

## Calculation contract

- Read `climate.temp_avg/vpd_avg` and `setpoint_changes` only for vallery.
  This remains a house average with potentially changing sensor membership,
  not a fixed scientific panel or an invented center measurement.
- Use the requested Denver local day, clipped to the last completed UTC minute.
  DST days retain their real 23/25-hour lengths; unfinished/future minutes are
  not reported as measured slots or sensor gaps. Midnight can yield a zero-minute
  window with unavailable fractions.
- Collapse identical timestamps. A conflicting duplicate invalidates that
  axis's minute, not the unaffected axis. Missing/nonfinite/boolean values are
  not measured failures. Average finite unique-timestamp values within a minute,
  then give each eligible minute equal weight regardless of polling frequency.
- Resolve each bound from the latest setpoint-log event at or before minute start.
  Keep all same-timestamp ties; conflicting/nonfinite/missing/inverted bounds or
  an expired/superseded latest event make that axis-minute unavailable. Never
  search backward for an older complete target. No device acceptance/freshness,
  frozen crop history or target-version validity is inferred from log events.
  The table supports requested and ESP32-feedback origins. The current ingestor
  suppresses band-owned feedback echoes, but that does not prove all historical
  rows are desired commands. Source tags are input-hashed; provenance remains
  explicitly unqualified rather than promoting a matching log value to truth.
- Report each axis's eligible/in-band counts, percentage, high/low observed-miss
  minute counts, mean high/low/outside distance, coverage and longest ineligible
  run. Joint eligibility requires both axes in the same minute. Empty eligible
  denominators produce null; genuinely measured zero stays zero.

Temperature distances are °F and VPD distances kPa. An observed-miss minute is
an occupied sampling slot, **not proof of one minute of continuous exposure**.
No missing-interval interpolation, hold-last-value integration or physical
stress hours are produced. Averaging can hide within-minute extremes; this is
not an extreme-temperature or worst-zone endpoint. `worst_measured_zone` remains
null and fixed-panel/duration-weighted/physical-proof/crop-outcome/experiment
eligibility flags are false. The SQL constraint refuses promotion of those flags.

## Source, input and revision binding

The payload includes its definition, calculation module SHA-256, scoped input
SHA-256, greenhouse/window and eligibility counts. The input hash includes all
scoped sample rows and selected setpoint events, including duplicates and invalid
value markers, with order-independent canonical serialization. The source hash
identifies the module bytes, not an authenticated running-image identity.
Hashes alone do not preserve raw data: an immutable raw export and target/panel
manifest are still required for a reproducible historical scientific analysis.

The daily writer appends this diagnostic after its existing legacy work. The
new read/calculation/write uses a repeatable-read transaction and requires one
vallery daily row. Missing migration245 returns unavailable; once installed,
SQL/capture errors propagate and roll back this diagnostic write. Legacy updates
earlier in the existing refresh are separate, as before; this change does not
claim the entire legacy daily refresh is now atomic.
The existing historical reconciliation utility already wraps a whole day in a
transaction; its outer isolation now also uses repeatable read so the nested
diagnostic can run there and dry-run rollback still removes all tentative rows.

Migration245 requires244 and expands capture layout to
`daily-summary-capture-v2` without changing prior v1 records. The existing
payload helper is replaced in place, preserving OID/owner/ACL and cached trigger
bindings. New diagnostic changes receive before/after revision capture; repeated
identical results do not create revisions. No existing daily values are backfilled
or relabeled by the migration itself.

## Typed read contract

Migration246 adds `fn_observed_minute_diagnostic(date,text)`: exactly one explicit
day, vallery only, with API-duty EXECUTE and no new raw-table grants. It is a
database-owner SECURITY DEFINER function with a fixed search path and qualified
relations. The reader selects the newest capture **before** checking it. A
missing daily row, uncomputed diagnostic or current/captured payload mismatch is
unavailable, never a fallback to an older valid capture. It changes no stored
metrics or prior revision rows and is safe under the normal outer transaction.

`verdify_schemas/observed_minutes.py` validates the entire diagnostic's definition,
hash formats, window/day/greenhouse, counts, percentages, units, distance sums,
missingness, possible joint intersections and eligibility flags. An available
snapshot must have matching v2 capture metadata and nonfuture evaluation and
revision timestamps. Empty eligible fractions remain null; measured zero remains
zero. Unknown fields and malformed/promoted evidence are withheld without echoing
payloads or validation inputs. This is internal consistency checking, not raw
recomputation or authentication of the recorded calculation hash.

The shared API/MCP adapter uses a 3-second server statement budget, a 3.5-second
client query budget and a transaction/savepoint. Missing migration/reader grants
or timeouts become explicit unavailable reasons without poisoning an outer
transaction. Other database failures are not silently hidden.

`observed_minute_evidence` is separate from numeric `fn_planner_scorecard` metrics
in the scorecard API/MCP, public home response and evidence snapshot. Default-day
scorecards bind the numeric and diagnostic reads to the same resolved Denver day;
public snapshots bind it to their generated-at day. They are not claimed to be a
single transaction across every legacy metric and public query. The static
publisher revalidates the envelope and day before showing per-axis/joint eligible
denominators, evaluated window, revision and source/input hashes. It does not
substitute legacy controller credit when the diagnostic is missing.

Availability means a structurally valid **captured snapshot**, not a fresh live
measurement. `currentness=captured_snapshot_not_live_freshness_not_assessed` is
explicit: even a recent unrelated climate-metric revision may retain the same
older diagnostic window. Coverage is relative to that evaluated window, not
automatically a complete local day. Inspect window_end as well as recorded_at.
This release does not modify planner rewards, the standalone planner's context
collector or any scientific endpoint. MCP documentation keeps diagnostic-only
limitations visible to tool readers.

## Delivery and remaining work

Deliver migrations244/245/246 through the reviewed ledgered migrate-image path, then
the compatible ingestor image. Compatible API/MCP/publisher images may precede246
and return unavailable until it arrives. Preserve migration241's consumer-first
requirements documented in the contract-2 runbook. The ingestor handles absent245 without fabricating
diagnostics, but must not be called deployed merely because old pods are healthy.
Require exact source/pins, migration hashes/ledger, Argo Synced + Healthy, restored
production-data/role/lock/overhead checks and actual daily diagnostic/revision
readback. Retain both raw exports and the append-only revision ledger for rollback;
leave the legacy fields intact and use forward corrections instead of erasing
evidence. No live migration or physical action is performed by these instructions.

API/MCP/public publication is implemented but not yet deployed or live-qualified;
the standalone planner collector is unchanged. The old public aliases are not
automatically upgraded by this diagnostic's presence. #371 also
still needs a fixed sensor/target contract, true crop-band distance/severity and
worst-zone outputs, historical/current-day source-bound comparisons and normal
runtime acceptance. The locked experiment's minute/bin/missingness rules and
scientific decisions remain separate; do not replace them with this diagnostic.
