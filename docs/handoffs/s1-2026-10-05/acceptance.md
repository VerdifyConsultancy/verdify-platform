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
