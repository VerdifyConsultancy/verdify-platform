# S1 sprint acceptance custody — October 5, 2026

Scope is milestone 32: #949, #950, #951, #952, #862, #371, #424 and #778. Jason's execution objective authorizes delivery without discretionary human gates. This receipt separates source changes from deployed product acceptance. **The sprint is not yet complete.** Root integration, build/promotion and production reconciliation are coordinated in `/Users/jason/repos/verdify-s1-20261005`.

## Verified investigation/source evidence

| Issue | Evidence established | Required final evidence |
|---|---|---|
| #862 | Warm init uses one canonical binding scan; recovery/candidate guards remain. 53 Lab guard tests pass. Init scan count/time and publisher source/build/upload phase timings added. | Exact built/pinned/running publisher digest, faster measured init/main/total, natural consecutive 10-minute runs, publication freshness and preserved guard failure behavior. |
| #778 | Twelve immutable typed extracts and exact SELECT-only SQL hashes; reproducible 271-row join, 199 repeated vent refusals. Live historical climate/action re-extraction matches frozen bytes. 89 incident/readiness tests and native firmware tests pass. | Completed: [#778 closed October 5 at 20:00:39 UTC](https://github.com/VerdifyConsultancy/verdify-platform/issues/778). [#749 retains the physical-proof constraint](https://github.com/VerdifyConsultancy/verdify-platform/issues/749#issuecomment-6001965453). The allowed investigation disposition is distinct from physical qualification. |
| #371 | Currentday and September 4 legacy SQL/API/both MCP replicas agree on 14 climate fields under stable before/after SQL brackets. Public typed snapshot agrees under its own SQL bracket. Actual source public formatter keeps binary and credit labels separate. | Deliver writer/current-row fixes, verify new runtime identities and genuine current/historical probes; preserve historical revisions. Qualified physical target/panel producer and consumer acceptance remain unproven. |
| #424 | No new completion assertion in this receipt. | Served/consumed/raw-readback source, units, timestamps, generation and consumption proof; preserve explicit unknowns. |
| #949 | No new completion assertion in this receipt. | Genuine bounded transition, retained caps/failure/confirmation history and required two-hour steady observation. |
| #950 | No new completion assertion in this receipt. | Durable token-file wiring, natural brief and alert delivery, bounded retry/no duplication. |
| #951 | No new completion assertion in this receipt. | Both authenticated fresh camera images, truthful failure states and successful natural analysis. |
| #952 | No new completion assertion in this receipt. | Durable least-scope monitoring identity; protected positive and unauthenticated negative before/after replacement; both vantage probes recovered. |

## #371 live consumer receipt and its limits

[The captured receipt](evidence/scorecard-consumers-before-release.json) was collected October 5 at 19:47:13 UTC before sprint release. It records MCP running image identities, source SHA and tool hash. The Iris bearer stays inside the MCP containers. Only typed score/contract evidence is retained; no credentials or plan/lesson narratives are captured.

| Day | Binary joint | Temperature binary | VPD binary | Attributable credit |
|---|---:|---:|---:|---:|
| 2026-10-05 |0.8%|14.2%|82.3%|78.9%|
| 2026-09-04 |6.1%|34.3%|30.8%|85.8%|

The historical sample directly reproduces high credit alongside low binary compliance across live SQL/API/authenticated MCP and the actual source formatter. This is useful semantic acceptance evidence; it does not verify new writer adoption or make house-average desired-setpoint readings into physical crop compliance. The source renderer probe uses live typed metrics and records rendered/source hashes; it explicitly does **not** prove natural live Lab adoption. Historical public API comparison is available through the date-specific scorecard; the public evidence snapshot is currentday only.

Both current and historical `physical_crop_band_evidence` return `availability=unavailable`, `unavailable_reason=publication_not_qualified`, no diagnostic/revision. The API/MCP source explicitly calls `unpublished_physical_crop_band_evidence()`, whose contract fails closed until an authenticated producer/reader exists. Route-only and native-route diagnostics have their own observational scope and cannot qualify physical crop outcomes by relabeling them. Fixed target definition, physical panel identity/crop placement, per-probe freshness, eligibility, distance/severity and worst measured zone need evidence appropriate to their actual scope. Missing DLI, center sensing, gas or cost remain unavailable.

Rerun after deployment using a fresh output path:

```
.venv/bin/python scripts/scorecard-consumer-acceptance.py \
  --day 2026-10-05 --day 2026-09-04 \
  --output /path/to/new-scorecard-consumer-receipt.json
```

Dates are explicit; choose the actual current Denver day when rerunning. The helper bounds DB queries and HTTP bodies/timeouts, probes every Running MCP replica, brackets changing daily summaries and refuses an existing receipt path. No data is rewritten. Unstable brackets, missing consumers or alias mismatches cannot produce agreement=true. Agreement alone never grants physical publication eligibility.

## Runtime acceptance and closeout

Root must record exact build/source/release-pin revisions, Argo full-sync operation identity and fresh hook receipts, intended running imageIDs, protected DB/PVC preservation, one Recreate ingestor and one ESP32 connection. Ingestor rollout requires the five-minute schema-validation log check. Passing CI, static Ready, fixtures and migration rehearsal do not substitute for product behavior.

Close each issue only on its exact acceptance criteria. Preserve the #778 hold after closing its investigation; do not launch experiments or cut over databases to complete this sprint. Record reversible recovery coordinates and distinguish genuinely unavailable physical evidence from executable work still remaining. Update this receipt with post-release evidence and real elapsed timings rather than converting pending criteria into completed claims.

## First release validation and current provenance boundary

Full Linux CI passed for source `929c372b974e501067d769c6808d821f7c4c7cb7` in workflow `verdify-platform-ci-vldl9` (UID `66602c20-9b9b-4956-a311-3dadc37ece2f`). Validation ran 19:53:19–20:04:03 UTC; the terminal output reported `ALL GATES GREEN`. [The receipt](evidence/ci-linux-validation-receipt.json) retains authoritative node identity and the stdout archive limitation: PodGC removed the pod before full log export. Image builds, release promotion and live acceptance are separate and remain pending.

[Read-only physical provenance metadata](evidence/physical-provenance-boundary-20261005.json) shows six contributor revisions, all `route_only`, and zero hardware-attested revisions. The sole native source binding covers September 29 12:00–September 30 06:00 UTC. Target/contributor records cover that historical observation or the future November 2–January 1 window, not October 5. Those declarations do not authenticate physical serials, placement, calibration or Modbus poll times. Current physical outcome publication remains unavailable. The remaining executable work is an explicitly observational native-route measurement projection with eligibility, distance/severity and missing-window reasons; any prospective source declaration must be future-effective rather than backdated.

## Final source validation and NFS coordination

Exact source `c8fc15e6a81d29b16e23d6db078893160704b4ea` passed full Linux validation at 21:28:14 UTC in workflow `verdify-platform-ci-qbghc`, UID `491bd3a6-2a7f-4dcb-8bab-3b72f9edc4ec`. The actual main container exited 0 and its preserved 27,196-byte stdout includes `ALL GATES GREEN`. [The receipt](evidence/ci-c8fc15e6-validation-receipt.json) binds the source, validation node, Pod UID, timestamps and stdout hash. Image builds, candidate adoption, promotion, full sync and product acceptance remain separate.

Jason directed coordination of every further NFS change with his local Proxmox Codex session. The [coordination receipt](evidence/proxmox-local-nfs-coordination.json) records delivery to that local task, without a fleet-pod broadcast. Further NAS/NFS/service/host/client mutations are stopped pending a coordinated recovery sequence. The incident packet identifies the paired stock NAS service restart at 21:08:20 UTC and its fleet-wide effect; scoped registry/Frigate probes never establish whole-fleet recovery. The peer acknowledged the hold and is independently verifying node recovery. Normal application builds and scoped Verdify delivery continue without additional NFS mutations.

## Final ingestor release and remaining observation

Source `37a9cc0b7a2ef07585d0790f4bb4dcfbbe75bb6f` passed full Linux validation and all eight registered image builds in workflow `verdify-platform-ci-n4v62`, UID `769be28b-30b0-4322-8f61-88f7a0f9ce8d`. The startup repair records actual native replay before the existing boot-window DB suppression; it preserves all 207 expected fields, nonfinite/range checks and generation fences. c8's earlier startup gap was a delay that periodic callbacks cleared, not a permanent outage. Its original 12+9-command completion and genuine natural transition remain historical evidence.

The [verified final ingestor release receipt](evidence/verified-final-ingestor-release-receipt.json) binds exact promotion `88567ed19254a75e9bacde724c42c560709c14e6`, a full 127-resource operation at 22:44:57–22:47:15 UTC with no selector/prune, all six fresh successful ordinary hooks, unchanged ledger 270–273 hashes/timestamps and preserved DB/PVC identities. The single ingestor is Pod UID `55d2bbbf-8fd6-4297-81bf-5745710a71f6` on node4, actual digest `sha256:e22e160f80adf79f70778c3d8796e036ced31a3e59fcf7b4d6e82eecb0d80f22`. Packaged module SHA is `4afb5b7daa1873952cced16387d18104a9c95923333a3210fad333d6f7ba56a6`. Independent readback found Synced/Healthy; five-minute logs ran 300 seconds with zero schema-validation errors and no restart, smoke passed 9/9, and namespace device-monitor found one ESP32 socket. The global final-pod repeat was requested from Jason's local Proxmox task.

Only the shared ingestor image changes in its Deployment and existing vision/HA-backfill CronJobs; other 124 desired resources are identical. API/MCP/migrate/orchestrator and publisher retain their reviewed c8 digests because schema and consumer sources are unchanged. [Build provenance](evidence/final-37a9-build-provenance.json), [promotion and rollback pins](evidence/final-37a9-promotion-receipt.json), and [render delta](evidence/final-37a9-render-delta.json) preserve these boundaries. Actual production render contains no gate-r PostSync Job; no fictional hook success or experiment activation is claimed.

The [prospective observational declaration](evidence/native37-prospective-declaration-apply-receipt.json) was atomically committed at 22:50:26 UTC for the still-future 23:00–06:00 UTC interval. Target 3, route-only contributor revisions 7/8/9 and native source binding 2 bind the actual37 collector and firmware. Physical serial/evidence fields are NULL and physical/experiment qualification stays false. First completed-bin and public/Iris/Grafana consumer proof remain pending. This writes no control setpoint or historical measurement.

The final37 audited normal planner event produces 17 genuine desired-value changes and a 14-command candidate. Cap12 correctly holds the initial broad pass; fresh source-bound stages and confirmations are still required. Preliminary observation cannot count as the final two-hour steady interval. #949 remains open. #952 is [closed with durable replacement acceptance](https://github.com/VerdifyConsultancy/verdify-platform/issues/952#issuecomment-6004277462); #778 was already closed.

Publisher optimization `8f04fc1794b7ec001c550c26edcf223d72b153fd` is now on main after [qualification PR954](https://github.com/VerdifyConsultancy/verdify-platform/pull/954) passed full Linux CI at exact patch head `111132cc`. Its registered main build and later publisher-only promotion are separate. Current natural publisher runs of 751 and 651 seconds did not qualify ten-minute cadence. Three natural successful faster runs, final native consumer/public agreement, and natural 00:00 UTC vision/brief receipts remain required. **The sprint remains active and incomplete.**

## 23:15 UTC continuation: retained halt and coherent successor

The final37 run confirmed **15 commands in two capped batches of 12 and 3**, then safely halted at 23:01:42 UTC because two declared moisture guardrails emerged after initial admission. [The exact halt receipt](evidence/policy37-fully-confirmed-stage-halt.json) retains both current desired/native/plan values and the no-dispatch disposition. The preliminary observer is stopped with **zero two-hour acceptance credit**; the original state, approval, Pod custody and independent database confirmations remain unchanged.

Source `81d76a4c3d35a925ebd2438d89099d1f657b3f8f` is on main and validating through the registered pipeline. Its narrow fix permits only declared guardrails present in the unchanged approved plan and original native baseline, equal to the current dispatcher value. Source, plan, generation, readback, cap and confirmation fences remain. Explicit version3 recovery covers only two fully confirmed batches totaling13–24 unique effects, with each batch at most12 and the first fully confirmed before the second starts. Archival preserves the original halt and requires independent fresh approval. All 67 focused writer tests pass; no SQL migration is needed. The source already includes the publisher guard optimization. Build/promotion/live qualification and the clean two-hour interval remain pending; the earlier proposed publisher-only promotion is superseded by this coherent ingestor/publisher release.

The optimized publisher source8f passed full Linux validation but **built no images**: [registered build admission rejected a stale capacity signal](evidence/publisher8f-admission-blocked.json). The local Proxmox task traced repeated observer deadlines to a blocked NFSv4 file-open in node5 Zot and is coordinating replacement of only that registry Pod, preserving its PVC. The Verdify lane makes no competing NFS/storage mutation and does not bypass admission or widen deadline/freshness checks.

Prospective source37 declarations remain immutable. Any later-source callbacks will be reported with truthful missingness/discontinuity rather than relabeled as37 or backdated into its interval. Current f2 publisher natural runs include651,577 and627 seconds; three consecutive runs below600 seconds have not qualified. Midnight natural vision/brief acceptance and source81 product acceptance remain pending. Live ordinary pods are Ready; aggregate Argo health newly reports Degraded and is being investigated separately from the earlier successful release receipt. **No additional issue is closed by this progress record.**

## First completed native37 bin: consumer agreement and physical hold

[The immutable first-bin consumer receipt](evidence/native37-first-bin-consumer-acceptance.json) records read-only capture beginning 23:15:27 UTC. The 23:00–23:15 bin is eligible under target revision3, source binding2 and route-only contributors7/8/9. SQL, API, both Running MCP replicas, the actual Iris pod's authenticated MCP context and dedicated Grafana datasource queries agree on current and historical native measurements. Public API parity also passes. Iris evidence proves authenticated context access and registration state; it does not claim a new model action or plan execution.

| Denver day | Eligible bins | Temperature in band | Temperature high distance | VPD in band | Joint in band | Worst measured temperature zone |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| October5 | 1/72 | 0% | 3.518667602539054°F | 100% | 0% | east |
| September29 | 72/72 | 65.27777777777777% | 0.574444700170445°F mean | 100% | 65.27777777777777% | north |

The October5 denominator remains the full72-bin day:44 bins lie outside the prospective target and27 have not elapsed. Percentages use the one eligible bin, not invented coverage. Temperature distance is measured above the declared logical target; VPD is in band. Hardware identity, crop placement, per-probe physical poll time, physical outcome publication and experiment/causal qualification remain **false**. These are native route diagnostics, not authenticated physical crop results.

Collection-tool checkout identity is source81 (`81d76a4c3d35a925ebd2438d89099d1f657b3f8f`); actual API/MCP runtimes remain sourcec8 (`c8fc15e6a81d29b16e23d6db078893160704b4ea`), and the eligible callbacks are actual collector37 (`37a9cc0b7a2ef07585d0790f4bb4dcfbbe75bb6f`). Whitelisted runtime environment readback and Pod/image identities substantiate these separate boundaries. Source81 is still validating and has not been deployed.

The naturally served static Lab generation still contains its earlier unavailable projection: native render parity is **false/pending**, although its generation hash matches the public success receipt and the dynamic public API agrees. No manual publication was triggered. Preserve source37's prospective interval immutably when later collector identities arrive; do not invent overlap, backdating or physical qualification to finish acceptance. #371, #862 and sprint completion remain open pending their remaining live criteria.

## Natural native public adoption and literal #371 audit

[The natural public as-of receipt](evidence/native37-natural-first-bin-public-asof-acceptance.json) proves the served HTML matches the actual first-bin typed projection. The publisher read source23:24:30 UTC within the retained23:15–23:30 stable epoch; generation success23:29:59 and served hash `20018b3ba8da5b2018ab3b6d0728f2fbb0dd315943216ed4bbafb8215a092645` agree. At23:30:06 the dynamic SQL/public API already had two completed eligible bins, so the static page truthfully trails by one bin. Temperature3.519°F high/east, VPD100%, joint0%,1/72 eligibility and physicalfalse are visible. Actual Argo readback is Synced/Healthy at unchanged desired pins. The natural23:20 job completed23:31:06 in653 seconds; this is public adoption proof, not optimized-cadence acceptance.

[The criterion-by-criterion audit](evidence/issue371-literal-criteria-audit.json) retains100 focused passing tests plus all3 private PostgreSQL integrations passing with no skips. Hand-calculated distance, missing-axis independence, target provenance and versioned current/historical consumer scope qualify. The remaining literal boundary is explicit: the SQL fixture proves low **legacy** binary versus high credit; a separate synthetic physical-model fixture proves low physical percentage versus high credit in API/public rendering. Production physical SQL remains intentionally unavailable. These composed tests are not a single qualified physical SQL-to-consumer fixture, and native route diagnostics cannot be renamed physical compliance. This receipt does not close #371 or authenticate hardware. The earlier static-adoption pending status is superseded by this natural as-of proof; source81 deployment, optimized publisher cadence and the policy observation remain separate.

### Retained source37 band-lineage audit (23:47 UTC)

Issue #424 remains open pending final81 passive refresh. All six temperature/VPD low, high and target series have source, units, publication/setter grid, timestamps and generation mappings in [the retained literal audit](evidence/issue424-retained-source37-literal-audit.json). The historical 0% comparison was a reporting defect; the September 4 physical discrepancy remains unobservable. Ideal SQL residuals and controller callback aliases provide no independent wire qualification. Gate #749 remains fail closed.

### Source81 registered build and source promotion (23:56 UTC)

The supported same-workflow retry completed all eight exact-source images and its pin actuator. [Build and source-promotion receipt](evidence/final81-all-eight-builds-and-source-promotion.json) binds workflow UID, resolved tree and Dockerfile hashes. Promotion `6429acbb2c79f784e3978e9c74a1c8719d25076c` changes only ingestor and publisher release digests, affecting five image consumers; the retained API/MCP/migrate/orchestrator release pins remain unchanged. This is source promotion only. Full production sync is held until natural midnight brief and vision observations finish.

## Source81 live release and sole-writer verification (October6 00:34 UTC)

[Actual live release](evidence/final81-live-release-6429.json) supersedes the prior source-only promotion status. Full revision `6429acbb2c79f784e3978e9c74a1c8719d25076c` synced without prune or selector:127 resources, six newly created successful normal hooks. The observer also captured one retained historical Winter Job; it is not counted as a fresh hook. Initial terminal health was Degraded; ordinary ingestor initialization completed and Argo naturally became Healthy at00:20:04. Independent readback was Synced/Healthy at the exact revision. No optional experiment gate hook was activated.

The sole Recreate writer is source81, digest `sha256:2813240dbd5ce3c644d0285fd4603901eef9ae38ad00d28f4e64deb05f46ad2d`, Pod UID `237002d4-5fb9-450f-8fe2-e266c969a1e1` on node5. A300.268-second log observation retained the same Ready UID, restart0 and zero schema-validation errors. Nine smoke checks passed against the retained API sourcec8/digest09a7. DB StatefulSet/PVC, dumps PVC, Grafana PVC and migration271–273 hashes stayed unchanged. Migration274 was not yet applied at this capture.

The coordinated local Proxmox [pinned-namespace census](evidence/proxmox-global-final81-socket-survey.json) at00:31:12 covered all eight hosts and500 unique namespaces, with no probe errors and exactly one ESP32 connection: node5, `10.42.6.62:34928` to `192.168.10.111:6053`. ROOT independently matched the live Pod UID/IP/digest and restart0 after capture. Handles were pinned during each read-only probe to avoid exited-namespace races. Process exits before handle capture are recorded separately. [First](evidence/proxmox-global-final81-first-partial.json) and [second](evidence/proxmox-global-final81-second-partial.json) partial censuses remain immutable and do not themselves qualify fleet coverage. No census created a device connection or mutated storage.

#950/#951 natural midnight observations and #424 literal lineage are now independently qualified and closed. The first optimized natural publisher run completed410s with one validation scan and a matching fresh public generation; three consecutive natural runs remain required. Source81 produced a genuine eight-field Iris plan change at00:30:32; native confirmations and the complete7200-second policy interval remain separate acceptance. #371's source325 qualified physical reader is merged and building; production physical rows remain unavailable and #749 stays fail closed.


## Optimized publisher natural cadence qualified — 00:47 UTC

[Publisher #862 acceptance](publisher-862-acceptance.md) and its [exact live receipt](evidence/publisher81-three-natural-cadence-acceptance.json) qualify three consecutive natural00:20/00:30/00:40 runs at410/420/410s, one scan each, every logged phase and exact optimized imageIDs. The third generation success00:46:45 matches the served public hash and remains fresh until01:06:45 at unchanged1200s TTL. Schedule/Forbid/deadline are unchanged. Earlier over-budget/failed runs and missing-log limits remain preserved; this supersedes earlier pending #862 cadence status. Physical holds remain explicit, and unrelated policy/physical-reader/sprint acceptance remains pending.

## Source325 physical-reader release (October6 01:08 UTC)

The registered source325 pipeline passed full Linux validation and [all eight builds](evidence/source325-all-eight-build-provenance.json); the successful [actuator](evidence/source325-actuator-candidate-proof.json) changed candidates only. [Three-image promotion](evidence/source325-three-pin-promotion.json) keeps the source81 writer and qualified publisher3a unchanged. Source325 adds a genuine owner-only append publication path and bounded SQL reader consumed by API/MCP; it does not insert synthetic physical records into production.

[Reviewed render scope](evidence/source325-reviewed-render-scope.json) contains the same127 objects and zero Secrets. Normal pending drift changes API/MCP and the Winter CronJob images; six BeforeHookCreation Jobs use the corresponding new API/migrate images. Raw full kubectl dry-run stops at the retained Winter Job immutable template; metadata-only removal of injected Argo tracking annotations in the normal-object dry-run is not application drift. Argo full sync performs its declared hook replacement.

[Actual release](evidence/source325-live-release-9b4f.json) records full exact revision `9b4f8023839ffda05b9d0fd1a70fe63e62c6d9b4`, succeeded01:05:19 with127 resources, prune requestedfalse and no selector. Six fresh successful hooks are distinct from one captured historical Winter UID. Aggregate health became Healthy01:05:16. All four protected DB/Grafana/dumps object identities and271–273 applied hashes remain unchanged;274 is ledgered at exact SHA `0b939df1a79e3ce71835e59aedb5d1898805cd43cae72b79fa905f77067a8e4f`. Nine live smoke checks passed against API source325/digest3d5d, and the namespace monitor found the sole original source81 writer.

Immediate post274 actual application-DSN TCP attestation passed on the unchanged writer with exact packaged module hashes. Its independent effective-capability projection equals the273→274 rehearsal (`ef10e776…`); the legitimate catalog successor did not broaden writer capabilities. Separate API/MCP/Iris/public physical-consumer acceptance is being collected. #371 is not closed by this release receipt alone. The complete7200-second policy observation remains active00:41:25–02:41:25 and is not shortened by the rollout.

After274, rollback must retain the applied ledger, source325 C0/bootstrap configuration and migrate548 image; re-pin only compatible API/MCP images to priorc8 when needed. A full source642/C0 rollback is not recommended. Old-MCP ordinary TCP compatibility has been captured on retained prior pods; old-API compatibility has source-identical startup SQL but no performed old-image rollback. No physical experiment, OTA, database cutover, synthetic production publication or NFS mutation occurred.

## #371 literal metric-path acceptance — 01:07 UTC capture

[Qualified physical-reader acceptance](physical-reader-371-acceptance.md) now proves all three literal criteria: the single private SQL→actual HTTP/FastMCP→public/planner fixture preserves2/96 physical bins separately from85.8 credit; hand-calculated distance/missingness cases pass; current/historical genuine SQL, API, both MCP replicas, public and authenticated actual Iris registered-tool context agree. All four new API/MCP pods pass ordinary TCP attestation without raw-table or producer privileges. Production physical rows remain0 and both dates are explicitly unavailable; #749 and the independent7200-second policy observation remain unchanged. Failed wildcard CI and the operator-only unavailable SDK probe are preserved alongside successful repairs and exact artifact hashes.
