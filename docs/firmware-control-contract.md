# Firmware Control Contract

Status: active audit, 2026-04-27.

This document defines how a firmware value is allowed to enter the ESP32
control loop. The goal is that greenhouse policy comes from the database,
planner, crop profile, or operator configuration. Firmware literals are allowed
only for fallback safety rails, physics/calibration constants, unit conversions,
or invariant clamps.

## End-to-End Tunable Path

Every planner/operator tunable must have this chain:

| Layer | Contract |
|---|---|
| Schema | Name appears in `verdify_schemas/tunables.py::ALL_TUNABLES` |
| Registry | Row in `verdify_schemas/tunable_registry.py::REGISTRY` with owner, range, default, ESP object id, cfg readback |
| Database | Written to `setpoint_plan` and/or `setpoint_changes`; latest value visible through MCP `get_setpoints` |
| Dispatcher | `ingestor/entity_map.py::SETPOINT_MAP` maps canonical name to ESPHome Number/Switch object id |
| ESPHome config | `firmware/greenhouse/tunables.yaml` exposes the Number/Switch and stores into a global |
| Firmware ingest | ESPHome native API number/switch callbacks store globals; `controls.yaml` reads them into `Setpoints` and `validate_setpoints()` clamps corrupt values |
| Control model | `Setpoints` in `firmware/lib/greenhouse_types.h` receives the global value, not a literal |
| Readback | `firmware/greenhouse/sensors.yaml` cfg sensor and `CFG_READBACK_MAP` let alerting confirm the value landed |

MCP `set_tunable` now gates on `PLANNER_PUSHABLE_REG`, which is intentionally
equal to the canonical planner-policy surface. Non-policy rows are visible
context only; they are not planner write targets. Safety rails and operator/fallback
values remain outside planner control.

## Passive full-48 configuration observations

All 48 canonical policy fields expose a diagnostic sensor sampled every 30 seconds
from the firmware's current consumed configuration. Writable Switch object IDs are
separate from these sensor routes: ESPHome suppresses unchanged switch callbacks,
so a switch alone cannot support successive passive full-48 observations.
`sw_direct_wet_gate_enabled`, `sw_fog_closes_vent`, and `sw_mister_closes_vent`
therefore use distinct `cfg_*` sensors with the same policy getter as their switches.
The sensors return unavailable until configuration is ready; they do not set values.

Legacy switch slugs remain ordinary ingestor readback aliases for firmware
compatibility. C1 capture accepts only the registry's canonical periodic sensor
routes, with its existing generation, timestamp, source and stage fences. Neither
cached replays nor device toggles qualify as fresh passive evidence. This source
contract does not claim deployed full-48 epochs; those require native observations
after the matching firmware and consumer rollout.

## Crop-agnostic boundary

The shipped firmware (`firmware/lib/*.h`, `firmware/greenhouse/*.yaml`) knows
sensors, relays, thresholds, setpoints, and numeric bands **as numbers only** —
never crops. The sole crop knowledge on-chip is the served band, expressed as four
plain floats (`BandAnchors{sr,sm,ss,mid}`, `greenhouse_solar.h`). Crop strategy is
isolated **above** firmware: `band_defaults.yaml` (zone→crop map) → DB
`crop_band_anchors` → dispatcher anchor-sync → NVS anchors. The guard
`tests/test_firmware_crop_agnostic_guard.py` fails CI if any crop name appears in
non-comment firmware code/config. (L2 #344 AC5.)

## Disconnected / standalone operation (72 hours)

Firmware is **offline-first**: it computes the solar ephemeris on-chip and
reconstructs the band from NVS anchors, so the full diurnal program and every
safety rail keep enforcing for **≥72 hours with zero network**. A dispatcher
anchor-sync is an *update*, not a control-loop dependency; with no time source at
all it degrades to a fixed-day fallback phase rather than mis-gating. Full
mechanism, degradation list, and the offline-determinism tests are in
`docs/firmware-fsm-spec.md` §9. (L2 #344 AC3.)

> The authoritative FSM / relay-transition / safety-rail / bands-and-hysteresis /
> compliance specification is **`docs/firmware-fsm-spec.md`**.

## Ownership Classes

| Class | Examples | Planner access |
|---|---|---|
| Crop band | `temp_low`, `temp_high`, `vpd_low`, `vpd_high` | Read-only in routine plans; explicit temporary override only |
| Planner policy | mist/fog timing, vent dwell, heat hysteresis, relief/latch knobs, grow-light thresholds | Pushable through `set_tunable` / `set_plan` |
| Operator/site constants | `site_pressure_hpa`, safety rail values | Not planner-pushable unless registry says so |
| Firmware fallback rails | `validate_setpoints()` clamps, sensor plausibility limits | Firmware-owned; used when upstream data is corrupt or absent |
| Physics/calibration | Magnus dewpoint constants, lux ADC conversion, VPD formula constants | Documented constants; not planner policy |

## 2026-04-27 Audit Fixes

The audit found live `Setpoints` fields that were already modeled in C++ but
still assigned as literals in `controls.yaml`. They are now fully routed:

| Canonical param | Previous literal | Current source |
|---|---:|---|
| `heat_hysteresis` | `1.0f` | `heat_hysteresis_f` global + ESPHome Number + cfg readback |
| `max_relief_cycles` | `3` | `max_relief_cycles` global + ESPHome Number + cfg readback |
| `dehum_aggressive_kpa` | `0.6f` | `dehum_aggressive_kpa` global + ESPHome Number + cfg readback |
| `vent_latch_timeout_ms` | `1800000u` | `vent_latch_timeout_ms` global + ESPHome Number + cfg readback |
| `safety_max_seal_margin_f` | `5.0f` | `safety_max_seal_margin_f` global + ESPHome Number + cfg readback |
| `econ_heat_margin_f` | `5.0f` | `econ_heat_margin_f` global + ESPHome Number + cfg readback |
| `gl_lux_hysteresis` | global-only | ESPHome Number + cfg readback + registry |

Drift guards now fail if `Setpoints setpts = {...}` contains numeric policy
literals, if a dispatcher route lacks a registry row, or if MCP stops using
`PLANNER_PUSHABLE_REG` for `set_tunable`.

## 2026-05-11 Traceability Audit Update

The current tactical planner surface is limited to tunables with a demonstrated
control-loop effect and a readback route. These exposed globals are intentionally
not planner-pushable until firmware implements their semantics:

`sw_mister_closes_vent` now means "block normal mister pulses while the vent is
physically open." The explicit `VENTILATE` vent-mist assist path bypasses that
interlock so hot/dry cooling can add bounded moisture without closing the vent.

| Canonical param | Current status |
|---|---|
| `mist_vent_close_lead_s` | ESPHome number + cfg readback exists, but firmware does not consume it |
| `mist_vent_reopen_delay_s` | ESPHome number + cfg readback exists, but firmware does not consume it |
| `summer_vent_min_runtime_s` | `Setpoints` field exists and is clamped/read back, but the summer-vent gate does not use it |
| `mister_on_s`, `mister_off_s`, `mister_all_on_s`, `mister_all_off_s`, `mister_max_runtime_min` | Deprecated legacy duty-cycle values; current pulse controller uses `mister_pulse_*`, `mister_all_kpa`, and `mister_water_budget_gal` |

## 2026-05-12 Control Invariants

The live controller must now satisfy these cross-layer invariants:

- `DEHUM_VENT` cannot remain active after VPD crosses above `vpd_high`. The
  firmware exits dehumidification immediately, seeding humidification readiness
  so VENTILATE can use vent-mist assist or SEALED_MIST can recover dry stress.
  In addition (#410), a HOLD-flavor `DEHUM_VENT` (estimator
  `vent_plus_heat_hold`, behind `dehum_vent_hold_enabled`) exits when the
  measured temp falls below the heat-demand line
  (`band_heat_target_f + heat_hysteresis`) and may not re-enter until temp
  recovers `GH_DEHUM_HOLD_REENTRY_F` (1.0 °F) above that line (the
  `vpd_min_safe` rescue is exempt and arms the hold-reheat on the same cycle).
- Non-safety heat cannot overlap physical vent/fan air exchange. Relay min-on
  timers may hold fans/vent briefly, but heat is suppressed until that air
  exchange clears. Sanctioned exception: the estimator-gated `DEHUM_VENT`
  heat1 co-run (BC-13, extended by the #410 hold flavor, where heat1 — the
  ELECTRIC stage — reheats up to the served `temp_target` while venting;
  `heat2`, the gas stage, never participates).
- `heat2` cannot run unless `heat1` is available or physically held on. The
  executor and replay invariant suite both treat heat2-without-heat1 as a
  staging fault.
- The dispatcher keeps the firmware house VPD band at least 0.55 kPa wide by
  relaxing the low edge when crop/zone targets would otherwise create chatter.
- During live VPD-high or near-edge `VENTILATE` stress with healthy dew-point
  margin, dispatcher clamps planner moisture thresholds back near the active
  `vpd_high` band. Firmware is expected to cool through `VENTILATE` while
  moisture assist carries the VPD correction; planner policy should tune
  intensity, not delay correction until far above the active band.

## Removed HTTP Poller

The earlier HTTP `/setpoints` poller is intentionally absent from v1.0. It
created avoidable heap pressure on the ESP32 and duplicated the native ESPHome
API path. Current delivery is direct number/switch push plus `cfg_*` readback
confirmation into `setpoint_snapshot`.

## Remaining Firmware Literals

These are still intentional constants or future tuning candidates:

| Category | Examples | Recommendation |
|---|---|---|
| Sensor plausibility and stale sentinels | temp/RH/VPD plausible ranges, stale outdoor age sentinel | Keep firmware-owned unless field data shows false trips |
| Physics and unit conversions | Magnus constants, F/C conversion, ADC/lux scaling, PPFD/DLI conversion | Keep as documented calibration constants |
| Controller shape constants | band interior fraction `0.25`, VPD hysteresis cap fraction `0.33`, cold outdoor margin `10°F`, minimum band widths | Promote to planner tunables only after replay shows the current defaults are the limiting factor |
| Irrigation weather skip constants | `0.6 kPa` VPD, `35°F` outdoor temp | Candidate for a future irrigation tuning PR |
| Mister local timing heuristics | idle holdoff/fairness windows | Candidate if post-deploy telemetry shows zone starvation or cycling |

The next hardening step is a generated registry-derived ESPHome map so
`SETPOINT_MAP`, `CFG_READBACK_MAP`, and MCP allowlists stop being hand-maintained.

### Lighting cutoff and manual disable precedence (#386)

A solar or fixed-hour cutoff removes automatic lighting demand, but an already
on circuit completes its configured minimum-on interval. At the exact elapsed
minimum the decision is OFF with `outside_window`; low lux or occupancy cannot
restart it outside the window. An external relay OFF is current state and is
never replaced with a cached automatic ON at the cutoff.

Setting the existing per-circuit `auto_enabled` flag false requests OFF with
`auto_disabled` immediately, including during minimum-on dwell. That explicit
disable takes precedence over ordinary cycling protection. Invalid controller
time still forces both relays OFF in the existing wrapper before evaluation.
These source rules do not establish actual per-circuit transitions, DLI or
lighting efficiency; those require separate native runtime evidence.
