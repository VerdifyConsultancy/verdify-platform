# Frozen resource ledger and commissioning boundary

The historical producer counterexamples are now superseded in source by the
[paired producer/interval repair](shelly-source-intervals.md). That candidate is
not deployed or commissioned; the frozen baseline below remains byte-unchanged.

Campaign #775 / #781. This is historical baseline evidence and a versioned
offline data contract, **not resource commissioning or a runtime correction**.
The 22 completed America/Denver dates are August 14 through September 4, 2026.
The extraction completed September 5 at 15:11:16 UTC. Original snapshots remain
unchanged outside Git; only their allowlisted daily projections and hashes are
published. No DB, device, image pin, resource coefficient, protocol or score is
changed by this work.

## Reproduced result

`research/planner-efficacy/resources-2026-08-14_2026-09-05.baseline.json`
reproduces the acceptance equation with decimal arithmetic:

**5047 accepted gallons = 1361 attributed + 3465 ambiguous + 221 manual/unattributed.**

All 22 days individually conserve volume; the scope subtotal equals attributed
volume on every day. All 1361 attributed gallons are labeled climate wetting;
wall irrigation, wall fertigation and unsupported-path attributed totals are
zero *as reported*. This does not prove physical absence of those activities.
Ambiguous water is 68.7% of accepted volume, and is not reassigned to climate
wetting, divided among overlapping equipment or converted into savings.

The source labels 13/22 water days available for scoring (2361 gallons on those
days). Fifteen days have ledger quality `ok`, seven `discontinuous`; conservation
alone does not imply eligibility. Source flags are retained, not recomputed or
promoted to scientific commissioning.

| Result | Observations | Interpretation |
|---|---|---|
| Partial two-channel electricity | 124.384 kWh, 22/22 days, source eligible 22/22 | Historical reported integration, not certified circuit coverage or whole-facility energy |
| Whole-controlled-equipment runtime model | 342.025 kWh subtotal, 21/22 days, source eligible 0/22 | One missing day; complete-period total is null; coefficient-based model, not measured energy |
| Gas, interior DLI, resource cost, measured uncertainty | Null | Not established by these inputs |

Never subtract these electricity quantities as waste, savings or model error:
their scopes differ. Never sum them into a facility total. Model coefficient
bounds are not meter calibration uncertainty or statistical confidence limits.
The nine-stream sum (eight relay-active streams plus vent-open duration) remains
an equal-weight control-state burden diagnostic. Vent-open time is not vent
motor travel time, and the sum is not electricity, water, cost or carbon.

## Reproduction and byte binding

Run at the exact PR commit using Python 3.12 and the retained extraction:

```sh
python research/planner-efficacy/resource_ledger.py \
  --evidence-dir /home/agent/reports/verdify-review-2026-09-05/evidence \
  --manifest /home/agent/reports/verdify-review-2026-09-05/evidence/manifest.json \
  --manifest-sha256 da7c871afbad36c99fb1acbcc43c5e485c9001acc3a34e36c119dd1ebb36a25a \
  --start 2026-08-14 --end 2026-09-05 \
  --output /workspace/verdify-platform/scratch/resource-ledger-reproduction.json
cmp /workspace/verdify-platform/scratch/resource-ledger-reproduction.json \
  research/planner-efficacy/resources-2026-08-14_2026-09-05.baseline.json
sha256sum research/planner-efficacy/resources-2026-08-14_2026-09-05.baseline.json
python -m pytest -q tests/test_resource_ledger.py
```

Choose a new output filename if it already exists; the tool refuses overwrite.
It does not fetch data or touch any original input. The output embeds the pinned
manifest hash, each exact input hash/byte count/request identity, tool hash and
six audited source-file hashes. Those source hashes identify the reviewed
checkout, **not proof of the software deployed at extraction time**. The
published output hash is frozen in the regression test and PR receipt.

Supplied hashes bind bytes, not server authenticity, hardware identity or
physical measurement. A clean checkout without the retained raw snapshots can
run all synthetic/committed-artifact tests, but cannot independently replay the
actual extraction. Do not replace that missing-input condition with a fresh API
request and call it the same historical reproduction.

## Offline contract `historical-resource-ledger-v1`

- Exact requested local dates, end exclusive, maximum 62 days. Require completed
  dates at request time, one HTTP 200 JSON manifest entry per expected filename,
  the exact public endpoint URL, timezone-aware request/extraction timestamps,
  matching root/nested date and greenhouse, exact length and SHA256 before
  parsing. Reject duplicate JSON keys, duplicate dates, path traversal, symlink
  inputs, missing files, wrong hashes, nonfinite and invalid typed quantities.
- Quantities serialize as decimal strings; booleans/counts remain typed. An
  absent section/value is null and visibly unavailable, never zero. Known zero
  remains an observation. Signed measured electricity is preserved for possible
  export, not silently clamped or made positive.
- Every requested day remains in the output. Aggregate `selected_days`,
  `observed_days`, `missing_days`, `observed_subtotal` and `complete_total` are
  separate. Any missing or unscoped energy observation makes the complete
  period total unavailable. The source-eligible subset explicitly names its own
  denominator; its total is not the full-window total.
- Independently recompute daily water conservation and attribution-scope
  conservation, compare source residuals, flag discrepancies over 0.001 gallon
  (the existing source tolerance), and retain source quality/eligibility.
  Internal-consistency `audit_issues=[]` is **not evidence of commissioning**.
- Electricity values retain their named scope. Unknown scopes are refused;
  missing scope is flagged and excluded from scoped aggregation. Never emit the
  legacy cross-scope `estimate_delta_kwh`, raw runtime/coefficient arrays, or
  current health timestamps as historical completeness evidence.
- Commissioned water/electricity, whole-resource/cost claim, physical-proof and
  experimental-endpoint flags remain false. Uncertainty and scientific minimum
  coverage remain null because this extraction does not establish them.

## Audited source defects and limits

1. `ingestor/tasks/ha.py::shelly_sync` forms a sum using zero for either missing
   power channel. Any available mapped entity, including only a cumulative
   counter, can trigger insertion with zero power. The row is stamped at fetch
   time, with no persisted channel completeness or per-channel sample freshness.
   `tests/test_resource_ledger.py` executes this actual function via AST
   isolation and fake I/O to preserve five baseline counterexamples. These
   passing tests document a defect; they are not a producer-fix acceptance test.
2. `_SHELLY_ENTITIES` in `ingestor/tasks/_common.py` maps channel 0 power to
   `watts_other`, channel 1 power through `abs()` to `watts_heat`; `watts_fans`
   is a hard-coded zero. Channel 0 cumulative active energy becomes `kwh_today`
   without this function establishing a daily reset boundary. These software
   labels do not certify the actual connected circuits. HA state timestamps
   likewise do not by themselves prove fresh physical measurements.
3. `v_energy_daily` in migration 194 filters null total watts **before** computing
   the next timestamp, integrates up to 900 seconds per interval, and uses a
   fixed 21.6-hour eligibility threshold. The final sample contributes zero
   interval duration. Date casting/day-length arithmetic is session-timezone
   sensitive. The daily rollup cannot recover missing-channel provenance or
   establish DST-correct scientific coverage. Changing the writer alone to null
   without correcting interval segmentation would allow bridging missing rows.
4. Water source views classify accepted positive meter deltas and attribute
   them across actuation intervals; source event sums use zero defaults. The
   audit preserves the published result, not reconstructed raw meter/reset or
   calibrated flow evidence. `command_only_runs` remain counts, never gallons.

These findings explain why historical source scoring flags cannot commission
an endpoint. They do not prove which actual rows had missing channels, nor
establish the cause of any particular water discontinuity or CI failure.

## Required forward commissioning contract

The narrowest existing measured electricity *candidate* is the two-channel
meter scope; the narrowest water *candidate* is independently supported,
attributed climate-wetting volume. **Neither is commissioned by this audit.**
Until the following evidence exists, #782 must retain climate/runtime
exploratory scope; whole-resource claims remain gated under #787.

| Contract dimension | Required evidence before eligibility |
|---|---|
| Identity and units | Meter/counter ID, installed circuit or plumbing boundary, channel-to-load mapping, units and conversion, calibration revision with validity interval |
| Calibration and uncertainty | Reference measurement, error/bias bounds and uncertainty propagation for the exact measured/attributed scope; no nameplate/model range as a substitute |
| Water epochs and attribution | Raw counter timestamps, reset/reboot epochs, zero/high-delta/gap disposition, accepted-versus-excluded deltas, event overlaps, manual volume and conservation at raw and daily boundaries |
| Electricity intervals | Raw per-channel value/availability and source timestamps; reject nonfinite/conflicting samples, break incomplete/stale/gap intervals before integration; explicit export/sign and maximum-hold policy |
| Coverage and missingness | Versioned endpoint-specific minimum coverage chosen before lock, denominator for the actual local-day or 06:00–24:00 outcome window, DST tests, gap limits and explicit unavailable reasons |
| Aggregate eligibility | All required streams individually eligible on the same valid boundary, no ambiguous attribution borrowed across streams, no model filling, no inference from a present rollup or current health row |
| Cost inputs | Commissioned quantity plus named price/units/currency/effective dates/tariff boundary and uncertainty; required missing quantity or price makes cost null, never free |

A future producer/forward-SQL/typed-consumer change must be coherent and tested
together; historical samples without the newly required provenance must remain
explicitly legacy/unqualified. Applied migration 194 is immutable. Preserve this
baseline and original extraction; publish any later result under a new filename,
version and hashes with a date-by-date explanation of changes. Never backfill
channel freshness, reset identity or calibration from inference.

The CI contract is registry-generated and currently does not select this new
test module. Run its explicit local command and retain receipts; have the owner
integrate it into the declared check selection rather than editing the generated
mirror. Normal CI/merge remains required for delivery. This offline change needs
no manual SQL, physical action or deployment to reproduce; docs are runtime build
inputs in this repository, so runtime impact still follows normal delivery rules.

## Remaining #781 acceptance

The frozen ledger/reproducer and source counterexamples establish the historical
baseline. Remaining work includes actual circuit/meter/calibration evidence,
versioned interval/eligibility repairs, production contract adoption, scoped
uncertainty and coverage qualification, and the empirical handoff into #782.
Do not close #781, #371, #783 or the campaign on this artifact alone.
