# One field visit, one release evidence packet

All observation fields start **UNOBSERVED**. Source configuration and old photos do
not count as a present-day survey. Never fabricate a value to complete this sheet.

| Station | Record during the same visit | Enables / acceptance |
|---|---|---|
| Crop and plumbing map | Timestamped location/photo, actual species/substrate/pot, sensor ID, center/wall/south/west hose endpoints, explicit enabled/disabled/uncommissioned state | #298 → #398 → #45; #434 center climate-only, wall feed-only, center drip disabled |
| Climate and hydro | Identify south climate probe separately from south soil probes; power/bus/wiring/model, reference/neighbour comparison, hydro source and observed fault or unresolved status | #751; #368 truthful contributor state; #749 preserves 4°F spread limit and original failed packet |
| Soil/runoff/feed | Per-channel physical position, freshness, calibration method/reference, valid range, EC/pH/moisture uncertainty; missing channels stay unavailable | #398/#45 before #297 feedback/skip; flow/dose/flush commissioning before fertilizer enablement |
| CO2 | Reference NDIR model/serial/calibration, simultaneous paired measurements, predeclared tolerance and uncertainty | #51; blocked until reference exists; retain uncalibrated label |
| Seasonal care | Actual species/condition, door-screen position, night temperature/moisture/ventilation; justified no-change or reversible change | #52/#412; record any study intervention |
| Safety/physical proof | Actual binary/config identity, equipment mapping, wetting rails/admission, reset/counter evidence; final safe-state readback | #749/#641; no proof run or safety-hold release implicit in the survey |
| Lighting | Passive per-circuit solar boundary timestamps, min-on start/stop and expected reason | #386; wait for eligible natural event, do not manufacture a toggle |

## Release packet prepared alongside the survey

- Select exact reviewed firmware source and signed archived output; bind source,
  configuration, toolchain, binary hashes and signature. Reuse #1009 software/tamper
  receipts at their stated scope. Network-infra#77 closed by consolidation into
  open #555; this is not a passive device ACL acceptance receipt.
- Identify independently available recovery artifact and its actual prior recovery
  evidence. A matching filename, copied metadata or dummy-credential build is not
  qualified last-good. Preserve current operational binaries and rollback coordinates.
- Reuse #985's 154 causal YAML cases, native invariants and zero-divergence replay
  only within their original source scope. Select additional checks from actual
  source changes, rather than treating old tests as acceptance of a new binary.
- Declare pre-OTA heap/fragmentation/loop timing, reset counts (software and watchdog
  separately), bake/canary duration, controller/relay compatibility and rollback
  triggers. Bind #367/#368/#299/#434/#963 behavior to the selected release.
- Record observed HA moisture state/detail and recovery, preserving native detail.
- Keep compile/sign/verify separate from flash, password rotation, device-side
  signing and physical acceptance. No OTA/password cutover is authorized by this
  worksheet; no new ESP32 connection or node cordon is permitted.
- Writer hold remains a distinct recovery item. Do not release it to make a survey
  or release checklist appear green.

Visit date: DEFERRED — Jason answered "survey later" on 2026-10-25. Operator: Jason.
Reference CO2 meter: NONE AVAILABLE, confirmed by Jason; #51 calibration remains blocked.
Binary/recovery selection: PENDING. OTA window: NOT AUTHORIZED BY THIS PACKET.
