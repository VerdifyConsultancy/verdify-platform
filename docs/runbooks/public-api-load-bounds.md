# Public API load bounds (#1008)

List endpoints for recent/per-crop observations and crop events accept 1–500 rows; per-crop health accepts 1–365 days. FastAPI rejects invalid bounds before DB acquisition and publishes the maxima in OpenAPI.

The existing fixed 1080px authenticated source-frame reader remains unchanged. A 20-second in-process cache is keyed only by the two allowed camera identities because legacy `h` never changes the actual upstream frame. Each process retains at most two 8MiB frames and two in-flight fetch tasks. Concurrent callers share one task per camera; caller cancellation/timeouts cannot free a still-running upstream thread and start duplicates. Failed refreshes never relabel an expired frame current. `X-Camera-Cache` reports a fresh in-process hit; edge/browser cache hits are a separate layer. Two API replicas can each fetch one frame per camera, so this is a bounded per-process cache, not a claimed cluster-wide single fetch.

The canonical HTTP route definitions compile through the tokenless file-provider generator. API rate limits use socket-peer IP (`ipStrategy.depth=0`), not CF-Connecting-IP or X-Forwarded-For. Public clients therefore share the tier-1 proxy peer bucket. This deliberately bounds origin load without assuming trustworthy end-client forwarding; it is not a per-person fairness guarantee. Limits are per edge replica/peer: API 20 requests/s, burst30; camera paths 2/s, burst6. Each edge replica admits at most 12 API requests in flight globally. The public Service has two proxy replicas; do not report these as a distributed global quota.

Verify the invalid/oversize query boundary and OpenAPI maxima, one valid frame plus an in-process hit on each intended API replica, generic upstream-failure behavior and a bounded 429 burst on an **invalid camera identity**. That negative path never calls the NVR or DB. Do not use a camera-frame burst as a load test against the family's security NVR.

Cloudflare edge/WAF policy and Turnstile Secret custody remain owner follow-through. This source packet does not change DNS, Cloudflare policy or credentials, and does not claim whole-issue acceptance for those unresolved clauses.

Successful upstream frame refreshes emit `public_camera_upstream_refresh` with only the fixed camera identity and byte count. Compare two same-Pod TTL requests (including an ignored query parameter) against one refresh log entry and MISS/HIT headers. The provider deliberately disables access logs; no provider logging, credential or camera settings are changed.
