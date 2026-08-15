# Verdify Firmware Design

Status: v1.0 reference candidate, 2026-05-10.

This document describes the firmware path that is currently running the
Longmont greenhouse. The controller is deterministic and local: cloud services
and Iris can change bounded setpoints, but relay decisions are made on the
ESP32 every 5 seconds.

## Control Boundary

The ESP32 owns relay safety. It reads local sensors, direct Tempest UDP weather,
and the latest pushed setpoints, then evaluates `greenhouse_logic.h`.

The planner and ingestor own policy timing. They push crop bands and tactical
tunables through ESPHome native API numbers and switches. Firmware does not
invent day/night bands or forecast policy; when upstream setpoints are missing,
it falls back to wide safety-bounded defaults.

Crop VPD targets and house-control VPD targets are intentionally separate. The
dispatcher derives the global firmware VPD band from the median zone target so
the ESP32 controls the shared air mass, while the per-zone crop targets continue
to drive directional mister selection and zone stress response.

## State Machine

The active controller is an 8-state band-first FSM:

- `SENSOR_FAULT`: all relays off when core sensor inputs are invalid.
- `SAFETY_COOL`: emergency cooling above the safety max.
- `SAFETY_HEAT`: emergency heat below the safety min.
- `SEALED_MIST`: closed-vent humidification/misting.
- `THERMAL_RELIEF`: forced ventilation after sealed humidification runs too long.
- `VENTILATE`: open-vent cooling and air exchange.
- `DEHUM_VENT`: open-vent dehumidification when VPD is too low.
- `IDLE`: no active climate relay request.

The band-first controller path can also run a bounded vent mist assist: if the greenhouse is hot
enough to ventilate but VPD remains too high, the controller may pulse misters
while the vent remains open. This is explicit in the band-first control state and ESPHome mister loop and is
not the older "open-vent misting is impossible" invariant.

During high solar load, band-first controller can enter `VENTILATE` before the upper temperature
edge is crossed. The hard-coded feed-forward is deliberately small: Tempest
solar radiation at or above 500 W/m2 from 10:00-17:00 local lowers the cooling
entry point by 1°F, never below `temp_low + 1°F`, and is disabled when outdoor
air is cold enough to trigger the cold-vent guard or VPD is already high.

Night and shoulder-period dehumidification is edge-based: band-first controller enters
`DEHUM_VENT` as soon as VPD falls below `vpd_low` and normally exits after
recovering above `vpd_low + hysteresis`. If dehumidifying drives VPD above
`vpd_high`, that dry overshoot exits immediately even when the dwell gate is
enabled. Cooling demand then uses `VENTILATE` with vent mist assist; otherwise
the controller may seal for bounded mist recovery. When outdoor air is cold
enough to trigger the cold-vent guard, entry remains conservative at `vpd_low -
hysteresis`.

## Setpoint Contract

Every planner/operator-controlled value should have:

1. A schema/registry definition with bounds and ownership.
2. A dispatcher route in `ingestor/entity_map.py`.
3. An ESPHome number or switch in `greenhouse/tunables.yaml`.
4. A global consumed by `greenhouse/controls.yaml`.
5. A `cfg_*` readback in `greenhouse/sensors.yaml`.
6. A `CFG_READBACK_MAP` entry so `setpoint_snapshot` confirms delivery.

Values arrive through direct ESPHome API pushes. The removed HTTP `/setpoints`
poller is intentionally not part of the v1.0 runtime because it held buffers
and sockets on an ESP32 that was already close to heap limits.

`mister_engage_kpa` is the global S1 mister threshold. It is separate from
`vpd_high`, which is the house mode-control ceiling; zone misters may also fire
when an individual zone exceeds its crop VPD target.

## Policy Snapshot Consumers (tranche 2, #586)

Every read of a policy wire-schema field is runtime-gated on the atomic
policy engine (`lib/policy_vector.h`):

- The control tick captures ONE `PolicySnapshot` at the top of each loop and
  reads every field through the local `pol()`/`polb()` lambdas in
  `greenhouse/controls.yaml`.
- Readback (`cfg_*`), tunable entity state, and diagnostic display lambdas use
  `verdify_policy::policy_read()`/`policy_read_b()` — the same gate against
  the engine's active policy, valid outside the tick because everything runs
  on the ESPHome main loop.
- Engine inactive (no armed experiment manifest): every gated read returns the
  legacy global — bit-identical to pre-Lane-E firmware. Engine armed: every
  gated read returns the ACTIVE policy value, so `cfg_*` readbacks are
  device-confirmed truth for what the control path actually consumes.
- `set_action` writers in `greenhouse/tunables.yaml` stay on the legacy
  globals: they are the write path being demoted (Lane C #584/#597 demotes
  planner writers host-side to proposals while an experiment is armed; a
  write that still lands only touches the legacy global, which no armed
  consumer reads).

`firmware/policy_consumer_manifest.json`
(`scripts/gen-policy-consumer-manifest.py`) maps every `id(<global>)` access
of the 48 wire fields and is CI-ENFORCING: an unmigrated, non-allowlisted
read site fails the gate. The only allowlisted legacy accesses are the
writers above and the boot-time NVS repair reads in `greenhouse.yaml`
(read-check-rewrite of the legacy store itself).

## Recovery Image (#586, audit §8.10 step 3)

`firmware/greenhouse-recovery.yaml` builds the RECOVERY image: the identical
proven control logic with `-DPOLICY_ENGINE_RECOVERY`, which

- fail-closes every policy actuation service (`policy_*` calls reject with
  `recovery_image` before touching the staging arena),
- compiles the journal resume of active policy / pending slots / armed
  manifest out of `boot_init` (ROM baseline held unconditionally; generation
  high-water and the conservative water rule carry forward, and continued
  journal writes converge the journal on ROM-baseline state so a later
  full-image flash cannot silently resume the aborted experiment),
- keeps the generated vector/baseline schema, so the `Policy Identity`
  sensor still echoes `<schema>|<generation>|-|-|recovery`.

Consumers are therefore permanently legacy in a recovery flash. Build with
`make firmware-recovery-check`; host proof runs as `make
test-policy-recovery` (part of `test-firmware` and `ci-local.sh`) against a
journal fixture holding a live experiment.

## Safety Layers

Relay output is constrained by several independent layers:

- Plausibility checks reject NaN, infinity, and impossible sensor values.
- `validate_setpoints()` clamps corrupt or inverted bands before use.
- Min on/off timers prevent relay chatter.
- Safety states preempt normal dwell gates.
- Occupancy blocks moisture-producing relays.
- Readbacks confirm setpoint delivery and alert on drift.
- Non-safety heat is suppressed while vent/fan air exchange is physically
  active, and heat2 is invalid unless heat1 is available or physically held on.

## Observability

The firmware publishes:

- `diagnostics.firmware_version`, uptime, reset reason, Wi-Fi RSSI, heap, and
  heap fragmentation metrics.
- Active probe count so stale zone probes do not silently bias averages.
- Controller timers and mode reason for relay RCA.
- Override events for safety/constraint decisions that alter planner intent.
- Per-zone and per-relay counters for daily runtime and cycle audits.

## Deployment Contract

Firmware changes require:

- `make lint`
- `make test`
- `make test-firmware`
- `make firmware-invariants`
- `make firmware-check`
- Replay diff against the merge base with zero unapproved divergence.
- Post-OTA `sensor-health` before promoting rollback artifacts.

Accepted OTAs archive `firmware.elf`, `firmware.bin`, `firmware.ota.bin`,
`firmware.map`, hashes, source SHA, and the `addr2line` command under
`firmware/artifacts/<fw_version>/`. `last-good.ota.bin` is only updated after
the ESP32 reports the expected version and sensor-health passes.
