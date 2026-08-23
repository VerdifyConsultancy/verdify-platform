# Planner efficacy audit

This directory contains the reproducible, read-only **core PID analysis** behind
the 2026-08-14 planner-efficacy report. Raw production telemetry is deliberately
not committed. The checked-in result manifest records input hashes, row counts,
study windows, model validation, support diagnostics, and aggregate outcomes.
The report's broader architecture, delivery-lineage, and inventory audit uses
additional source inspection and read-only aggregate database queries that are
not all reproduced by this package.

The first-pass aggregate output is checked in as
[`results-2026-08-14.json`](results-2026-08-14.json). The firmware-bounded
second pass is documented in
[`planner-efficacy-current-firmware-2026-08-14.md`](../../docs/research/planner-efficacy-current-firmware-2026-08-14.md)
and has two executed manifests:

- [`results-current-firmware-core-2026-08-14.json`](results-current-firmware-core-2026-08-14.json)
  for the bounded PID/model rerun; and
- [`results-current-firmware-supplement-2026-08-14.json`](results-current-firmware-supplement-2026-08-14.json)
  for the stale-policy match, forecast response, waypoint survival, and
  effective-tunable audit, plus the reproducible 30-day switchback screening
  calculation used to size the proposed next phase.

The study distinguishes three questions:

1. What actually happened while plans governed the greenhouse?
2. What could an adequately validated plant model estimate for an explicitly
   specified deterministic fixed-setpoint PID study policy under the same
   weather?
3. What is specifically attributable to AI rather than the deterministic
   firmware, delivery system, target curves, or simultaneous hardware and
   firmware changes?

Question 2 is an engineering counterfactual, not a randomized causal result.
Question 3 cannot be identified from historical telemetry alone; the report
therefore includes a prospective randomized switchback design.

## Reproduce

### Protocol-v2 source-only contracts

Regenerate the additive protocol-v2 profile, provisional joint-power, schedule
golden, and analyzer golden artifacts without a database/provider/device call:

```bash
PYTHONPATH=research/planner-efficacy:. \
  uv run --project research/planner-efficacy \
  research/planner-efficacy/generate_v2_artifacts.py
```

The generated power file is a transparent planning scenario, not a frozen
trial design or efficacy result. Its missing provider replay and exact
six-hour raw-data refresh are recorded in the artifact itself.

Extract the four input files through the repository's read-only database
helper. Keep the output outside Git because it contains operational telemetry:

```bash
VERDIFY_DB_BACKEND=kube \
  research/planner-efficacy/extract.sh /tmp/planner-efficacy-inputs
```

The extractor uses separate read-only transactions for its four bounded
exports. The hashes record the exact executed inputs, but this is not a single
database-wide MVCC snapshot. Journal lifecycle, validation, and score fields
are snapshot-mutable; the outcome cutoff and extraction time are therefore
reported separately.

Then run:

```bash
uv run --project research/planner-efficacy \
  research/planner-efficacy/audit.py \
  --climate /tmp/planner-efficacy-inputs/climate_15m.csv \
  --equipment /tmp/planner-efficacy-inputs/equipment_transitions.csv \
  --daily /tmp/planner-efficacy-inputs/daily_outcomes.csv \
  --plans /tmp/planner-efficacy-inputs/plans.csv \
  --output /tmp/planner-efficacy-results.json
```

The primary model is trained only on the open-window calibration period and is
evaluated on complete days after the 2026-07-10 stable-firmware boundary.
Every simulated day starts from its observed initial state; future observed
indoor state is never fed to the PID. Models are rejected when held-out
multi-step accuracy or historical state-action support fails the declared
gates.

Run the focused validation with:

```bash
uv run --project research/planner-efficacy \
  pytest research/planner-efficacy/tests
```

## Reproduce the current-firmware second pass

The wrapper fixes the physical firmware epoch and complete-day outcome cutoff,
then adds the read-only inputs required by the mechanism audit:

```bash
VERDIFY_DB_BACKEND=kube \
  research/planner-efficacy/extract-current-firmware.sh \
  /tmp/planner-current-fw-inputs
```

Run the core analysis with the exact arguments recorded in the report:

```bash
uv run --project research/planner-efficacy \
  research/planner-efficacy/audit.py \
  --climate /tmp/planner-current-fw-inputs/climate_15m.csv \
  --equipment /tmp/planner-current-fw-inputs/equipment_transitions.csv \
  --daily /tmp/planner-current-fw-inputs/daily_outcomes.csv \
  --plans /tmp/planner-current-fw-inputs/plans.csv \
  --output /tmp/planner-current-fw-core.json \
  --train-start 2026-07-11T06:00:00+00:00 \
  --train-end 2026-07-31T06:00:00+00:00 \
  --eval-start 2026-07-31T06:00:00+00:00 \
  --eval-end 2026-08-14T06:00:00+00:00 \
  --factual-start 2026-07-11 \
  --factual-end 2026-08-14 \
  --plan-start 2026-07-10T21:03:12.991915+00:00 \
  --plan-end 2026-08-14T06:00:00+00:00 \
  --firmware-version 2026.7.10.1500.09ee886 \
  --firmware-epoch-start 2026-07-10T21:03:12.991915+00:00 \
  --era-label 'exact live firmware 2026.7.10.1500.09ee886; complete local days only' \
  --skip-historical-match
```

Run the supplemental analysis:

```bash
uv run --project research/planner-efficacy \
  research/planner-efficacy/epoch_analysis.py \
  --climate /tmp/planner-current-fw-inputs/climate_15m.csv \
  --equipment /tmp/planner-current-fw-inputs/equipment_transitions.csv \
  --daily /tmp/planner-current-fw-inputs/daily_outcomes.csv \
  --forecast-response /tmp/planner-current-fw-inputs/forecast_response.csv \
  --waypoints /tmp/planner-current-fw-inputs/waypoints.csv \
  --forecast-vpd-accuracy /tmp/planner-current-fw-inputs/forecast_vpd_accuracy.csv \
  --effective-tunables /tmp/planner-current-fw-inputs/effective_tunables.csv \
  --trigger-outcomes /tmp/planner-current-fw-inputs/trigger_outcomes.csv \
  --output /tmp/planner-current-fw-supplement.json
```

The stale-policy comparison is intentionally labeled hypothesis-generating.
It asserts the fixed sample counts, matching reuse, and raw-feature coverage and
does not emit a minute-level significance test or causal savings estimate.
The switchback sizing is also explicitly a screening approximation: it uses
adjacent-day variability in the all-AI exact-firmware history and cannot stand
in for the prospective paired randomization analysis.
