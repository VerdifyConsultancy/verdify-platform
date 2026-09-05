# Fixed north/east/west historical measurement

`research/planner-efficacy/fixed_panel.py` prepares the fixed-panel sensitivity
required by #371/#779. It does not replace the locked experiment's outcome code,
recompute the historical matched estimate by itself, or activate a production
metric. Its definition is `fixed-north-east-west-snapshot-bins-v1`.

The tool has no database client, network path, device operation or current crop
resolver. It consumes a frozen raw snapshot export and an explicit panel/target
contract, preserves every requested UTC 15-minute bin, and writes a new report
with exclusive creation. Original inputs and earlier outputs are never replaced.

## Source trace and the remaining evidence gap

The current source routes the selected panel as follows:

| Zone | ESPHome temperature / VPD object IDs | Climate columns | Modbus route |
|---|---|---|---|
| North | `north_temp___f_` / `north_vpd__kpa_` | `temp_north` / `vpd_north` | `north_wall_probe`, address 2 |
| East | `east_temp___f_` / `east_vpd__kpa_` | `temp_east` / `vpd_east` | `east_wall_probe`, address 5 |
| West | `west_temp___f_` / `west_vpd__kpa_` | `temp_west` / `vpd_west` | `west_wall_probe`, address 3 |

Owning sources: `ingestor/entity_map.py`, `firmware/greenhouse/sensors.yaml` and
`firmware/greenhouse/hardware.yaml`. These are source routes, **not verified
historical hardware serial identities or calibration evidence**. Bus addresses
can be reused. The contract must bind one distinct contributor per zone, its
supporting evidence SHA-256 and validity interval covering the entire comparison.
A repaired/replaced probe requires a new panel version and an explicit comparison,
not silent substitution. South and a house-average/center proxy are rejected.

`ingestor/ingestor.py::write_climate` combines newly received values with a
last-known cache younger than 600 seconds, and stores the flush timestamp. The
sensor callback skips NaN before normal climate routing. Firmware wall VPD is a
template calculation over stored temperature and RH states. Consequently, an
exported climate row timestamp does **not** prove simultaneous fresh measurements
at every probe; repeated rows can reflect cached state. This tool preserves that
limitation as `database_flush_snapshot_not_per_probe_observation_time`. It cannot
recover historical freshness/connection/callback metadata absent from the data.

`fn_crop_band_value` in migration170 reads `crop_band_anchors` and defaults the
season through `fn_current_season()`. Evaluating an old timestamp today is not a
frozen historical target version. Do not fill the contract by silently calling the
current resolver, or relabel historical dispatched bands as crop targets.

## Frozen input contracts

The export object contains exactly:

- `contract_version: 1`, `sample_basis: database_flush_snapshot`,
  `greenhouse_id: vallery`, an explicit-offset `exported_at`, and completed
  `window_start`/`window_end` aligned to UTC 15-minute boundaries (maximum 62 days).
- `rows`: explicit-offset `ts`, `greenhouse_id`, and optional/nullable
  `temp_north`, `vpd_north`, `temp_east`, `vpd_east`, `temp_west`, `vpd_west`.
  Numbers are °F/kPa. Missing/nonfinite/non-numeric values are not zero.
  House-average aggregates and extra fields are rejected, not guessed into shape.

The separate measurement contract contains exactly:

- `contract_version: 1`, `panel_version`, `target_version`,
  `target_evidence_sha256`, and `target_basis`, explicitly either
  `frozen_historical_crop_definition` or `fixed_counterfactual_crop_definition`.
  Those are different scientific interpretations; declare and justify the chosen
  one before interpreting results. A current reconstruction is not historical
  truth. The shared bounds are a **house crop reference**, not each zone's own
  crop-specific optimum or dispatched controller target.
- `minimum_minutes_per_bin`: an explicit integer from 1 through 15. There is no
  hidden threshold default. Existing protocol-v2 uses 12/15, but selecting a
  historical sensitivity threshold here does not modify its locked rules.
- `members`: exactly north/east/west; each has `zone`, `contributor_id`,
  `identity_evidence_sha256`, explicit `valid_from`/`valid_to`, and its canonical
  `temp_field`/`vpd_field`. Contributor identities are unique and fixed throughout
  the requested window. No best-available probe list or within-window replacement.
- `targets`: at most one record for each `bucket_start`, with explicit `temp_low`,
  `temp_high`, `vpd_low`, `vpd_high` evaluated/frozen at that bin's start. No
  interpolation or carry-forward is performed. Missing bins, null/nonfinite
  bounds and inversions remain unavailable; they do not get default targets.

`tests/test_fixed_panel.py::fixture` is a complete **synthetic** contract example,
not a production target or identity inventory. SHA format and structural checks
do not authenticate evidence. Output explicitly says the supplied evidence
hashes are not independently authenticated; retain the referenced artifacts for
review rather than treating a hash string as a scientific approval.

## Calculation and interpretation

1. Scope the declared greenhouse and half-open window; count excluded rows.
   Collapse exact timestamp duplicates per field. Any finite/missing/value
   disagreement at the same timestamp invalidates that field's entire minute.
   Within each UTC minute, average finite unique timestamps once. Extra polling
   cannot add minute weight. No interpolation or continuous exposure is inferred.
2. For each axis, admit a minute only when **all three** panel members have a
   valid value. Compute every zone's 15-minute mean on the same complete-panel
   slots, then give the three zone means equal weight. Missing west never turns
   the endpoint into a north/east mean. Temperature and VPD have independent
   eligibility and report per-zone missing counts and conflicting minutes.
3. Compare the bin's fixed-panel mean with its frozen shared crop bounds. Report
   binary in-band, low/high/outside distance, each zone's corresponding result,
   mean zone distance, maximum zone distance and all tied worst zones. All are
   **bin-mean comparisons**, not peak-event measurements or continuous stress.
   Distance of the panel mean, mean zone distance and worst zone distance differ
   because distance is nonlinear; none is silently substituted for another.
4. Joint comparisons require the intersection of all six member/axis fields.
   Recompute both panel means on that intersection. Independently sufficient
   axes do not establish sufficient joint observations.
5. Preserve every requested bin, including null results/reasons. Summary provides
   eligible/expected bins, unavailable reasons, longest unavailable runs, binary
   in-band bin percentages and distance summaries. Null denominators stay null;
   measured zero remains zero. There is no assigned-day or daily-validity gate
   here: this is a historical measurement table, not a randomized ITT freezer.

No attributable/graded controller credit enters this calculation. The report
includes the window, member records, declared thresholds, target basis/version,
contract hash, canonical input hash and calculator source hash. The CLI also
records exact input-file and contract-file hashes. Different raw row ordering
changes the input identity but not calculated bins. Physical proof, experiment
endpoint, causal-effect and measured-center claims remain false.

## Reproduce and collect safely

Emit the explicit read-only export for an authorized collector (this command
prints SQL and does not connect):

```sh
python research/planner-efficacy/fixed_panel.py emit-sql \
  --start 2026-07-11T06:00:00Z --end 2026-08-14T06:00:00Z
```

The SQL uses one repeatable-read, read-only transaction, bounded statement/lock
timeouts and an explicit six-field allowlist. It does not query credentials,
targets, unrelated houses or controller history. Preserve its one JSON result
privately with source/export provenance. Use the existing authorized export
interface; do not invent ambient database access or place raw inputs on the lab.

After obtaining and reviewing the identity and target evidence:

```sh
python research/planner-efficacy/fixed_panel.py analyze \
  --input /private/export.json --contract /private/frozen-contract.json \
  --output /private/new-fixed-panel-report.json
```

Repeat to a second new output and compare bytes/hashes. Existing outputs, or an
output path equal to either input, are refused. Malformed inputs fail without
echoing their contents. Hashes are reproducibility identities, not recoverable
copies of the underlying data; keep both input artifacts.

Tests and optional actual SQL export round trip:

```sh
FIXED_PANEL_TEST_PG_BIN=/path/to/private-postgres/bin uv run pytest -q \
  tests/test_fixed_panel.py --junitxml=/path/to/scratch/fixed-panel.xml
```

The test fixture starts and stops its own private Unix-socket cluster with TCP
disabled and no production credentials. Run with PostgreSQL 15 and 16. Without
the explicit binary directory, the SQL test skips; do not call that SQL acceptance.
The generated fleet CI selection does not currently include this new test.

## Remaining campaign acceptance

The existing September 5 `climate-7d.csv` is a public house-average rollup, not
these raw six-field rows; it cannot satisfy this input contract. No fixed-panel
August effect, matched count or historical target identity is claimed here.

Obtain the exact raw export, contributor validity evidence and chosen frozen
target contract; reproduce the original population/matching with coverage,
common-support/exclusion diagnostics and sensor/firmware/delivery/target strata.
Keep earlier estimates immutable and label associations as noncausal. Feed the
empirical variance/completeness into #782 before prospective lock.

Production writer/DB/API/MCP/planner/public measurement integration, immutable
target/contributor capture, real-data comparison and verified delivery remain
required for #371. This offline PR changes none of those runtime consumers, no
historical aggregate, no firmware/configuration, and no protocol assignments or
launch authority. #371, #779 and the whole campaign remain open.
