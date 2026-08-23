# Frozen-FSM baseline candidate + AI template candidates (Lane G, #588)

Status: **CANDIDATE — pending horticultural/firmware/safety approval
(gate:jason)**. Nothing in this directory is an executable treatment
definition; per audit §8.2 the baseline becomes usable only after the full
approval chain below.

Artifacts (committed, derived; raw extraction CSVs stay outside Git):

- `frozen-fsm-baseline-candidate-2026-08-14.json` — the per-field baseline
  candidate derived from stable same-firmware effective readbacks.
- `ai-template-candidates-2026-08-14.json` — the two reviewed 48-field AI
  template candidates (moderate / aggressive hot-dry response) whose
  differences from baseline are confined to the §8.2 11-field allowlist.
- `extract-baseline.sh` + `baseline.py` — read-only extraction and
  deterministic artifact builders (the SQL lives in `baseline.py` and its
  SHA-256 is recorded in the artifact). `baseline.py requantize` rebuilds a
  committed artifact under the current wire schema WITHOUT touching the
  database (used for the contract-v2 refresh below).

**Contract v2 (#588)**: wire schema v2 retired `direct_wet_stress_latest_hour`
(wire_id 6 — the v1 reserved zero-consumer row and the ONLY unqualified
field). The committed artifacts are the original 2026-08-14 v1 extraction
**requantized** to the 48-field schema (`provenance.requantized_schema_version:
2`; original SQL text + SQL/input-CSV hashes preserved verbatim). With the
dead field gone, **all 48 fields qualify** and the canonical vector bytes +
`content_sha256` are now emitted for the baseline and both templates:

- baseline `content_sha256`
  `c090f769be541cde90e2242568fcf5182b3ac764621a37111764b51eea18d795`
- moderate `content_sha256`
  `94446c6cd5e20cd00f3316f7fa1e962e8ff62f9a4dea50d26f41d35f8af0090f`
- aggressive `content_sha256`
  `0439e618a9a8a64af103fdced9fa946294776496e900b95d04819676d301f5a9`

These hashes are CANDIDATE identities — nothing actuates before the approval
chain below (gate:jason).

> **Identity warning for protocol v2:** the three `content_sha256` values above
> use the historical revision-bound vector-content domain. They must never
> populate `baseline_state_content_sha256` or another v2
> `policy_state_content_sha256` field. Applying the new state-only formula to
> these same historical bytes yields baseline
> `02aaec81f48488830079c0f39821012b52af70254f7c9e8119a628cfe6cc5e38`,
> moderate
> `730df1ff380e78abf61610599c3b1fdde3901303cd286edb23f10aceb52e4445`,
> and aggressive
> `bc93ba1a18ccf3429cb89222affec92fde8b6bbcb5ef72a6b96d0eebf8333eb3`;
> those are also non-lockable historical candidates.
> Regenerate all three on the actual deployed setter grid, then compute and
> golden-test the complete v2 hashes before lock.

## Method (audit §8.2)

- **Source**: `setpoint_snapshot` effective device readbacks for greenhouse
  `vallery` — the same effective-readback source §5's
  `effective_tunable_posture` used (see
  `../extract-current-firmware.sh`). Production DB, read-only
  (`BEGIN READ ONLY`), reached through `scripts/lib/psql-verdify.sh`
  (`VERDIFY_DB_BACKEND=kube` on fleet pods →
  `kubectl exec -n verdify-prod verdify-db-0 -- psql -U verdify -d verdify`).
- **Window**: Denver-local days **2026-07-12 through 2026-08-04 inclusive**,
  **excluding 2026-07-25** (reboot day). Effective window = 23 local days =
  1,987,200 s (no DST transition inside the window).
- **Weighting**: each readback value is weighted by the duration it was in
  effect — intervals span consecutive snapshots, are clipped to the window,
  and any time inside the excluded day is zero-weighted. A carry-in row (the
  latest readback at or before window start, 3-day lookback) covers the head
  of the window, which is why every present field shows exactly 1,987,200 s
  of coverage.
- **Statistics**: numeric fields take the **time-weighted median**; switches
  take the **time-weighted mode**. Results are then quantized round-half-even
  through the canonical wire schema
  (`verdify_schemas.policy_vector.quantize_policy_values`,
  wire schema v2, manifest digest recorded in the artifact).
- **Unqualified policy**: a field with no qualified readback in the window is
  listed as UNQUALIFIED and **blocks baseline approval** — no boot default is
  silently substituted, and the canonical 48-field vector bytes/content hash
  are omitted until every field qualifies. (Under wire schema v2 every field
  qualifies; the v1 artifact was blocked solely by the now-retired dead row.)

Extraction provenance: SQL SHA-256
`72a83c13028a11b5fb0d0b1aed08e00b37dccdfd9bd8ce169e20ad65c369354c`,
input histogram CSV SHA-256
`bfabce68f393d990d13b4b9097076f910a49c65b5a18afc6a778cbdad065064a`
(501 data rows). Both are embedded in the artifact and re-checked by
`tests/test_baseline.py`.

## Per-field coverage

All 48 wire fields have complete readback coverage (every field
covers the full 1,987,200 s effective window). Interval counts are ~32.5k
(one-minute readback cadence over 23 days; the 31,766-interval rows are
fields with a slightly sparser readback cadence — consecutive-snapshot
intervals still tile the full window). `raw` is the pre-quantization statistic; `quantized` is the
canonical wire-grid value.

| wire_id | field | stat | raw | quantized | intervals | coverage s | distinct |
|---:|---|---|---:|---:|---:|---:|---:|
| 1 | `band_track_fraction` | median | 0 | 0.0 | 32453 | 1987200 | 1 |
| 2 | `cold_vent_guard_delta_f` | median | 8 | 8.0 | 32453 | 1987200 | 5 |
| 3 | `cool_exit_hysteresis_f` | median | 1.69 | 1.7 | 32453 | 1987200 | 16 |
| 4 | `cool_stage2_exit_hysteresis_f` | median | 1 | 1.0 | 32453 | 1987200 | 1 |
| 5 | `cool_stage2_over_high_f` | median | 0 | 0.0 | 32453 | 1987200 | 67 |
| 7 | `direct_wet_stress_min_dew_margin_f` | median | 8 | 8.0 | 32453 | 1987200 | 1 |
| 8 | `direct_wet_stress_vpd_margin_kpa` | median | 0.05 | 0.05 | 32453 | 1987200 | 2 |
| 9 | `dwell_gate_ms` | median | 225000 | 225000.0 | 32453 | 1987200 | 11 |
| 10 | `enthalpy_close` | median | 1 | 1.0 | 31766 | 1987200 | 1 |
| 11 | `enthalpy_open` | median | -2 | -2.0 | 31766 | 1987200 | 1 |
| 12 | `fog_escalation_kpa` | median | 0.2 | 0.2 | 32453 | 1987200 | 19 |
| 13 | `heat_hysteresis` | median | 1 | 1.0 | 32453 | 1987200 | 1 |
| 14 | `min_fan_off_s` | median | 90 | 90.0 | 32453 | 1987200 | 1 |
| 15 | `min_fan_on_s` | median | 120 | 120.0 | 32453 | 1987200 | 1 |
| 16 | `min_fog_off_s` | median | 60 | 60.0 | 32453 | 1987200 | 23 |
| 17 | `min_fog_on_s` | median | 59 | 59.0 | 32453 | 1987200 | 20 |
| 18 | `min_heat_off_s` | median | 180 | 180.0 | 32453 | 1987200 | 1 |
| 19 | `min_heat_on_s` | median | 120 | 120.0 | 32453 | 1987200 | 1 |
| 20 | `min_vent_off_s` | median | 60 | 60.0 | 32453 | 1987200 | 1 |
| 21 | `min_vent_on_s` | median | 60 | 60.0 | 32453 | 1987200 | 1 |
| 22 | `mist_backoff_s` | median | 600 | 600.0 | 32453 | 1987200 | 1 |
| 23 | `mist_max_closed_vent_s` | median | 600 | 600.0 | 32453 | 1987200 | 1 |
| 24 | `mist_thermal_relief_s` | median | 90 | 90.0 | 32453 | 1987200 | 1 |
| 25 | `mister_all_delay_s` | median | 80 | 80.0 | 32453 | 1987200 | 12 |
| 26 | `mister_all_kpa` | median | 1.39 | 1.4 | 32453 | 1987200 | 64 |
| 27 | `mister_center_penalty` | median | 0.5 | 0.5 | 32453 | 1987200 | 1 |
| 28 | `mister_engage_delay_s` | median | 40 | 40.0 | 32453 | 1987200 | 12 |
| 29 | `mister_engage_kpa` | median | 1.19 | 1.2 | 32453 | 1987200 | 68 |
| 30 | `mister_min_off_s` | median | 45 | 45.0 | 32453 | 1987200 | 1 |
| 31 | `mister_pulse_gap_s` | median | 30 | 30.0 | 32453 | 1987200 | 16 |
| 32 | `mister_pulse_on_s` | median | 90 | 90.0 | 32453 | 1987200 | 10 |
| 33 | `mister_vpd_weight` | median | 1.7 | 1.5 | 32453 | 1987200 | 68 |
| 34 | `mister_water_budget_gal` | median | 300 | 300.0 | 32453 | 1987200 | 13 |
| 35 | `night_vpd_bias_kpa` | median | 0 | 0.0 | 32453 | 1987200 | 1 |
| 36 | `outdoor_staleness_max_s` | median | 600 | 600.0 | 32453 | 1987200 | 1 |
| 37 | `sw_cool_all_fans_at_high_enabled` | mode | 1 | True | 32453 | 1987200 | 2 |
| 38 | `sw_direct_wet_gate_enabled` | mode | 1 | True | 31766 | 1987200 | 1 |
| 39 | `sw_direct_wet_stress_override_enabled` | mode | 1 | True | 32453 | 1987200 | 1 |
| 40 | `sw_dwell_gate_enabled` | mode | 1 | True | 32453 | 1987200 | 1 |
| 41 | `sw_fog_closes_vent` | mode | 1 | True | 31766 | 1987200 | 1 |
| 42 | `sw_mister_closes_vent` | mode | 0 | False | 31766 | 1987200 | 1 |
| 43 | `sw_summer_vent_enabled` | mode | 1 | True | 32453 | 1987200 | 1 |
| 44 | `temp_hysteresis` | median | 1.69 | 1.7 | 31766 | 1987200 | 11 |
| 45 | `vent_exchange_fraction` | median | 0.3 | 0.3 | 32453 | 1987200 | 2 |
| 46 | `vent_prefer_dp_delta_f` | median | 3 | 3.0 | 32453 | 1987200 | 4 |
| 47 | `vent_prefer_temp_delta_f` | median | 4 | 4.0 | 32453 | 1987200 | 5 |
| 48 | `vpd_hysteresis` | median | 0.1925 | 0.2 | 32453 | 1987200 | 15 |
| 49 | `vpd_watch_dwell_s` | median | 56 | 56.0 | 32453 | 1987200 | 11 |

Notes: `mister_vpd_weight` shows raw 1.7 → quantized 1.5 because its wire
grid is 0.5 (round-half-even 3.4 → 3). Several medians sit off the ESPHome
entity step (e.g. `min_fog_on_s` 59 s) — the wire grid is intentionally at
least as fine as the entity step, and these are the device-confirmed values.

## Retired field (formerly unqualified)

- **`direct_wet_stress_latest_hour`** (former wire_id 6, now permanently in
  `RETIRED_WIRE_IDS`): the v1 reserved no-firmware-consumer field (#582) —
  no ESPHome entity, global, or `cfg_*` readback ever existed, so it could
  never alter an effective component (audit §6.4) and its zero device
  readbacks blocked §8.2 qualification. The #588 decision retired it from the
  wire schema (v2), which resolves the qualification blocker without any
  silent value substitution.

## AI template candidates

Both templates differ from the baseline only inside the §8.2 11-field
allowlist; the other 37 wire fields are byte-identical to the approved
baseline. Values are grounded in the §5 effective-readback posture table
(epoch means over 47,956 sampled minutes) and the §5 forecast-response
correlations; every value is on the wire grid and inside both the registry
planner bounds and the firmware clamp bounds (asserted at build time and in
tests).

- **moderate** — the observed epoch-mean hot/dry posture on the wire grid:
  stage-2 offset 0.8 °F, all-fans-at-high ON, fog escalation 0.3 kPa,
  mister engage 1.2 kPa, mister all 1.35 kPa, all-zone delay 90 s, pulse gap
  38 s, pulse on 60 s, water budget 220 gal, fog min on/off 60/60 s.
- **aggressive** — one bounded step further in the responsive direction:
  stage-2 offset 0.5 °F, all-fans-at-high ON, fog escalation 0.2 kPa,
  mister engage 1.0 kPa, mister all 1.2 kPa, all-zone delay 60 s (clamp
  floor the epoch already approached), pulse gap 30 s, pulse on 75 s,
  water budget 250 gal, fog min on 90 s / min off 30 s.

Per-field evidence and rationale strings are in the artifact. The once-daily
selector may choose only `baseline`, `moderate` or `aggressive`; it may not
synthesize intermediates or switch intraday. Every boundary interposes baseline,
so direct moderate↔aggressive activation is forbidden.

## Approval chain (what must happen before any of this actuates)

1. ~~Resolve the unqualified field~~ — DONE by the #588 retirement (wire
   schema v2); the canonical vector bytes and `content_sha256` (fixed policy
   revision ids `{"registry_rev": "wire-v2-retire-wire-id-6", "schema_rev":
   "efa85343"}`) are now emitted for baseline + both templates.
2. Regenerate baseline/templates on the **actual deployed setter grid**. Prove
   all 48 canonical setter/readback routes plus treatment-prefix and separately
   ordered full-baseline-recovery replay/HIL (including compiled defaults).
3. Horticultural, firmware, controls, water/fertigation and facility owners
   approve the exact profiles and supervised canaries
   (**gate:jason** sign-off).
4. Run baseline↔moderate/aggressive canaries and the fast-path 48-hour A/A
   rehearsal without inspecting comparative outcomes.
5. Lock the
   approved baseline and templates into the current fast-path protocol instance
   (`protocols/planner-switchback-v2.template.yaml`) and its database rows. The
   confirmed-component executor may resolve only those exact artifacts; it does
   not use the historical v1 manifest/vector arbiter.

## Reproducing

```sh
# 1. read-only extraction (raw histogram; do not commit)
VERDIFY_DB_BACKEND=kube baseline/extract-baseline.sh /path/to/outdir

# 2. rebuild the artifacts (fresh extraction)
uv run python baseline/baseline.py build \
    --input /path/to/outdir/baseline_intervals.csv \
    --out baseline/frozen-fsm-baseline-candidate-2026-08-14.json
uv run python baseline/baseline.py build-templates \
    --baseline baseline/frozen-fsm-baseline-candidate-2026-08-14.json \
    --out baseline/ai-template-candidates-2026-08-14.json

# 2b. or requantize the committed artifact after a wire-schema change (no DB)
uv run python baseline/baseline.py requantize \
    --source baseline/frozen-fsm-baseline-candidate-2026-08-14.json \
    --out baseline/frozen-fsm-baseline-candidate-2026-08-14.json

# 3. verify
uv run pytest tests
```
