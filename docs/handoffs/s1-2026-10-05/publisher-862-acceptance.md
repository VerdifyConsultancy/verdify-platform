# Publisher #862 acceptance — 2026-10-06 00:47 UTC

The optimized publisher meets the bounded live acceptance: three consecutive **natural** ten-minute runs completed in **410, 420 and 410 seconds**. [Exact receipt](evidence/publisher81-three-natural-cadence-acceptance.json) binds natural CronJob/Job/Pod UIDs, actual imageIDs, container exit/timestamps, all phase timings, original log hashes and public readback.

| Scheduled UTC | Actual init | Actual main | Actual total | Scan count | Source sync | Build | Upload |
|---|---:|---:|---:|---:|---:|---:|---:|
| 00:20 | 106s | 302s | 410s | 1 | 10s | 277s | 14s |
| 00:30 | 109s | 308s | 420s | 1 | 11s | 281s | 15s |
| 00:40 | 101s | 304s | 410s | 1 | 10s | 280s | 14s |

Actual totals include container transitions from Pod creation through main completion. Logged phase sums are measured inside the shell and need not equal Kubernetes container durations. Both schedule intervals are exactly600s, with no dropped tick in this bounded observation.

## Source and trust

Packaged source `81d76a4c3d35a925ebd2438d89099d1f657b3f8f` includes the single canonical warm binding scan and equivalent necessary-token prefilter for three expensive nonfinite matcher branches. Candidate/recovery guards, final root/tree attestation, decoding/media/canonical checks and the other regex branches remain enforced. Focused guard tests and1320 differential matcher cases qualified the change; [PR954](https://github.com/VerdifyConsultancy/verdify-platform/pull/954) and the [full Linux source81 validation](evidence/final81-linux-validation.json) passed. [Source/build provenance](evidence/final81-all-eight-builds-and-source-promotion.json) records exact source/tree/Dockerfiles and registered pipeline.

Every observed init and publisher container actually ran `registry.vallery.net/verdifyconsultancy/verdify-lab-publisher-k3s@sha256:3a678ee015274bd9429a7c2b71361a9ecf7bd96f974fa291d203f731f67d5b52`. All three natural jobs started after the full-sync fence `2026-10-06T00:15:39Z` at revision `6429acbb2c79f784e3978e9c74a1c8719d25076c`. [Actual release/hooks/smoke receipt](evidence/final81-live-release-6429.json) separately proves adoption.

At00:47:10 UTC the third run's public success timestamp was00:46:45, within that run's main-container interval. `served_public_sha256` and `last_success.public_sha256` both equal `sha256:74c60316a081fcdbe36ec2dd9440e9be0f7e3f93ca58da328a25a004558c4508`; `fresh_until_utc=01:06:45` is exactly1200s after success. The published planning page retains its explicit unqualified physical hold. Physical outcome, experiment and causal eligibility are not established by faster publication.

The schedule remains `*/10 * * * *`, concurrency remains `Forbid`, starting deadline remains30s and freshness remains1200s. No manual Job, schedule/TTL adjustment, storage mutation or guard bypass was used.

## Recovery and limits

The prior publisher digest `f2e08c6d79c9c3a9f36f95d0612a07e32b027caad9ad8406a9b87084424ba4b0` and release coordinates remain in the [promotion/rollback receipt](evidence/final81-all-eight-builds-and-source-promotion.json) and [prior pin receipt](evidence/final-c8-publisher-pin-receipt.json). Recovery uses a reviewed digest-only release promotion and full no-prune/no-selector sync; this acceptance made no recovery mutation.

Earlier source37/c8/f2 natural totals651,577,627,610 and653s remain historical over-budget evidence. The old-image00:10 failed run is outside this sync fence and is not credited. Some earlier retired Pods lost logs to kubelet/Job GC, so no cause is assigned to those missing logs. These three optimized runs were streamed from init/main start, all six streams exited0, and full originals are retained with hashes before GC.

This proves three consecutive qualifying natural runs and current fresh publication, not a long-term percentile/SLO guarantee. Source81 policy continuity, #371 physical reader adoption and sprint completion remain independent. #862 can close on this bounded acceptance; the sprint remains active.
