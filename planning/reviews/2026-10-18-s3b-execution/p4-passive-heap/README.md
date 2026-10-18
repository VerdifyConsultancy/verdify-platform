# P4 — passive heap/reset and rollback custody

One bounded read-only capture completed at 15:21:47–48 UTC on October 18. Database Pod UID and all container states/restarts were unchanged. The 2-second statement limits, read-only transaction and rollback are bound in the [capture receipt](capture-receipt.json), [exact SQL](query.sql) and [complete original output](original-output.jsonl). 80 diagnostic rows were returned; the separately bounded heap-profile/reset-log queries returned none. No retry or wider scan followed.

The observed window is **13:58:53–15:20:41 UTC**. All 80 rows report firmware `2026.10.2.0637.620a218a-wifi-bound` and the last boot reason `Software reset`; uptime increases without a sampled decrease, from 1,382,839 to 1,387,699 seconds. These are 80 samples of the boot reason, not 80 software resets, and do not establish an all-history watchdog count.

Current free heap is **63.21–63.25 kB**, largest available contiguous block **54–60 kB**. The misnamed DB column `heap_bytes` stores the firmware's `gh_free_heap_kb()` value; source binds the unit and no second conversion is applied. All 78 complete pairs meet existing 32/16 kB floors; two missing largest-block readings remain unknown. The 2.336 kB lifetime minimum is a historical watermark. No 15-minute timing/API profile packet was returned, so actual loop timing, worst-case load and ownership counters remain unmeasured. This gives no causal basis for a heap rewrite or a release PASS. [Full diagnosis and source hashes](diagnosis.json).

## Shared #390/#963 custody and observation matrix

| Surface | Measured or retained | Remaining boundary |
|---|---|---|
| Running firmware | Version in 80 DB samples and current source interpretation | Live flash hash/build provenance not read; no candidate delivery, timing or physical release proof |
| Retained matching-version binary | 1,106,304 bytes, SHA256 `0f84ea9a06a8c96928d3675361515aa8fa1ab39528d256353babb59717bd9176`; copied metadata names clean source `620a218a`, deployed October 2 | Matching version archive/provenance directory absent beside this local copy; version/filename/mtime do not re-establish bake or actual rollback recovery |
| Prior local rollback copies | Three 1,105,552-byte copies of SHA256 `d0e793abf5355b1bffff890a1d1f7aac349d88bdb7476ae225258cf37d8bfca6`, metadata version `2026.10.1.1315.e31889bd-wifi-bound` | No inference that an older binary meets current topology/configuration; local copies are not new independent failure domains |
| Next source/compile | Prior #985 compiler/causal/replay receipts remain reusable | Dummy-credential outputs are NONDEPLOYABLE; no operational candidate is built or promoted here |
| HA moisture state / runtime | Delivered source and ingestor compatibility retained | Actual HA length-error recovery, exact deployed new binary and recovery canary are not observed in this packet |

[Exact local paths, artifact hashes and allowlisted metadata](known-local-artifact-custody.json) preserve practical recovery custody. No binary bytes, credential values or encrypted archive contents are published. Existing encrypted custody was left intact; it was not decrypted for this bounded pass. Absence is stated only for the known inspected local copy, not every possible archive.

P4's bounded passive diagnosis/custody packet is complete. **#428 remains open:** representative candidate load/timing/clean-runtime and actual last-good recovery acceptance are not established. #390/#963 physical/runtime criteria also remain open. No new ESP32 session, stress, reset, device write, OTA, artifact promotion, database mutation, credential or shared infrastructure change occurred.
