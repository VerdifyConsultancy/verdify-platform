# P1: actual fog/vent consumer

**Native named-consumer qualification PASSED.** Three compiles,56 positive completions and8 completed causal negatives passed at `e844a40976068e93b52420e2d536eb230078ec5c`; all74 process records, complete output hashes and terminal container states were independently verified. [Full custody](../p1-native-custody/verified/verification.json). This packet implements the first S3B source qualification under #639. It changes no production firmware, policy value, grid/order identity, device state or shared pipeline. The compiled prefix remains **27/48 UNPROVEN**; this scenario matrix adds no row-wise coverage credit.

`scripts/fog_vent_consumer.py` extracts eleven uniquely identified fragments from the actual controls and helper source. It refuses missing/ambiguous anchors and reordered consumer/application stages. `tests/fixtures/fog_vent_consumer.cpp.in` adapts snapshot/legacy storage, switch state and monotonic time. It includes the actual `greenhouse_logic.h` helper and quoted header closure; it copies the real selector, dwell constants, relay metadata/helpers, vent readback, exemption call, safety suppression, fog consumer, and vent/fog application verbatim. It does not implement a second fog policy model.

The source manifest retains whole-file and fragment hashes, source lines, text, the seven-file include closure and three generated C++ hashes. The native driver regenerates these from the exact admitted checkout and retains the exact source inputs before compilation. The snapshot fixture is a storage adapter; it does not exercise policy-engine admission or derive upstream controller/safety decisions. Relay metadata persists across ticks within each case, and each case gets a fresh process. The supplied boundary is after upstream decisions and any tick reset, before downstream relay application.

## Native matrix and causal witnesses

Each of 28 scenarios runs in both snapshot and legacy selection modes (**56 expected positive completions**). The unselected fog switch is opposite and the unselected eight dwell values are 120 seconds, while the selected values are 60 seconds and checked against the actual wire contract.

- Eight explicit selected-policy/actual-vent/desired-vent combinations. Actual open plus desired closed still suppresses fog when selected policy is true; actual closed plus desired open allows the existing fog request on this tick. The real vent application then changes the switch adapter state.
- Explicit VENTILATE and SAFETY_COOL controller-fog exemptions, a manual-only request without controller authority, and an unrelated mode.
- Four already-blocked request combinations, including exemption present. The consumer cannot create a fog request.
- Minimum-on cutoff at 59,999/60,000 ms, including uint32 wrap; minimum-off and explicit manual force-on. A suppressed request leaves an already-on relay on until its real minimum-on boundary.
- Seven explicit upstream safety conditions exercising the actual suppression and forced-cutoff expression. The sensor-fault case supplies an already-blocked upstream request and checks forced cutoff only; it does not qualify the upstream resolver.

The private generated `deleted_consumer` and `inverted_consumer` variants change only the extracted branch. Both must compile successfully. Each then runs the unchanged selected-true/open-vent witness for desired open and desired closed in both selection modes (**eight expected completed behavior failures**). Each must exit 1 with exactly the named `selected_consumer_request` assertion and its completion line. Compile failure, timeout, unknown scenario, unrelated assertion or unexpected success fails the qualification.

## Execution and custody

ROOT materializes the full composed source SHA in [native-plan.json](native-plan.json) and uses the existing admitted versioned `repo-validate` path. No new template/image/security settings are needed. Use one serial check, the supported existing 2 CPU/4 GiB limits and 500m/1 GiB requests, with a fresh shared-owner/capacity check. The driver is bounded to 600 seconds (outer timeout 660 seconds; workflow allowance at least 720 seconds). It refuses macOS and requires `VERDIFY_NATIVE_QUALIFICATION=1`.

The source-only check command is:

```sh
python -m pytest -q tests/test_fog_vent_consumer.py
```

The native check uses only Python's standard library plus the supported image's existing C++ compiler. It performs **three compiles, 64 scenario processes and seven source/toolchain metadata processes**. Every invocation retains complete stdout/stderr, exit, timeout and hashes before its verdict. It records actual compiler version/target and compiler/frontend/assembler/linker binary hashes; the outer native receipt must bind the image digest, Workflow/Pod UIDs, node, limits and terminal exits. Native fixture binaries are hashed and deleted, never promoted or used as firmware artifacts.

All text source and output files are emitted as individually hashed base64 custody records, plus a complete artifact manifest (16 MiB bound). ROOT retains the entire native stdout before Pod collection expires, verifies every decoded hash and the final receipt, and preserves original failures. No verdict is inferred from workflow green alone. `process-results.json` and partial outputs survive a subprocess timeout. ROOT submitted once and performed no retry. The unchanged supported template permits one short infrastructure retry; none occurred, and all attempt/Pod records were checked.

Locally, **35 small pure Python checks passed** using the existing repository virtualenv. They cover source extraction/refusal, exact private mutation scope, inventory, the Mac compile guard, causal-negative result validation, and partial-output retention with a mocked subprocess. Ruff check/format passed. No C++ compilation, scenario execution, corpus replay or broad suite ran on the Mac.

## Remaining acceptance

P2 uses the pure extractor's exact fragment/source hashes to describe the required policy, upstream boundary, actual switch readback, persistent relay state and time/provenance inputs. Same-row `eq_vent`/`eq_fog` outputs are not causal pre-tick state. Unknown safety state cannot be filled with permissive defaults. A valid synthetic fixture cannot authenticate historical state.

This packet does not establish all-row downstream counterfactual propagation, full prefix recovery/negative coverage, actual device latency or physical vent confirmation, full baseline–target–baseline epochs, rescue, writer handoff, draw authority or efficacy. Keep full #639 and its physical/integration dependents open. Native qualification may establish only the named synthetic single-consumer behavior after exact result custody is verified.
