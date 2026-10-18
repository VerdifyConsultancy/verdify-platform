# P2 — fog/vent input admission

This source packet extends the existing prefix preparation and coverage report.
It does not reconstruct historical controller state. The compiled prefix path
remains **27/48 UNPROVEN**, with 21 unimposed fields. Selecting fog/vent for P1
does not change that count. The other 20 fields now have individual source-read
locations, required inputs/state and explicit missing or unknown evidence in
[source-coverage.json](source-coverage.json).

The report uses P1's actual `extract_fragments()` output, including the selected
policy lambdas, relay metadata/helpers, physical vent readback, exemption helper,
safety suppression and vent/fog application. Candidate inputs must match both
the extracted fragment hashes and the complete extracted source/include closure.
Missing or ambiguous extraction refuses admission. The P1 extractor is supplied
by the parallel P1 commit; this P2 commit does not own or change it.

## What the input check establishes

`fog_vent_input_admission()` in the existing preparation script checks a bounded,
explicit **synthetic consumer fixture**. It requires both snapshot and legacy
policy stores; exact booleans for every request/force/safety input; finite,
bounded, wire-quantized dwell values; fog/vent relay state and persistent
timestamps/runtime; and matching boot, tick, monotonic time and causal stage.
No missing input receives a default.

The three boundaries are distinct:

- Policy selection occurs at the tick's snapshot/legacy selection.
- `mode`, `relay_out.fog`, `willFog` and safety/force decisions are explicit
  upstream inputs before the extracted fog suppression. The fixture does not
  prove how the upstream controller derived them.
- Actual relay storage and timers are read before relay application, after any
  relevant tick reset. A genuine pre-tick capture would also need the authentic
  reset transformation; this packet does not invent it. `willVent` is a desired
  output and cannot substitute for `vent_rly.state`.

The check rejects same-row or previous-row `eq_vent`/`eq_fog` output origins,
stale/future fixture samples, mixed boot/tick identity, missing/nonfinite state,
off-quantum durations, permissive defaults and mismatched source bindings. Equal
fixture sample/tick time is a synthetic coherence check, **not a new production
freshness threshold**. Hashes and provenance labels alone cannot authenticate
historical measurements.

Even a complete fixture only receives `scenario_inputs_admissible: true`.
`historical_prefix_admitted` remains false for every input: no supported authentic
capture/reconstruction producer, counterfactual state propagation or complete
prefix recovery/negative coverage exists. No `ORDER_REVISION`, physical permission
or added coverage credit can be emitted by this check. Positive test fixtures
remain in tests and are not published as observed telemetry.

## Remaining consumers and current inputs

The report classifies eight relay dwell fields, nine downstream mister fields,
the pre-Setpoints night VPD bias, the mister/vent switch and the direct-wet switch.
Each names its actual scanned read locations and distinct required state.
Persistent relay/zone histories, timers, reset chronology and the corresponding
upstream validity/authority are unavailable as admitted historical inputs.
The night-band input must be unbiased; feeding the existing post-bias
`sp_vpd_low/high` back through the bias would double-count it.

The current `sw_direct_wet_gate_enabled` read selects a diagnostic reason inside
the blocked-mister branch. Its actuator influence has not been demonstrated;
counting the source name as an exercised safety consumer would be misleading.
These descriptions are a source-bound requirements review, not a claim that the
full upstream dependency closure has already been extracted or captured.

## Verification and handoff

[verification.json](verification.json) records the exact source hashes, commands,
exit codes and retained output hashes. The focused checks are pure Python input,
source and existing prefix/preparation tests; no compiler, corpus replay, DB,
credential, device, message or native operation ran for P2. The one-line gzip
header inspection recorded there establishes only that `eq_vent`/`eq_fog` output
columns exist, not their suitability as counterfactual state.

Root should compose the P1 source first, then this P2 commit, and run:

```sh
python -m pytest -q tests/test_component_prefix_inputs.py tests/test_component_prefix_replay.py tests/test_prepare_component_prefix_replay.py
python scripts/component_prefix_replay.py coverage-source
```

`coverage-source --fog-vent-inputs PATH` and the preparation tool's corresponding
option add the same admission report. A successful report command means the
report was produced, not that a historical prefix was admitted. The ordinary
external HIL protocol and existing qualification predicates are unchanged.

P1 compilation/causal scenarios remain Root's separately admitted native work.
P2 does not require a second replay or a new runner. A future historical input
producer must provide authentic upstream and persistent state with causal
ordering before prefix integration can expand; adding field assignments or
copying output columns is insufficient. #639 and #587 acceptance, dependencies,
physical guards and the live grid/order remain unchanged.
