# Planner efficacy audit

This directory contains the reproducible, read-only **core PID analysis** behind
the 2026-08-14 planner-efficacy report. Raw production telemetry is deliberately
not committed. The checked-in result manifest records input hashes, row counts,
study windows, model validation, support diagnostics, and aggregate outcomes.
The report's broader architecture, delivery-lineage, and inventory audit uses
additional source inspection and read-only aggregate database queries that are
not all reproduced by this package.

The aggregate output from the executed audit is checked in as
[`results-2026-08-14.json`](results-2026-08-14.json). The study distinguishes
three questions:

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
