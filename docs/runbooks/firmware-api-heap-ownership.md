# API heap ownership snapshots

This diagnostic adds read-only getters through the existing guarded ESPHome
2026.6.5 patch. The firmware base is the deployed `fe46633c` tree, byte-identical
in main `876efd8f`. It does not change allocation, queues, iteration, protocol,
controller decisions, locks, thresholds or the existing logging cadence.

The existing critical heap event and 15-minute `heap_profile` packet append:

| Field | Meaning |
|---|---|
| `api_peers` / `api_rm` / `api_sync` | Current API peers, marked for removal, active initial/entity iterators |
| `api_ov_n` | Allocated overflow entries |
| `api_ov_b` | Full overflow data allocation plus Entry objects; partial sends retain the full allocation |
| `api_pending_b` | Unsent overflow bytes; separate from allocated bytes |
| `api_batch_n` / `api_batch_b` | Pending deferred items / retained capacity bytes |
| `api_rx_b` | Sum of retained API receive buffer capacities |
| `api_shared_b` | Server-owned shared encoder capacity, retained across peer replacement |

The fixed stack snapshot scans at most 20 peers and eight overflow slots per
peer. It runs in the normal ESPHome main loop before the corresponding log
write. It does not read sockets, allocate memory, acquire locks, add a timer,
poll the API or consume a crash record. Existing accepted native log transport
can carry these fields without an additional client or schema change.

These are partial ownership counters. They exclude allocator bookkeeping,
connection/helper object allocations, Noise crypto, lwIP TCP/pbuf/socket memory,
Wi-Fi and non-API components. A low API buffer sum does not prove a leak in a
specific excluded owner. A missing packet proves no counter value. The existing
control-lambda timing does not measure the entire API/server task.

## Existing recovery mechanisms

The current firmware's automatic low-heap guard samples every 60 seconds. After
15 minutes uptime it calls `App.safe_reboot()` only after current free heap is
below 20 KiB for three minutes and both heater relays are off. A sample at or
above 20 KiB, or an active heater, resets the timer. Largest-block fragmentation
alone does not invoke it. No thresholds are changed here.

The native Restart button also calls `App.safe_reboot()` but does not duplicate
that heater check. The ingestor has no supported restart-button dispatch path;
do not manufacture an extra API client or generic command to invoke it.
A graceful sole-ingestor replacement uses the existing disconnect/finally path
and native API peer destruction; it can free peer buffers without a controller
reset. It cannot free the server shared buffer or guarantee recovery of Wi-Fi,
lwIP or other peers. Operational action and physical acceptance remain ROOT's.

Before a controller reboot ROOT must capture current sole-writer and authority
custody, fresh actual heater/irrigation/fertilizer/vent readbacks, valve-motion/FSM
state and full current tunable readbacks. The reboot starts relay outputs off,
cancels an interrupted weekly feed and cold-starts non-restored globals.
`restore_mode: RESTORE_DEFAULT_OFF` alone is not an unconditional OFF guarantee;
the explicit early on-boot actions are relevant. GPIO/PCA output behavior before
software initialization and actual mechanical valve position are separate
physical limits. After reboot require genuine new uptime/reset/version,
new-generation native callbacks, actual safe relay/valve readbacks and original
configuration reconciliation. Do not replay an unknown prior physical request,
reuse a stale authority worksheet or call reboot a stability proof.
