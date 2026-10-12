# Scientific/readiness execution — 2026-10-12

This is the first bounded execution packet for [#782](https://github.com/VerdifyConsultancy/verdify-platform/issues/782), [#639](https://github.com/VerdifyConsultancy/verdify-platform/issues/639) and [#587](https://github.com/VerdifyConsultancy/verdify-platform/issues/587), based on `b5bdb3c65b243c230b7864fa81b95f926b2e68ff`. It contributes source evidence to [#783](https://github.com/VerdifyConsultancy/verdify-platform/issues/783). It does not close these issues or qualify a deployment, physical experiment or integrated restored-data execution.

**Completed:** 621 existing focused tests passed; all three immutable historical input hashes and eight current warm-analysis contract hashes verified; fixed-30 numerical scenarios reproduced twice each; current native harness compiled and its actual coverage ceiling reproduced. Follow-up native execution found and fixed an offline evidence reproducibility defect. The calculators, receipts and digest fix change no runtime, protocol, release pin or deployment.

## #782: authentic input eligibility and fixed-30 analysis

`reproduce.py` invokes the existing immutable-input calculator and existing `simulate_joint_power` with exactly 30 pairs. It never invokes the sample-size chooser. `fixed30-eligibility.json` records source/input hashes, assumptions, environment and unavailable inputs. `historical-climate-recomputed.json` contains only derived aggregates and hashes; the private raw inputs remain outside Git.

The unchanged candidate is **June 1–July 30, 2027**, America/Denver, 60 local days/30 adjacent pairs, `[06:00,24:00)` (18 hours/64,800 seconds). The separate November 2–December 31, 2026 winter observer remains passive. Neither is a design lock. The accepted exploratory pilot has no 80% power launch gate.

| Recomputed historical input | Result | Permitted interpretation |
|---|---:|---|
| June–July 2026 climate analogue | 60/60 eligible days; 30/30 adjacent pairs; 4,309/4,320 eligible bins | Authentic retained route-level climate coverage only |
| November–December 2025 analogue | 0/60 eligible days; 0/30 adjacent pairs | Does not support the randomized warm candidate |
| Actual as-of forecasts, selector choices, equipment receipts | None retained in these fixed inputs | Unavailable; zero choices does not mean zero fallback |
| Crop-target/probe-contributor authentication and actual 18-hour paired endpoint covariance | Unavailable | No empirical effect, joint power or physical missing-endpoint bound inferred |

The old provisional model uses 22-hour paired scales, assumed effects/correlation/selector mix and assumed independent completeness. It is not an estimate from the current 18-hour treatment endpoints. Replaying 25,000 simulations per scenario with seed 588640 produced:

| Assumed pair completeness | All 30 retained | Provisional simulated joint advance probability |
|---|---:|---:|
| 0.9995 | 0.985108 | 0.13776 |
| 0.99 | 0.739700 | 0.10200 |
| 0.95 | 0.214639 | 0.03004 |

The first scenario exactly reproduces the retained reference calculation. Each scenario reproduced byte-identical canonical results twice. `retention_only_reweighting` separately retains the older conditional-power reweighting; its small difference from the new simulations is expected Monte Carlo variation. These calculations do not create a blinded schedule, draw an assignment or establish readiness. The operator Python/NumPy/SciPy versions are recorded; a frozen analyzer image digest remains null.

## #639/#587: independent source qualification

| Existing suite | Passed | Evidence scope |
|---|---:|---|
| Calendar, pre-draw sensitivity, design, selector and day export | 124 | Research contract and synthetic/golden cases |
| Component contract, executor, writer, observability, prefix logic, lease and device gate | 314 | Fake-store/transport faults, generation-bound epochs, exclusive bundles, recovery, rescue, blinded operations and deny rules |
| Component API/store/source and readiness guard | 183 | Mocked function-only duties, typed transitions, optimistic conflicts, fail-closed readiness |

The three JUnit files preserve exact executed test cases. `qualification-summary.json` binds their commands and source hashes. The processes used `VERDIFY_DEVICE_WRITE_ENABLED=0` and `KUBECONFIG=/dev/null`; the selected tests use local mocks/fixtures. This is not a real PostgreSQL login/grant witness or a network-isolated restored-target run.

The actual C++ probe compiled against the current source and a bounded 128-row corpus, then exited **1 as expected**: **27/48 policy fields injectable**, with 21 uncovered fields including seven treatment fields. `native-harness-coverage.log` and `.json` preserve the exact missing list and source/binary/corpus hashes. The probe executed zero prefix safety runs and yielded no qualified `ORDER_REVISION`. Full-coverage Python replay tests explicitly use test doubles; their passing arithmetic does not remove this native ceiling.

## #639: concrete offline reproducibility fix

The follow-up executed all **32 committed activation/rollback prefixes** through the actual compiled core against the full **296,698-row corpus**. The 27 distinct complete policy states required 27 native runs. No covered-core invariant breach occurred; fixed order, grid/clamp validity, idempotency and exact target landing passed. Every prefix remains **unproven** because coverage is 27/48, and no qualified order revision is emitted. This does not supply candidate compiled-default/current-state recovery or external ESPHome consumer evidence.

The replay tool previously hashed raw stdout containing a random temporary `policy_state` filename. Two actual 128-row runs with identical binary, corpus and policy bytes produced different evidence hashes solely because their workdirs differed. The new source fix normalizes only known policy/corpus input paths for the digest and explicitly binds policy, corpus, binary/source bytes and exit status in a versioned domain. Raw native stdout/stderr remain intact and are retained in this packet. Existing partial-coverage and breach decisions are unchanged; old partial native evidence hashes must be regenerated under the new digest domain.

The meaningful new regression failed before the fix, then passed. It proves workdir equivalence including a quoted Unicode/backslash path, changed-policy/corpus hash sensitivity, preservation of raw diagnostic paths and fail-closed unsafe handling. The updated prefix suite passed **46 tests** and the preparation/proof-consumer suites passed **304 tests**. These are additional runs alongside the baseline qualification, with overlap explicitly retained rather than counted as new test coverage.

After the fix, **two independent full native replays** produced byte-identical canonical source-witness evidence, SHA-256 `9b567b7e128b58a780dc4201fe26a35e1445ded8aa18e63c8aad8f92e79ebb7f`. `native-prefix-source-replay.json` and `native-prefix-source-replay-repeat.json` retain all raw native output plus exact per-prefix evidence and source dependencies. The temporary paths intentionally differ in the raw diagnostics and are excluded only from semantic evidence identity. `native-evidence-path-reproduction.json` records the before/after comparison; `source-fix-manifest.json` binds the changed tool, regression, calculator, commands and results. The original `source-manifest.json` continues to describe the first baseline test run. Independent read-only review of source commit `1ac661fbd9a86ddca31c8ee39020e8adc896471f` found no actionable issues, reran the regression and recomputed every native evidence hash from the retained raw output. `independent-review.json` records its bounded scope and checks.

## Next useful work and remaining decisions

1. **#782:** authenticate the actual north/east/west probe contributors, calibration/provenance and crop-target revision; then use authentic prospective as-of forecasts/once-daily selector/equipment receipts to estimate the actual 18-hour endpoints, missingness and carryover where support exists. Historical route data cannot manufacture those physical facts. Keep missing empirical quantities null and the accepted exploratory claim bounded to climate noninferiority plus nine-state control burden.
2. **#639:** qualify the missing 21 native injection fields, or supply an authentic full-48 external interlock witness; then execute all activation/rollback/recovery prefixes with exact candidate compiled defaults and current state. The current replay tool deliberately fails closed rather than issuing a certificate from partial coverage. Separately reuse the release lane's admitted restored target for real executor/role/recovery witnesses.
3. **#587:** reuse the same admitted target for authenticated off/locked/armed/hold/normal-disable/emergency-safe SQL transitions. Coordinate actual alert fire/clear with monitoring-stack #804/#807. Passing typed source tests do not establish actual alert delivery.
4. **#783:** reuse the current S2 restore/role/spool target and existing restored-interface/export contracts for the real selector → generated setter → raw receipt → freezer → export → analyzer chain, twice with canonical byte equality. Existing retained SQL fixtures contain synthetic assignments and seeded delivery evidence; do not count them as an integrated current-data or physical pilot witness.

The release/device lane owns production delivery and attended proof. This packet performed no production SQL, device/provider call, protocol lock, schedule draw, firmware flash, deployment or global issue-comment publication.

## Reproduce

Run from the repository root with the existing Python environment and the three explicitly documented private inputs from `docs/research/c2-pre-draw-sensitivity-2026-09-28.md`:

```sh
VERDIFY_DEVICE_WRITE_ENABLED=0 KUBECONFIG=/dev/null \
python planning/execution/2026-10-12/scientific-readiness/reproduce.py \
  --input-dir "$PRESERVED_INPUT_DIR" --output-dir "$REPLAY_OUTPUT"
```

The exact selected pytest invocations and native probe command are recorded in `qualification-summary.json`. A tampered private-input negative check must fail before analysis; its result is included there. `SHA256SUMS` binds the packet files; `source-manifest.json` binds the reused implementation/test sources. A new Python/compiler environment may produce different numerical/binary bytes and must be recorded honestly.

The native source witness uses only committed profiles/corpus and a local compiler:

```sh
VERDIFY_DEVICE_WRITE_ENABLED=0 KUBECONFIG=/dev/null \
python planning/execution/2026-10-12/scientific-readiness/reproduce_native.py \
  --output "$REPLAY_OUTPUT/native-prefix-source-replay.json"
```

Run it again in a fresh process with a separate output file. Compare canonical JSON with `raw_native_runs` excluded; retain those raw arrays for diagnosis. Successful reproduction remains an unproven partial native witness, with `all_pass=false` and `qualified_order_revision=null`.
