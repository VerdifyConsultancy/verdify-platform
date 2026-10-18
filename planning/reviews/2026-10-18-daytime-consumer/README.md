# Daytime coverage and ordinary outcome consumer — 2026-10-18

The recorded daytime assessment is complete and its result is incomplete coverage. The ordinary MCP reader is delivered. Jason also requested upgrading live Iris and this qualification to GPT-6 Luna. Model release and actual qualification results are recorded below. **#410 and #775 remain open.**

## Recorded daytime coverage

One bounded read-only transaction inspected the complete selected October 16/17 stored input against the installed migration 277 solar clock and classifier. No historical rows were altered.

| America/Denver solar day | Expected minutes | Recorded valid minutes | Missing minute buckets | Known held admissions | Ordinary dry action minutes | Complete recorded zero |
|---|---:|---:|---:|---:|---:|---|
| October 16 | 669 | 644 | 25 | 0 | 12 | NULL |
| October 17 | 666 | 643 | 23 | 0 | 2 | NULL |

The 48 missing buckets are shared climate/action gaps, not 96 separate gaps. Longest consecutive gaps are 3/1 buckets; these do not measure a continuous physical outage. No observed held classification or explicit hold marker was unknown. Snapshot records have no per-relay freshness guarantee and can default absent equipment to false. Neither the observed zeros nor typed rows prove every controller tick, continuous physical absence or crop safety. No interpolation, resampling, backfill or zero substitution was used. Science independently checked the source, native result and derived acceptance.

## Delivered ordinary MCP path

Source `b814f35e228fcb35c11593c47ea1e021e5fd73de` adds the default-off `scorecard(target_date, include_realized_dryout=False)` input and a typed nested result. Opt-in uses the explicit date as solar-night **start** date, distinct from calendar-day scorecard metrics. Default calls make no extra getter query.

The selected read uses the existing getter and ordinary identity in a READ ONLY transaction within the unchanged 15-second fence. It preserves all 46 columns, NULLs, dispositions and confounds. A 501-row sentinel rejects more than 500 episodes; a 64-KiB evidence cap rejects oversized results without truncation. Empty/all-null daytime counts remain NULL. Safety status is distinct from effectiveness and crop safety.

Native source CI `verdify-platform-ci-bgf2f` / `f2083616-e793-4994-bd3b-9a48b1fbb376` succeeded at 20:10:56 UTC: full gate, five selected serial image builds and candidate publisher; all 7 actual Pods' declared containers exited 0 without restarts. Only MCP was promoted, digest `ae8a367035a3ae8a06ab2384bc166f444b3decad0da21e5c70e53e76203d9c73`.

Release `e545380a23129eb1830e9d6d804334f46f8fdcfe` selectively applied exactly MCP Deployment, Iris inventory ConfigMap and Hermes Deployment. All 3 results succeeded; no hooks, migration, prune or other resource selection. Both new MCP replicas matched image/source/module hashes; Iris new process `b27182d8-088b-46ee-9e39-4bd9c7290069` seeded inventory 5733c1c6b35d and intended SOUL. This refresh matters because installed Hermes keepalive/reconnect do not re-register cached tool schemas. All 9 smoke checks passed and exactly 1 ESP32 writer connection was observed. All 15 Deployment UIDs, 13 other Deployment specs and 4 critical DB/API/ingestor Pod states stayed unchanged. Normal release CI `bq2rq` succeeded at 20:25:23 UTC with its actual validation Pod and full logs retained.

The source-owned SOUL requests one prior-completed-night read in ordinary non-experiment SUNRISE/SUNSET work, reuses it, and preserves unavailable/negative/incomplete results. The existing Iris audience retains 23 tools; `outcome_kpi` stays admin-only and denied to Iris. No credential, audience, ACL, getter or schema change was required.

## Authorized GPT-6 Luna upgrade

The initial bounded model export was rejected by automatic approval review before process creation. Jason then explicitly authorized the selected historical payload and requested **both live Iris and qualification use GPT-6 Luna**. The original unexecuted v2 and the rejection remain retained; this is not an automatic retry.

[Official model guidance](https://developers.openai.com/api/docs/models/gpt-6-luna) and the [Responses migration guidance](https://developers.openai.com/api/docs/guides/migrate-to-responses) were fetched. The existing Hermes image already resolves the direct OpenAI URL to its Responses adapter. Its public source was captured and reviewed. Existing account model metadata returned HTTP 200 for `gpt-6-luna`; no generation or greenhouse payload was sent in that availability check.

The initial source `182691082420466dc1c5b39bae2fd84f49f38dbf` introduced the model/profile and label change. Its normal CI `verdify-platform-ci-99648` failed in validation on a stale live-model test expectation before any image build. That failure remains retained. Corrected source `56b316f3fda666b25665dfc0f791d6be6db0fa46` updates the stale audience/config test expectations and attribution comments; it supplies the qualified delivery below. The model default, API label and effective profile are unchanged from the initial proposal. It preserves `xhigh`, provider/endpoint, tool limits, credential references and upstream Hermes image. The effective profile hash is `2261f5c1a236202b3bfccedd82f1284f50ab27445767dcb09439a92a8d58b56b`, revision 2261f5c1a236. The installed ingestor prompt, playbook, inventory and experiment profile are unchanged; their old model-name prose is retained legacy text, not runtime model truth. The current README, declarative API label and canonical profile agree. Vision/embedding settings are unchanged; the vision ConfigMap only mirrors planner audit metadata.

Corrected-source workflow `verdify-platform-ci-87whm` / `410c5d3a-6bbe-4aaf-89bb-c3cecce0dd2f` passed its full source gate at 20:50:00 UTC. Its first build admission then refused the stale integrity generation 175 against current generation 176. The owning normal audit `8826…` succeeded at 20:53:01 UTC. ROOT requested one supported failed-build-only retry on that same Workflow, accepted at 20:56:12 UTC; the successful source-gate node was byte-identical before and after the retry. Its successful Pod was later garbage-collected, while the original direct terminal metadata and complete validation log remained sealed. Recovery attempt 03 stopped at a retained NotFound result before invoking the retry CLI; attempt 04 retains the actual retry and recovery capture. The final qualification combines original and recovery custody and does not claim every historical container or admission attempt succeeded.

Model source CI `verdify-platform-ci-87whm` / `410c5d3a-6bbe-4aaf-89bb-c3cecce0dd2f` completed at 2026-10-18T21:02:34Z: the full source gate, API and orchestrator builds, and candidate publication passed. The qualified API digest is `67c0705274d699d21f257e4787f2a8f7c1fcc33d152aa567a4de534e45d9dedf`. Only API was promoted from those two candidates; the orchestrator release pin was retained.

The first model-release preflight at `12e3cd6e01ee6e2e3a989ce2e78d2c9e30f09ffe` refused before mutation: the global API image transformer also changed the active winter-feasibility CronJob and winter archive Sync-hook Job, producing six changed objects. No operation was submitted from that preflight. Source commit `1f132f20eea07ab57c5d705a9bd626ac6fe72faf` adds exact-resource image pins after the global release transformer, preserving both complete winter objects at their prior API digest `75f510f52d71846197fdc1ddabead87912d8a31070439a29747e4ec36dc37624`. All 8 existing focused owning tests passed. The corrected `model-render-after-02` has exactly four changed resources and preserves the other 125 whole objects. It reuses the qualified API image built from `56b316f3fda666b25665dfc0f791d6be6db0fa46`; no image rebuild or broader selector was needed.

Dispatcher attempt 03 submitted that exact four-resource operation once, with custody in `model-rollout-02`. The original six-object render and `model-rollout/preflight-refusal.json` remain retained. Submission is distinct from acceptance: the actual terminal/runtime results below are filled only from their completed receipts. At this report-template preparation, normal CI `verdify-platform-ci-6l8w9` (Workflow UID prefix `fd2e81ec`) for the winter-preservation release was pending; this history does not claim it passed. Its eventual terminal receipt is recorded separately in final immutable custody.

Model release `1f132f20eea07ab57c5d705a9bd626ac6fe72faf` completed its exact four-resource selective operation at 2026-10-18T21:07:33Z: the Hermes profile ConfigMap, planner audit metadata in the vision ConfigMap, API Deployment and Hermes Deployment. It ran no hooks or migrations and did not prune. The subsequent actual runtime readback matched the new API source/image and model label, and Hermes Pod `4a3a563f-be72-4f50-8f2e-d5a17e64f4e3` loaded the exact GPT-6 Luna/xhigh profile `2261f5c1a236202b3bfccedd82f1284f50ab27445767dcb09439a92a8d58b56b`. MCP stayed on its accepted image and same two Pods; all 15 Deployment UIDs, 12 unrelated Deployment specs and the critical DB/ingestor states were preserved. All 9 smoke checks passed and exactly 1 device writer was observed. At report preparation, model-release CI terminal verification for revision `1f132f20eea07ab57c5d705a9bd626ac6fe72faf` is pending in this report. Its actual terminal result and the later report-CI result will be recorded separately in immutable custody; neither is claimed passed here. Source cache renewal is a fresh-process result, not live registry introspection or natural-cycle use.

## Actual bounded consumer acceptance

Two actual bounded ordinary-consumer cases passed using GPT-6 Luna/xhigh over Responses and the existing Iris audience. October 4 returned the same four complete 46-field rows as the prior installed-production custom read: two ineffective and two insufficient-evidence, none effective. October 16 returned no episodes, pending safety and NULL daytime counts. There were exactly 2 authenticated scorecard reads and 4 provider requests, using 818 output tokens in total, with no other tools dispatched or retries. The fixed structured scope statement and actual counts/NULLs passed; this is forced, bounded evidence consumption, not autonomous tool selection or a full Hermes gateway cycle.

The qualification permits exactly one actual authenticated scorecard call per selected historical night and two Responses requests per case, at most 800 + 1200 output tokens, `store:false`, no action dispatcher, 90-second process deadline and no automatic retries. It uses the shipped Iris preamble and actual public MCP schema, with strict input, output, source, image and Pod identity guards. Encrypted reasoning is retained only in memory for stateless continuation, never published. A final summary must match actual counts/dispositions/NULLs and fixed scope text; safety pass cannot become efficacy/crop-safety/complete-daytime proof.

This is bounded ordinary MCP/model consumption. It does **not** establish an autonomous natural planner cycle, a full Hermes gateway run, physical efficacy or complete daytime observation. The captured gateway API uses full configured action toolsets and has no enforced per-run read-only filter; no full gateway run or controller action was launched for qualification.

The new model has no measured natural-cycle cost baseline: no new natural cycle was launched for this qualification. Bounded request token usage is reported separately and cannot establish a plan-cycle cost change or a comparison against the 20% investigation threshold.

## Prior accepted repair and preserved database

[Installed getter 277 repair](../2026-10-18-getter-timeout-fix/README.md) remains accepted at its prior boundary. Actual October 16 returned 0 rows/pending in 11.816649 seconds. October 4 custom/generic calls returned the same 4 full 46-column rows in 11.269874/4.966761 seconds: 2 ineffective and 2 insufficient-evidence, none effective. The clone 3.174204-second result is not installed-production timing. Its 20 native full-row parity cases, 2,673 genuine clone readback relations and original failures remain preserved and were not requalified here.

The bounded database metadata read at 2026-10-18T21:09:03.568673+00:00 confirms ledger 285/max 277, the original postmaster, and unchanged getter body/definition and solar helper. This is metadata readback at that timestamp, not a claim of full data parity. The selected operations contain no migration or data rewrite.

## Graph and next execution

The prepublication native snapshot at 2026-10-18T19:47:49.343562+00:00 (source `b814f35e228fcb35c11593c47ea1e021e5fd73de`) matched 47 open issues, 93 canonical records, 143 open acceptance clauses, 26 local active edges plus HA #878 → #981 (27 total), and 24 closed predecessor records. This update preserves those contracts and relationships. Publication-bound native conformance is recorded separately after ROOT commits and updates GitHub; this report does not assert a future final MATCH.

All acceptance clauses, issue states, parents, dependencies, priorities and effort estimates remain unchanged. Original S3B 40h and 22–34h selected estimates plus at least 6h reserve are planning values, not remaining or measured effort.

1. Specify a supported observation/freshness contract and collect a prospective complete recorded daytime window. Preserve the incomplete October 16/17 history.
2. Observe intended outcome consumption in a naturally scheduled ordinary planner cycle separately from this bounded qualification.
3. Advance authentic sensing, physical efficacy and crop-safety requirements on their own evidence. No tuning, universal VPD target, OTA or experiment follows from this packet.

No additional user decision is identified by this completed bounded packet. ROOT records public evidence publication, terminal report CI and remote byte checks separately after the report commit. This report does not claim those later operations have already completed.
