# S1 sprint acceptance custody — October 5, 2026

Scope is milestone 32: #949, #950, #951, #952, #862, #371, #424 and #778. Jason's execution objective authorizes delivery without discretionary human gates. This receipt separates source changes from deployed product acceptance. **The sprint is not yet complete.** Root integration, build/promotion and production reconciliation are coordinated in `/Users/jason/repos/verdify-s1-20261005`.

## Verified investigation/source evidence

| Issue | Evidence established | Required final evidence |
|---|---|---|
| #862 | Warm init uses one canonical binding scan; recovery/candidate guards remain. 53 Lab guard tests pass. Init scan count/time and publisher source/build/upload phase timings added. | Exact built/pinned/running publisher digest, faster measured init/main/total, natural consecutive 10-minute runs, publication freshness and preserved guard failure behavior. |
| #778 | Twelve immutable typed extracts and exact SELECT-only SQL hashes; reproducible 271-row join, 199 repeated vent refusals. Live historical climate/action re-extraction matches frozen bytes. 89 incident/readiness tests and native firmware tests pass. | Merge the v4 disposition and retain #749's incomplete physical-proof prerequisite. The investigation's allowed fail-closed disposition is distinct from physical qualification. |
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
