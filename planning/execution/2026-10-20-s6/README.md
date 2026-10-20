# S6 execution — October 20, 2026

Planning PR #1014 merged at `0af7f213`. The dated 62-open-issue audit remains the
baseline; this checkpoint accounts for all **61 currently open issues**, including
new #1021/#1022. The six original bounded packets were delivered; no alternate
was added. Whole-issue acceptance is recorded separately.

| Issue | Delivered result | Remaining acceptance |
| --- | --- | --- |
| #994 | Closed: exact original DB PVC adopted/protected; DB pod/spec unchanged. | None. #1015 and agents#5175. |
| #1006 | Closed: Git-generated tokenless routing, no Kubernetes grants; natural hour on the same two pods with zero restarts/forbidden logs. | None. #1016 and agents#5179. |
| #1002 | Closed: 21 approved native shares, public graphs/PNGs, classic SQL/datasource 401s, curated-view-only 15s login. | None. Other legacy boards require SSO; original null Cost Today data is preserved. |
| #1008 | Deployed: maxima/422, fixed per-camera cache and bounded refresh, origin rate/in-flight limits. | Edge cached-URL throttling/public TTL discrepancy and Turnstile custody/decision. |
| #297 | Deployed: pure evaluator/fixtures on exact-live-base backport; scheduled unavailable/uncommissioned posture. | Actual measured-target/source adapter and alert lifecycle; #45 gates physical skip/shrink. No operating alarm or actuation claimed. |
| #1007 | 24h discovery packet finished with explicit attribution gap. | #1010 identity/audience evidence and network-infra#752 cutover; no token issued. |

Final full sync at `7dd6dcb3` succeeded across 134 resources/tasks, with no selector
and pruning off; Argo is Synced + Healthy. The 285-entry migration ledger matches
the source. Source/digest-bound smoke passed 9/9 and the separate device monitor
found exactly one ESP32 writer. The new ingestor completed 300.829 real seconds
with zero restarts/schema errors; running source hashes match the frozen backport.
DB pod and DB/Grafana PVC UIDs/specs are unchanged. Final cache replicas span two
eligible workers; admin SSO remains available.

The natural 19:05 publisher run completed, emitted 421 pages, passed clean and
promoted public-output guards, completed normal S3 custody, and its public success
and served manifest hashes match. Real Cloudflare Home/Climate/Forecast/Operations
pages contain native links, including seven Weather embeds. All 21 public metadata
links and single-panel filters pass with time controls; Home interactive range
changes and Climate were observed in the browser. Home/Climate/monthly-economics/
Weather PNGs were inspected. Post-cutover classic query, bearer-token bypass,
datasource and classic dashboard paths return 401; unknown shares return 404.

The live login selects only 50 curated views, with zero other application reads
or writes and both 15-second timeouts. Public native queries ignore caller SQL.
The application burst proof uses 50 unique ignored query values to force edge
misses: 42x200/8x429 in 4.036s, while both frontends remain 200. Literal same-URL
requests are edge-cache-served; that is not claimed as edge-rate acceptance.

Two rollout problems were corrected without skipping guards: the first migration
hook refused unregistered 278 before workload rollout; #1027 binds exact bytes and
unchanged native fingerprints, with verified rollback and 68 cluster tests passed
(18 fixture skips). Two pre-build dependency-collection failures are retained in
operator custody. The later cache rollout was stopped only after a proven spread
deadlock; #1028 honors NoSchedule taints and #1024 counts replicas per revision.
The two obsolete generated ConfigMaps were retired only after no-pod-reference
checks and recovery copies with exact UID/version guards. The first raw DELETE
ignored its query-level dry-run and retired the authorized target during validation;
the second used body-level dry-run with verified UID/version preservation. No
PVC/data pruning occurred.

The file provider routes a ClusterIP. Cache affinity is per edge pod, so a browser
can reach either pod-local cache; verification requires a HIT matching an observed
cached variant, not a guaranteed first-two-request MISS/HIT. Weather observed
MISS/MISS/HIT; the final receipt retains every attempt.

`receipts.json` indexes sanitized evidence under `evidence/`. Raw logs, ConfigMap
recovery copies and screenshots remain in operator custody. No credentials,
private addresses, camera pixels, OTA, dosing or irrigation action are included.
