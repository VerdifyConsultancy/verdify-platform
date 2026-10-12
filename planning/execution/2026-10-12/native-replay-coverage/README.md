# Native relay dwell source fix — #587 / #639

The historical replay's 27/48 ceiling exposed a real firmware consumer defect:
`RelayMeta R` initialized all eight relay dwell limits once, although `pol()`
read a fresh snapshot or legacy tunable every tick. A subsequent limit change
could read back correctly while the relay still obeyed its first-tick limit.

The fix refreshes only `min_on_ms` and `min_off_ms` for the two heat relays, two
fan relays, fog and vent. It preserves the static relay history, runtime totals,
force overrides and wrap-safe elapsed arithmetic. Increasing or decreasing a
limit during a cycle measures the new limit from the original ON/OFF edge; it
does not restart the cycle or bypass its remaining dwell.

For example, an ON relay begins with a 120-second minimum. Its policy changes
to 240 seconds after the initial tick. The previous source incorrectly turns
it OFF at 120 seconds; the corrected source holds it through 239.999 seconds
and permits OFF at 240 seconds. The inverse 120-to-60 change permits the edge
at 60 seconds, measured from the original transition. OFF dwell behaves the
same way, including across the uint32 millisecond rollover.

## Verified source and limits

- Base: `64586987b76f7e0fba7982d582b06184ff9966bf`. This branch is separate
  from the frozen PR #973 runtime cohort and contains no deployment change.
- Reported deployed firmware: source
  `620a218a93cf24ca1e1b49dfed617cc97de7d258`, version
  `2026.10.2.0637.620a218a-wifi-bound`. Its `controls.yaml` is byte-identical
  to the pre-fix source (SHA-256
  `db066c4708bf33ac11014ae1d006a37d26098bbf6e17720312db3f7c36774ac3`).
  This establishes source applicability; no live or physical reproduction
  or firmware upload was performed.
- Corrected controls SHA-256:
  `3f37614eff7e10fc52193d6bc2d3619d268feecb6dde1eceb2ffc9dda9ed6f50`.
- The focused test compiles the actual policy-selection lambda, duration
  constants, static relay initialization, refresh and relay helper lambdas
  directly from YAML. Only snapshot/global storage and Switch state are
  narrow adapters. The test does not duplicate the relay algorithm, exercise
  the policy journal/transport, or qualify physical hardware.

## Evidence

| Check | Result |
|---|---|
| Original 42-case native reproduction | 36 fail, 6 force-override cases pass |
| Final 84-case regression against unchanged base source | 72 fail, 12 force-override cases pass |
| Final regression against corrected source | 84 pass, no errors/skips |
| Related offline relay/prefix/preparation regressions | 138 pass, no errors/skips |
| `make test-firmware` | Pass: 273 vector, 251 engine, 30 recovery, 14 injection, 288 greenhouse checks; historical replay passes |
| `make firmware-invariants` | 296,698 rows, zero invariant violations |
| `make firmware-replay-worktree OLD=64586987...` | 296,698 rows, zero decision/diagnostic differences, threshold 0 |
| `make firmware-check` | ESPHome 2026.6.5 ESP32 compile succeeds; no upload |
| Consumer manifest and Ruff | Pass; manifest changes are 55 line offsets of +11 |
| Independent read-only review | No actionable findings; independently reran all 84 native cases |

`readiness.json` records source, corpus and check provenance. Raw native
receipts, JUnit results, extracted native C++ and fragment hashes are retained
beside it. JUnit XML and extracted C++ use lossless gzip archives to preserve
their original bytes through Git text-normalization hooks. ESPHome output was
redacted in memory before retention; its build
receipt records artifact hashes. Compiled firmware binaries remain local and
are neither a release nor an accepted rollback artifact.

The zero-difference historical replay is expected: `replay_emit` does not
execute the changed outer relay wrapper. The actual-source native boundary
tests establish the behavioral change. Neither result expands historical
treatment qualification.

## Historical qualification remains UNPROVEN

A fresh compiled invariant run imposes the complete ROM policy state over all
296,698 immutable corpus rows. It still genuinely consumes **27/48** fields,
with zero invariant violations and `full_coverage=false`. `native-coverage.json`
retains the exact compiled run declaration. No `ORDER_REVISION` is qualified;
the prior 32 activation/rollback prefixes remain UNPROVEN.

The 21 absent consumers are eight relay dwell fields, nine downstream mister
fields, the pre-band night VPD bias and three outer-control switches. The
eight relay fields now have actual-source boundary evidence, but they have
not been added to the historical prefix harness or counted as historical
coverage. Missing downstream consumers are:

- `mister_all_kpa`, `mister_center_penalty`, `mister_engage_delay_s`,
  `mister_engage_kpa`, `mister_min_off_s`, `mister_pulse_gap_s`,
  `mister_pulse_on_s`, `mister_vpd_weight`, `mister_water_budget_gal`;
- `night_vpd_bias_kpa`, `sw_direct_wet_gate_enabled`,
  `sw_fog_closes_vent`, `sw_mister_closes_vent`.

The corpus's 91 columns omit the required zone temperature/VPD/bands,
pre-bias solar-band anchors, measured flow/water history, actual GPIO OFF
edges, boot/clock/feed and irrigation state. It samples historical rows rather
than the real one-second mister control ticks. Filling these values with
defaults or treating the replay's synthesized mister outputs as actual GPIO
observations would not establish coverage. Reapplying night bias to captured
post-bias bands would double-count it.

The current actual source also matters: south/west humidity zones are disabled;
the later S2 pulse path can weight the center despite an older nearby comment;
and `sw_direct_wet_gate_enabled` currently changes the diagnostic reason,
not the admission condition. No different control behavior was invented here.

The next useful bounded source work is to compile and observe the actual
downstream mister fragments at native one-second cadence with explicit input
provenance and safety-denial edges. Such scenario evidence must remain
separate from historical full-field qualification until authentic missing
state and observed edges exist. This is a data/control-path limitation,
not a reason to change coverage labels or qualification thresholds.

## Reproduce the source checks

From this branch, use the existing repo tooling environment:

```sh
env PYTHONPATH=. VERDIFY_DEVICE_WRITE_ENABLED=0 KUBECONFIG=/dev/null \
  /Users/jason/repos/verdify-platform/.venv/bin/python -m pytest \
  tests/test_policy_relay_dwell_refresh.py tests/test_irrigation_stop_edges.py \
  tests/test_component_prefix_replay.py tests/test_prepare_component_prefix_replay.py -q
make test-firmware
make firmware-invariants
THRESHOLD_PCT=0 BUILD_DIR=/private/tmp/verdify-relay-source-check \
  make firmware-replay-worktree OLD=64586987b76f7e0fba7982d582b06184ff9966bf
ESPHOME_BIN=/path/to/esphome-2026.6.5 SECRETS_SRC=/existing/private/secrets.yaml \
  make firmware-check
```

These commands compile and replay only. Runtime integration, firmware release
and any attended OTA remain with the designated release owner.
