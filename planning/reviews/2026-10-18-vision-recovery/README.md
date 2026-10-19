# Vision CronJob recurrence and recovery — October 18 MDT / October 19 UTC

**Recovered and verified. #951 remains closed on its original scoped contract.** Both failed midnight attempts were real failures. The next genuine scheduled run completed both cameras and analysis after the owning provider restored service. This diagnosis submitted no sync, recovery Job, image build or provider request. The prepared manual recovery preview was never submitted.

## Cause and owning repair

`verdify-vision-29872800` (UID `f2f2640b-a800-47d5-bbf6-edbad67f3b8a`), scheduled 2026-10-19 00:00 UTC, failed at 00:00:16 with BackoffLimitExceeded. Both actual retry Pod logs report ConnectionRefusedError for both cameras and zero captured frames, before analysis. This was an unavailable authenticated provider, not a JPEG, Gemini, credential or database failure.

The endpoint remains `https://frigate.frigate.svc.cluster.local:8971`, mapped to the authenticated proxy listener 8973, with verified TLS identity `cameras.vallery.net` and the existing camera-scoped token. The port mapping is intentional. Do not change it to the unauthenticated admin API.

[Sentinel PR #333](https://github.com/jvallery/sentinel/pull/333), merged at 01:06:03 UTC as `82403efb1e9dcfebeb95ba5c265535d429781771`, documents the failed guarded cutover and restores the stopped provider from replicas 0 to 1. Its existing broker operation completed at 01:07:30; replacement Pod `91c6fee3-09f5-407f-b784-da1380d99004` became Ready at 01:09:07. This operator read that existing recovery; it did not perform or repeat it. Sentinel's ongoing archive/cutover work is separate and may introduce later planned outages.

## Actual acceptance

| Evidence | Verified result |
|---|---|
| Genuine natural Job | `verdify-vision-29872980`, UID `a79a37a9-db51-4669-89d2-97849d74921b`, CronJob controller owner UID `4b0f62d3-20f2-4e28-b841-b7f649a12ddb`, scheduled annotation 03:00:00 UTC |
| Actual container | Pod `verdify-vision-29872980-jtgdp`, UID `84a4ba67-72ce-41c6-8387-e88f87c44fbe`; exit 0, zero restarts; Job Complete at 03:00:24 |
| Image identity | `registry.vallery.net/verdifyconsultancy/verdify-ingestor@sha256:4b4994bc68c9e0966d2665d4d4da3e4e7e5d1c2dc8b2694f95db1fa629dc6000` |
| Actual capture | Both camera JPEGs saved at 03:00:03/04, 225/274 KiB as reported by the existing client; `2/2 cameras captured`, `Analysis complete` |
| Genuine analysis | `image_observations` #823/#824 at 03:00:15.539847/21.372094 UTC, model `gemini-3.1-pro-preview`, structured objects, confidences 0.80/0.85 |
| Downstream writes | Three linked crop observations per analysis, six total |
| Declared source | Current render and CronJob template equal after explicit Kubernetes defaulting; all five generated ConfigMap key hashes match source `af46d263352d6403201a4642378197e9ca00303c` |
| Failure behavior | Original failed Job and both retry logs retained; 34 focused TLS/authentication/JPEG/partial-input/analysis-exit/source-copy tests pass |
| Independent aggregate | At 03:54 UTC Verdify was Synced / Healthy at `af46d263…`; no active operation; last actual operation still completed October 18 21:07:33 UTC |

The terminated Pod's mounted file bytes and JPEG overlays were not reread. The actual successful log, real structured database rows, unchanged capture contract, mounted ConfigMap identity and current source hashes establish pipeline recovery. No new current-frame timestamp or anonymous-denial probe is claimed; original #951's fresh-frame/authentication proof remains historical. This proves the recovered pipeline at the recorded time, not continuous availability, crop-health accuracy, physical efficacy or unrelated public-ingress recovery. Aggregate Healthy does not erase retained Failed Jobs.

The live CronJob declares no `timeZone`. Its actual controller annotations include 03:00 and 06:00 UTC; the manifest expression alone must not be interpreted as UTC execution. Scheduling was not changed.

## GitHub and dependency plan

The targeted update preserves #951's closed history and original acceptance clauses, adds the current recurrence/accepted natural recovery, and refreshes #75's operational context. The later standalone ingress issue #988 enters the canonical source with its four unchanged open criteria and no parent/blocking edges. It remains open; this vision recovery does not qualify its public/authenticated/cause/recurrence requirements.

The refreshed plan has 94 canonical records and 48 open issues, with 147 open acceptance clauses. Existing 27 active native blocking edges (26 local plus Home Assistant #878 → #981) and 24 closed predecessor records remain unchanged. Original sprint estimates and remaining scientific/physical criteria are retained. The final native equality receipt is published separately after source and issue-body readback; this paragraph does not claim a future check passed.

## Delivery, custody and limits

Only the runbook, planning contracts/generated views and curated evidence change here; the production render must be byte-identical. Shared CI was coordinated with its owner: normal validation only, no image/policy publication. Full in-cluster CI completion and final remote custody are recorded separately after publication.

Whole-application sync remains prohibited. No selective sync, hook, migration, prune, Deployment restart, credential/security change, synthetic observation, failed-Job deletion or additional provider call occurred. No new user decision is required for this recovered vision incident. #988 and the existing scientific/physical backlog remain independent work.

Published evidence deliberately contains metadata, source hashes, bounded SQL and three sanitized Job logs; it contains no JPEGs, model responses, crop notes, credentials or unrelated provider inventory. Complete private diagnostic inputs remain under `/private/tmp/verdify-vision-fix-20261018`. `PRIVATE-CUSTODY.json` binds their hashes without exporting their contents. Retain original failure evidence; do not clear history merely to obtain green health.
