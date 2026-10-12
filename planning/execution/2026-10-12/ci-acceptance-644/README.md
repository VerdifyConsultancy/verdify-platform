# Verdify #644 bounded source and acceptance review

Reviewed after the attended release receipt at `07e7097e30a93acd642b6b7a360194eb754b1287`, against product main `d43919e933eb1be69559be7566853ff69a64e903` and Agents main `d86ecb84ae55d35fbdf395ec9a5c866625bbbde6`.

**#644 remains open.** The current attended five-image build/publication passed. Retained source failure, infrastructure failure, native cancellation, idempotency and local native Git concurrency exercises remain valid within their recorded scope. They do not prove unattended delivery, authenticated cancellation observation, deployed missing-artifact rejection or final greenhouse acceptance.

The original `95e2f6e` checkpoint performed source/read-only review, local offline tests and one local source correction; it submitted no live CI, build, Job, controller/template mutation, promotion, production sync, device action or credential change. The later authorized owner PR and its normal native qualification are recorded separately below. Retry remains the sole production/release writer. Root confirmed no duplicate offline matrix implementer. The accepted contained recovery and its 18 retained staging files are unaffected.

## Clause disposition

[The 18-clause matrix](clause-matrix.json) records what each receipt proves, its limits and the smallest next execution. [Historical receipt custody](historical-evidence.json) verifies all 11 referenced retained file hashes without republishing private handles. [Current attended evidence](attended-release-evidence.json) links immutable public receipts, source identities, five selected profiles and current/rollback pins.

| Clause | Accepted evidence | Remaining limit |
| --- | --- | --- |
| Exact source and path-aware success | Attended `8wkb8`, UID `a94200db-a899-45f8-95d1-80bea935a093`, exact `cb21fd3d`, five verified publications | Operator fences were part of this success; unattended behavior is unproved |
| Pin commit no recursion | Historical authentic promotion `54b98ee6`: all eight builds/actuator skipped; current `d439` source classification has no profiles | Current promotion's terminal native event was not reread here |
| Source failure | Authenticated missing commit fetch failed with exit 128; durable terminal journal, no publication | This is a fetch failure, not deliberate application assertion failure |
| Infrastructure failure | Authentic stale registry-admission signal stopped compilation/publication | Distinct-source fix-forward is not automatic retry |
| Cancellation | Two preserved exact-UID native cancellations reject success | Authenticated post-Terminate broker observation fails the sealed-spec check |
| Missing/wrong receipt | Current reducer/publisher source checks fail closed | Native expression evaluator unavailable locally; no live omission injected |
| Missing registry artifact | Real-origin absent/present observer proof and merged availability code | Actual adopted broker/runtime missing/present resume pair is unproved |
| Retry | Preserved authenticated replay and governed same-source recovery | Neither establishes unattended automatic retry/cleanup |
| Concurrent promotion | Two real local Git processes: non-force loser rejected; winner/rollback preserved | Authenticated live overlap remains unproved; current build success alone is not pin promotion |
| Authority and rollback | Current exact native mounts, eight named denial probes, historical network/Secret negatives, reviewed before/after release pins | Named probes have bounded scope; no device/OTA endpoint was probed |
| Owner/policy | Bounded successor #4763 merged; #4806 source/native/WFT adoption; current main policy read | Whole #4014, old #4747 merge and broad shared sync are not blanket prerequisites |
| Final runtime | Root reports `d439` Synced/Healthy and expected singleton ingestor | Retry still owns runtime, #949 and observation-window acceptance |

The product branch-policy read at **2026-10-12 19:49:48 UTC** found linear history required, no required review/status fields, and empty effective rules/rulesets. This lane changed no policy. Its source review does not qualify a stable unattended producer for new required checks.

## Bounded owner source correction

[Owner source receipt](owner-source-fix.json), [exact patch](owner-source-fix.patch), [exact local Git bundle](owner-source-fix.bundle).

Original Agents commit **`6f1ebc36902e4bf9e6197aced2852d5653d32820`** on `codex/ci-runtime-source-label-20261012` corrects one metadata argument and adds a regression assertion. At the `95e2f6e` checkpoint it was local-only; the later owner PR preserves this commit and adds the exact trusted-source companion described below. Neither commit is merged or deployed. The bundle requires existing Agents commit `d86ecb84ae55d35fbdf395ec9a5c866625bbbde6` and preserves the exact commit independently of the local checkout.

Real Git reproduces a legitimate split stage: broker/tooling `d86ecb84` and retained runtime `b874103a` pass the existing complete source comparisons because all 144 runtime COPY inputs are byte-identical (`b54efdce24c74959526fdf971546a75860a71275e5535c995e06811461549e3f`). Stage binds the runtime receipt and native qualification to `b874`, but incorrectly records `d86` in the runtime catalog. Passing `runtime_revision` corrects that claim. The original PR4763 changed-client/older-runtime comparison still correctly rejects differing bytes; relaxing it would be wrong.

Validation:

- **112 existing focused workflow/authority tests pass; one native-expression test skipped** because the pinned evaluator is unavailable. No evaluator was fetched or built.
- The new catalog assertion **fails against the original code** with broker revision instead of runtime revision.
- **34/34 executable-image delivery tests pass** after the correction.
- The real Git stage reproducer now records the exact requested runtime revision; external image/Workflow/native/crane observations in that test are fixtures, not delivery proof.
- `git diff --check` and bundle verification pass. Source comparisons, native source binding, catalog conflict rejection and the shared-revision finalization boundary remain intact. No generated catalog, image or pin changed.

The [rerunnable offline reproducer](source-review/split-runtime-catalog-probe.py) accepts `--repository`, `--revision` and `--output`; use `--expect-corrected` for the bundled fix. It clones locally, reads real source/Git and substitutes only external observation functions. It makes no network or live calls. The preserved before and after JSON receipts and test logs are in [source-review](source-review/).

## Smallest safe continuation

1. Complete the existing owner PR’s required `fleet-ci/native` qualification. Preserve the original commits, failed attempt, real source guards and finalization boundary. Source qualification remains separate from later owner deployment and from the completed Verdify release cohort; do not merge/adopt the owner correction during the active production observation window.
2. Bind actual broker/runtime bytes to existing image/Workflow/native delivery receipts, then read-only observe retained absent/present artifact operations through the actual authenticated repository identity. Do not infer image consumer adoption from the completed WorkflowTemplate-only operation; the immutable source delivery declaration remains `build-required` with null candidates.
3. Resolve authenticated cancellation/terminal observation through an exact-UID, owner-authorized transition. Preserve the sealed submission and reject changed source/spec and foreign identities; do not globally ignore `spec.shutdown`.
4. Keep unattended retry and authenticated overlap explicitly unproved until a separately coordinated finite Fleet execution. Do not schedule races, faults or new builds alongside Retry's production work.
5. Reuse Retry's final exact desired/running, #949 and observation-window receipt for live acceptance. Root owns issue disposition and any later branch-policy decision.

No repeated live source/infra/cancel/replay exercise, whole-estate audit, new capacity framework, full shared Argo sync or second device connection is introduced by this packet.

## Owner PR and native qualification supplement

The owner correction is published as [Agents PR #4807](https://github.com/jvallery/agents/pull/4807) at exact head `f5d21bdb50bb3b108d3ddaf356a543c633836a1c`, base `d86ecb84ae55d35fbdf395ec9a5c866625bbbde6`. Fresh GitHub readback verifies all three changed files byte-for-byte. Auto-merge is off. The effective branch rule requires `fleet-ci/native`; the existing trusted controller automatically discovers this normal PR with a 300-second debounce. No manual duplicate Workflow/build was submitted.

The original native attempt at `6f1ebc36` failed `validate dashboard app and manifests`, Workflow UID `b8994141-66e3-4588-80c9-8c542d645b72`, exit 1. Its trusted archive is complete and preserves failure; [the full original run snapshot](owner-qualification/native-original-failed-run.json) retains the exact source/plan and check identities. The archive summary contains conclusion only, so it does not independently identify a failed step. Private diagnostics remain in retained PVC custody; this lane created no new log-reader Pod.

Local exact-source reproduction identified a stale `TRUSTED_AGENTS_PROGRAMS` digest in `delivery_stage_runner.py`: the strict verifier rejected the changed delivery script. Companion commit `f5d21bdb` refreshes that one SHA to `f8950104f61531bd3880081ea38a1c9e6a9e22cf3477423afde90de8b2ca2254`. It preserves the byte check. The stage-program ConfigMap is generated directly from the runner, so the offline Kustomize render updates its immutable identity/reference without a separate generated file or any live apply. No native catalog/controller generation changes.

The direct three authority tests fail before and pass after the refresh. All 35 tests in the affected native delivery step pass; three targeted manifest/security tests also pass with six subtests. These are local regression proofs, not native success. [The companion receipt](owner-qualification/companion-receipt.json) binds the logs, exact patch, both-commit bundle and provider readback. Native qualification of the corrected head **passed 12/12 required checks**, with all 12 archives complete and verified. GitHub independently published `fleet-ci/native: success` at **2026-10-12 20:46:41 UTC**, status `56171776095`. The exact [final native run](owner-qualification/native-corrected-qualified-run.json) binds source merge `2d07c9a42852498bd1e3e1c0375a1d239942297e`, operation `11e4f761-deb0-5a36-ba8f-7005360d190c` and plan SHA `ec8ece625c17229d72df00de3411e77eb88b6175a30acfb29c6c874604d117ea`. [Final provider custody](owner-qualification/owner-final-provider-custody.json) confirms exact head, open/mergeable PR, `merged=false` and auto-merge off. This source/PR-qualification task has no execution blocker; merge/adoption remains held by the active production-window scope. No source merge, live adoption, image build, registry mutation, production action or credential change is included.
