# Native API DeferredBatch allocation abort — September30

## Actual controller panic evidence

Root authorized one transient read-only diagnostic API client after the existing
native ingestor exposed no request hook. It sent encrypted Hello/login and one
SubscribeLogs(ERROR, dump_config=false), captured five seconds, then disconnected
in finally. No ListEntities, SubscribeStates, setters, retries, HA interruption,
reset or OTA occurred. The writer and HA consumers remained running.

The fresh DB guard at21:46:30UTC measured age59.777s, free45.234KiB and largest34KiB
on running firmware2026.9.30.1251.831dc208. Connected21:46:30.434633UTC;
finally disconnected21:46:37.339064UTC. Captured retained panic reported
Fault/IllegalInstruction, core1, PC0x4008839C, with16 backtrace addresses.

Exact831 ELF SHA256:
`0a740d3db8feb8538b511e8119e5e3ec397cf7cbb41b737d71b4775b3351b0f5`.
Raw capture SHA256:
`7dc998ce4d23e8d26cd348cf7937c2e75ac16f8961f4023a10cc9320992b16e5`.
Private raw lines and addr2line output:
`/Users/jason/Documents/Codex/verdify-panic-passive-20260930/`.

The actual stack decodes to:

```
panic_abort → esp_system_abort → abort
  → __wrap___cxa_allocate_exception → operator new
  → vector<DeferredBatch::BatchItem>::_M_realloc_append
  → DeferredBatch::add_item → schedule_message_ → send_message_smart_
  → send_sensor_state → APIServer sensor update → Sensor::publish_state
  → TemplateSensor::update → PollingComponent → Scheduler
```

This proves the captured panic traversed the batch-vector allocation/exception
abort path. PC0x4008839C is abort machinery; the generic IllegalInstruction label
does not establish an arbitrary opcode corruption cause. Which sensor/client
caused the allocation and the allocation's exact requested size are not retained.
This evidence does not attribute the fault to clock acquisition or logging.

831 and prepared969 both contain ESPHome's noinit crash handler, not an IDF flash
coredump partition. It retains PC/cause/core and up to16 PCs per core during a boot.
SubscribeLogs invokes its ERROR output and clears the next-boot magic, while
keeping the current-boot validity flag. **Deployed NONE ingestor skips SubscribeLogs**;
the earlier suggestion that it sent NONE and cleared the marker was incorrect.
Current HA exposes reset reason/uptime, no stack entity. DB read at21:44:10 found
zero esp32_logs since21:00; the diagnostic client recovered the missing raw stack.

## Source correction and limits

ESPHome2026.6.5 `api_connection.h` and `.cpp` are patched before compilation by
`firmware/build_api_batch_patch.py`, using exact upstream hashes and exact output
hashes plus generated version-header validation. Unknown/edited upstream fails
before any file is changed. This avoids copying/owning the complete API component.

Only DeferredBatch storage changes: fallible POD realloc replaces throwing vector
growth, with checked item/byte arithmetic and original geometric capacity behavior.
Dedup, events, priority-front swap, FIFO removals and empty-buffer release remain.
No new entity/client cap or306-item limit is imposed; full620-entity bursts remain
accepted while memory exists. Realloc failure retains original pending metadata.
Normal/priority scheduling failure marks that peer fatal and returnsfalse, without
allocating a log/error string. Direct-send callbacks, connection-loop phases, batch processing and iterator
finalization check that removal flag before encoding. The iterator stops and
returns before its post-loop flush;
missing states are not relabeled as delivered/completed. Peer reconnect and caller
generation gaps remain observable.

This removes the proven batch-vector exception-abort path. It does not make all
ESPHome/LWIP/encoding allocations safe, prove physical stability, identify the
sensor/client, or turn a source test into successful delivery. The two-server SNTP
acquisition correction remains unchanged. No OTA has been performed for this patch.

Focused native tests compile the actual firmware POD storage with exceptions
disabled and ASan/UBSan. They exercise620 initial and routine bursts, blocked FIFO
retention/drain, duplicate states, repeated event edges, priority order, initial and
later allocation failure with unchanged pending metadata, arithmetic ceiling, and
fail-closed source/version guards. Firmware compile and control replay are separate
required source checks; physical acceptance follows an attended source-bound OTA.
