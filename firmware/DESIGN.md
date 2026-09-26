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
