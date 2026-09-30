# Native initial-stream gap observer, 2026-09-30

## Small source correction

Reconnect startup already requests the primary initial state subscription.
Gap backfill now registers a removable observer on that existing stream without
another SubscribeStates request. Pinned aioesphomeapi44.24.2 registration is
synchronous; the startup send and observer registration have no intervening
await, so initial responses cannot be delivered before the observer is installed.
Manual/daily explicit bursts retain their existing request and C1 invalidation.

Gap evidence still uses only newly received, enumerated canonical keys, original
receive timestamps and exact client/generation fences. Prior equipment caches
are ignored. Missing, conflicting, invalid, stale-generation or late states
cannot claim a complete snapshot. Cancellation removes the observer and writes
no completed gap. Accepted rows never infer continuity across the missing interval.

ESPHome2026.6.5 ignores an extra subscription while its initial/list iterator is
active; after that iterator finishes, another subscription replays all620entities.
Thus the redundant request does not prove a duplicate burst happened in this
incident, but removing it eliminates unnecessary replay work without narrowing
the observation contract. This is not a claim to have corrected TaskWDT or the
physical heap root cause. No firmware dependency or device policy is changed.

## Bounded live diagnostic, root-owned pause

HA Greenhouse integration01KRB9DE2C6RD77GCWRQWCJM4E remains loaded/enabled.
It carries the firmware's Main/Grow Lutron service bridge and family exterior
lighting override proxies; it was not disabled. Root preserved ingestor state,
paused only that client and restored its exact baselinefaae1b44 image.

- Actual old pod removed20:47:03.869071Z; replica restored20:50:03.972398Z.
- Baseline native client connected20:50:21.223617512Z.
-42 HA polls every10seconds20:46:13.550–20:53:09.119Z; zero query errors.
- All four actual/proxy greenhouse light switches stayed on and available.
- Controller uptime1264.256→1684.261seconds, with no observed reboot.

Independent free-heap updates (KiB):

| Phase | Samples |
|---|---|
| Before pause |52.457,40.680|
| Ingestor absent, HA retained |53.348,48.656,53.336|
| Baseline client restored |29.563,45.711,42.969|

The HA largest-block20KiB/minimum12.246KiB states were unchanged, with old update
timestamps: these are carried values, not new independent native observations.
The lower post-restore free sample and ingestor keepalive loss are associated,
not proof of the exact allocator cause. This three-minute test is not a bake,
physical qualification, or permission to permanently remove a consumer. Other
possible clients are not excluded by the available read-only surface.

Exact831/5bc compiled API sources are byte-identical. Five low-cadence telemetry
entities are the only45-line firmware difference. Runtime component capability
off/experiment empty excludes its31-second periodic replay; log levelNONE
excludes ingestor log subscription as this incident's explanation. Native send
backpressure queues up to8 heap-allocated overflow packets per connection at
1390-byte batch MTU, plus TCPbuffers and deferred-entity vectors. There is no live
per-client queue/allocation telemetry establishing which allocation caused failure.

Private exact evidence: `/Users/jason/Documents/Codex/verdify-heap-transport-20260930`.
Comparison SHA256:`1fe675eedd541db2d00f492a15bac3870b5f6fa1e0c45feadf4e9927322c21d4`.
