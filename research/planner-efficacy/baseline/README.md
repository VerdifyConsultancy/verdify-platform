# Frozen-FSM baseline candidate + AI template candidates (Lane G, #588)

Status: **CANDIDATE — pending horticultural/firmware/safety approval
(gate:jason)**. Nothing in this directory is an executable treatment
definition; per audit §8.2 the baseline becomes usable only after the full
approval chain below.

Artifacts (committed, derived; raw extraction CSVs stay outside Git):

- `frozen-fsm-baseline-candidate-2026-08-14.json` — the per-field baseline
  candidate derived from stable same-firmware effective readbacks.
- `ai-template-candidates-2026-08-14.json` — the two reviewed 49-field AI
  template candidates (moderate / aggressive hot-dry response) whose
  differences from baseline are confined to the §8.2 11-field allowlist.
- `extract-baseline.sh` + `baseline.py` — read-only extraction and
  deterministic artifact builders (the SQL lives in `baseline.py` and its
  SHA-256 is recorded in the artifact).

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
  wire schema v1, manifest digest recorded in the artifact).
- **Unqualified policy**: a field with no qualified readback in the window is
  listed as UNQUALIFIED and **blocks baseline approval** — no boot default is
  silently substituted, and the canonical 49-field vector bytes/content hash
  are omitted until every field qualifies.

Extraction provenance: SQL SHA-256
`72a83c13028a11b5fb0d0b1aed08e00b37dccdfd9bd8ce169e20ad65c369354c`,
input histogram CSV SHA-256
`bfabce68f393d990d13b4b9097076f910a49c65b5a18afc6a778cbdad065064a`
(501 data rows). Both are embedded in the artifact and re-checked by
`tests/test_baseline.py`.

## Per-field coverage

48 of 49 wire fields have complete readback coverage (every present field
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
| 6 | `direct_wet_stress_latest_hour` | — | — | **UNQUALIFIED** | 0 | 0 | 0 |
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

## Unqualified fields

- **`direct_wet_stress_latest_hour`** (wire_id 6): reserved
  no-firmware-consumer field (#582) — no ESPHome entity or `cfg_*` readback
  exists, so the window contains no qualified readback. Per §8.2 this
  **blocks baseline approval**; the canonical 49-field vector bytes and
  `content_sha256` are deliberately omitted from both artifacts. Before
  approval the owners must assign its frozen value explicitly (an approved
  reviewed constant recorded in the protocol instance — e.g. the registry
  default 22 — via a reviewed edit, never a silent substitution by this
  pipeline).

## AI template candidates

Both templates differ from the baseline only inside the §8.2 11-field
allowlist; the other 38 wire fields are byte-identical to the approved
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

Per-field evidence and rationale strings are in the artifact. The planner may
only select and switch between the two complete reviewed templates (§8.2); it
may not synthesize intermediates.

## Approval chain (what must happen before any of this actuates)

1. Resolve the unqualified field (`direct_wet_stress_latest_hour`) with a
   reviewed explicit value.
2. Regenerate the artifacts so the canonical vector bytes and
   `content_sha256` (fixed policy revision ids
   `{"registry_rev": "wire-v1-initial", "schema_rev": "efa85343"}`) are
   emitted for baseline + both templates.
3. **Compiled replay** of the complete baseline vector through the current
   firmware build, then **HIL**, then **A/A** (§8.2/§8.6) — constructed and
   reviewed without inspecting trial outcomes.
4. Horticultural, firmware, and safety owners approve the complete vectors
   (**gate:jason** sign-off).
5. The approved content hashes are locked into the protocol instance
   (`protocols/planner-switchback-v1.template.yaml` successor) and the
   `policy_templates` rows; only those exact hashes are resolvable to bytes
   by the arbiter.

## Reproducing

```sh
# 1. read-only extraction (raw histogram; do not commit)
VERDIFY_DB_BACKEND=kube baseline/extract-baseline.sh /path/to/outdir

# 2. rebuild the artifacts
uv run python baseline/baseline.py build \
    --input /path/to/outdir/baseline_intervals.csv \
    --out baseline/frozen-fsm-baseline-candidate-2026-08-14.json
uv run python baseline/baseline.py build-templates \
    --baseline baseline/frozen-fsm-baseline-candidate-2026-08-14.json \
    --out baseline/ai-template-candidates-2026-08-14.json

# 3. verify
uv run pytest tests
```
