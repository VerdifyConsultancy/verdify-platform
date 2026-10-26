# Firmware qualification shutdown handoff

Captured once at **2026-10-26 13:16:38 UTC** after Jason requested shutdown preparation and that background cluster work remain active. Monitoring stops after this capture. No Workflow was terminated, changed or resubmitted, and the compiler Workflow was not submitted.

## Active native qualification

- Context / namespace: `vallery` / `agent-fleet-ci`.
- Workflow: `verdify-finite-native-d7c25b0c-20261026-01`.
- Workflow UID: `88700998-b28a-4303-8363-3cb053dfbd00`.
- Exact source: `d7c25b0c620d537d56f938302d8be236a4d46fbb` (PR #1091).
- Exact baseline: `ccc4274cb790308685110a154831480f700e95d7`.
- Workflow phase at capture: **Running**, started 13:14:57 UTC; no terminal result yet.
- Check Pod: `verdify-finite-native-d7c25b0c-20261026-01-run-check-576053662`.
- Check Pod UID: `02638fd2-f38b-4e96-a42e-64ee5a7c7a27`.
- Node: `vm-k3s-node7`; main container started 13:16:08 UTC, **zero restarts**.
- Main image: `registry.vallery.net/jvallery/agents-ci-tools@sha256:7d61fca39280efb3b26419a111609f4713f3a1cc54b736a7af624fdf03c8f0e6`.
- Main requests: 500m CPU, 1Gi memory, 4Gi ephemeral storage. Main limits: 2 CPU, 4Gi memory, 12Gi ephemeral storage.
- Argo wait sidecar adds requests 100m CPU / 128Mi and limits 500m / 512Mi.
- Prepare Pod completed successfully on node5 with exit 0.

The captured check log is **27,012 bytes**, SHA256 `d1e04c8141865fc65b7bedbf1b8259aeb9c2237f9ca83d25254b5fd55ebcabbe`. It has progressed into native firmware tests and contains passing case output. There is no terminal `FIRMWARE_STRICT_RESULTS`, JUnit/custody emission or complete acceptance result yet. Do not label any pending target passed from this partial log.

## Preserved files

All files are under `/private/tmp/verdify-resume-review-20261026/`:

- `firmware1091-native-live-status.json`: exact final read-only status snapshot.
- `verdify-finite-native-d7c25b0c-20261026-01-check.log`: current complete captured check log; written through a temporary file and atomic rename.
- `firmware1091-native-workflow.json`: submitted request shape and exact command.
- `firmware1091-native-command.sh`: readable native command.
- `firmware1091-secretless-workflow.json`: **prepared, not submitted** compiler request.
- `firmware1091-secretless-command.sh`: unchanged declared secretless compiler command.
- `firmware1091-workflow-preparation.json`: file hashes, source/base and compiler-command identity.
- `monitor-firmware1091-native.py`: read-only exact-UID collector, not running in the background.
- `firmware-validation-path.md`: existing submission path, coverage limits and source references.

The native command captures each strict step's actual return code, 140 current actual-YAML cases, and 70 new historical counterexamples. The historical run must have exactly 32 failed / 38 passed, no errors/skips, and restore its private sensor file to the exact HEAD bytes. Invariant exit 2 is a failure. Custody limits are 4MiB per file and 16MiB total. Logs and decoded outputs must be retrieved once the command emits them; none had been emitted at this shutdown capture.

## Continuation after explicit resume

1. Read the existing Workflow by **name and UID**; do not submit a duplicate. Collect the final check log and any `NATIVE_CUSTODY_FILE`, `NATIVE_CUSTODY_MANIFEST` and `FIRMWARE_STRICT_RESULTS` records using the read-only collector. Its outputs use atomic replacement and verify each decoded file hash.
2. The existing request has a 7200-second deadline and retains completed Pods for 3600 seconds. If resuming after Pod garbage collection, inspect the retained Workflow result first and record missing full-log custody truthfully; do not silently rerun to replace evidence.
3. Require actual terminal success plus strict native/invariant/replay zero exits, current 140/140, exact historical 32/38 counts and source restoration before claiming qualification.
4. Only after native terminal completion and renewed execution, root may submit the prepared compiler Workflow as the serialized second lane. It uses the exact committed `firmware-secretless-compile` command/image, two public-dummy targets and generated-C++ negative test. It does not create a deployable firmware artifact.
5. No deployment, signed firmware build, flash, new ESP32 connection, node cordon, credential action or writer-hold release is included. Existing background cluster work remains active; broader runtime readback belongs to root's shutdown handoff.
