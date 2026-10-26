# Focused firmware validation path — read-only discovery, 2026-10-26

## Supported submission

Reuse existing `repo-validate-568985cdbf6b4e3f` in context `vallery`, namespace `agent-fleet-ci`. Its immutable spec annotation is `568985cdbf6b4e3fdec9724f096b9816e18ace2a0b001bc52dfe9fb6e417246e`; live UID `09f8d5e5-68bf-484e-b5a3-1181d1a1ee50`.

Parameters are `repo`, `revision`, `base_revision`, `checks_json`, optional `default_image`. `checks_json` is an array of `{name, command, image?}`; the template transports commands through base64 and executes them in an exact-revision checkout. Bind the full repository URL and full source/base SHAs, and assert the resolved HEAD in the command. This path cannot fetch an uncommitted Mac worktree; preserve the actual postcommit chronology.

The previously accepted invocation is `planning/reviews/2026-10-18-execution/native-custody/firmware-native-02-submission.json`. It used this exact template, a 7200-second overall deadline, and 3600-second completed-Pod log retention. It submitted one check that ran native/invariant/causal/replay steps sequentially and retained each actual exit code. No new pipeline, RBAC, credential, live DB or device is needed. The original `-01` attempt was rejected for a `parallelism` override; do not repeat that field. A one-element check list bounds check concurrency without overriding it.

Run-check bounds: requests 500m CPU / 1Gi memory / 4Gi ephemeral; limits 2 CPU / 4Gi memory / 12Gi ephemeral. Default check image is `registry.vallery.net/jvallery/agents-ci-tools@sha256:7d61fca39280efb3b26419a111609f4713f3a1cc54b736a7af624fdf03c8f0e6`. Existing ServiceAccount is `argo-ci-workflow`; UID:GID 1001:1001 for the check; no privilege escalation and all Linux capabilities dropped. Runner selector is `agentfleet.vallery.net/runner-eligible=true`. Existing semaphore and retry policy remain; the template permits one retry for an attempt shorter than 600 seconds. Root must refresh current capacity/admission before submission.

The fetch init-container uses its existing repository-restricted read-only GitHub App identity, destroys its token before the check, and has a separate credential mount. Do not claim this proves zero ambient Kubernetes/network access. Use only the approved pure source/test commands, device-write disabled, no ambient DB handles or live service tools.

## Checks needed for this source packet

- `make test-firmware`: includes policy vector, engine, recovery, injection, greenhouse native cases and tracked-corpus replay.
- `make firmware-invariants`: strict actual exit zero. Capture the coverage JSON and diagnostics; no exit-2 compatibility waiver.
- Explicit `THRESHOLD_PCT=0 make firmware-replay OLD=<exact base> NEW=<exact head>`; bind corpus identity and preserve output. The replay compares compiled C++ decisions, not the ESPHome aggregate lambdas, so actual-YAML tests remain the causal proof.
- Retain the actual-YAML focused test from full PR CI, including the new finite-contributor cases.
- Compiler qualification: reuse the exact declared `firmware-secretless-compile` command and its pinned ESPHome 2026.6.5 image in `.agent-fleet/ci.yaml`. It compiles both maintained targets using public dummy credentials and verifies a generated-C++ negative case, then emits source/toolchain/config/generated C++/binary provenance. It performs actual ESPHome compilation but does not literally execute the `make firmware-check` wrapper; report that equivalence honestly or add the literal wrapper with the same public fixture if required. Never supply production credentials for this qualification.

Serialize the compile check after the native check, rather than starting two heavy lanes. The existing declaration is an input to `repo-validate`; do not edit generated `.agent-fleet/ci.yaml` or invoke a signed production firmware build to replace secretless qualification.

## What current full PR CI actually covers

Live `verdify-platform-pr-ci` runs its trusted event/source precheck and invokes only `verdify-platform-ci` template `validate`. That container runs `scripts/ci-local.sh`, under `native-ci-candidate` on node6, with 500m CPU / 768Mi memory / 10Gi ephemeral requests, 2 CPU / 4Gi memory / 10Gi ephemeral limits, and 1500-second validation deadline.

`ci-local.sh` includes `tests/test_probe_diagnostics_native.py`, `tests/test_policy_relay_dwell_refresh.py`, policy engine and recovery tests, and twin C++ syntax compilation. It does not run `make test-firmware`, strict `make firmware-invariants`, or ESPHome compilation. Its replay trigger at lines 349–350 lists `firmware/lib/*.h`, `firmware/lib/*.cpp`, controls.yaml, tunables.yaml and globals.yaml; sensors.yaml alone does not trigger it. Neither current PR nor main template invokes the separately declared firmware profile commands.

## Corpus limits

The generated `firmware-logic` profile explicitly accepts invariant exit 2 as a legacy corpus. That is not strict invariant acceptance and must not supply this packet's result. The current tracked compressed corpus is 17,172,732 bytes and its bounded header read contains all five required invariant columns (`ts`, `temp_avg`, `vpd_avg`, `rh_avg`, `occupied`); execute the strict run to learn the actual result.

The previously accepted October18 strict run observed 296,698 rows, zero invariant violations, and zero of 48 policy fields fully imposed, with an injection ceiling of 27. Those are historical limits, not a freshly executed current result. Do not claim a full experimental policy-state or physical sensor/relay qualification. No band-curve change is in the finite-contributor packet, so no separate band-curve experiment is introduced.

No checks, builds, jobs, credentials, schedules or production/device changes were submitted during this discovery.
