# Recovery module dependency graph

```text
device-writer-reconcile ───────┐
                              ├──> planner-delivery ───────┐
firmware-control-policy ──┐    │                            │
                          ├────┴──> evidence-contracts ────┼──> runtime-release-verification
                          │                                 │
                          └─────────────────────────────────┘
```

## Ordering

1. Correct canonical cfg/readback identity before dispatcher acceptance and before firmware adds readbacks.
2. Land solar/cycle/night/replay evidence, then resource accounting, then DLI availability in one serialized migration/schema/MCP-adapter sequence.
3. Planner consumes the canonical registry and final availability/evidence adapter; evidence consumers accept planner terminal lifecycle.
4. Firmware is one final integrated module because irrigation, DLI baseline, relay ownership, heap, replay, and weekly OTA constraints make file-level parallelism unsafe; speculative anti-chatter changes remain deferred.
5. Release verification first requires credential rotation approval/evidence, then integrates reviewed modules, deploys schema/services, retires stale intent, clears the planner blocker, and performs one combined OTA with topology-aware cycling/heap gates.

There is no unresolved dependency cycle. Planner/evidence have a bidirectional runtime relationship, but schema migration order breaks implementation coupling: evidence schemas land first, then planner writes and evidence consumers read the versioned contract.
