# PR #1095 / compiler final shutdown observation

Observed 2026-10-26T15:17:39.615724+00:00. **Observer stopped; cluster jobs remain active and unchanged.**

Exact source `96464472f58ccb7bb1f62aa19af1421fa601a56c`, context `vallery`, namespace `agent-fleet-ci`.

- Full native PR CI `verdify-platform-pr-ci-qkprq`, UID `63665964-8b9a-46f7-839e-6f2ce0af2f19`: **Running** at 2026-10-26T15:16:47.357462+00:00; required `Verdify Platform / Argo PR CI` **pending**. Full test suite last reported 45%.
- Its actual private fixture report is already `qualified=true`, **19/19 passed**, 10/16 native ops reasons. This is the exact PR head’s source 19-case scope; main’s separately merged 22-case implementation must survive an ordinary merge. A moved base is not grounds for a rebase.
- Compiler `verdify-control-compile-96464472-20261026-01`, UID `3d86b16a-f485-4d9f-9660-0fcf6f320214`: **Running** at 2026-10-26T15:16:46.872282+00:00. Greenhouse target compiled in **478.60 seconds**; recovery target is in its CMake/compile sequence. **No final two-target / negative /16 compiler receipt exists yet.**
- Compiler command and pinned ESPHome 2026.6.5 image exactly equal the previous successful c60 qualification command/image. Actual running imageID matches the pin.

Exact Pod UIDs, current container states, log tails, source/command provenance, private proof and atomic log hashes are in `1095-shutdown-final-observation.json`; compiler-only state is `control-secretless-shutdown.json`.

On resume, inspect these exact handles and verify source/UID before continuing observation. A model-capacity error or observation timeout is not cluster-job failure. Require authoritative terminal state and full final receipts; never recreate work merely because monitoring stopped. Strict native 219/current+76 causal-red evidence remains separate and was not rerun.

No cancellation, job submission/retry, source or GitHub write, deployment, firmware upload, credentials change, new ESP32 connection, cordon or scheduling action occurred. No Mac observer/background process remains running after this bounded snapshot.
