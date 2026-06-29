# Firmware v2 — Implementation Contract (Appendix B, 2026-06-10)

Binding contract for the v2 build. Parent: `firmware-v2-simplification-2026-06-10.md`.
Principle: **offline-first** — after one anchor push, the ESP32 enforces the full
diurnal program indefinitely with zero network (on-chip solar ephemeris +
NVS-persisted band anchors). The dispatcher becomes a *table-sync + audit*
channel, not a control-loop dependency.

## B1. On-chip solar engine (pure C++, shared with tests)

New in `greenhouse_logic.h` (NOAA solar-position approximation, float-only):

```c
struct SolarTimes { int sunrise_min; int solar_noon_min; int sunset_min; };
SolarTimes compute_solar_times(int day_of_year, int year, float lat_deg, float lon_deg, int utc_offset_min);
float solar_phase(int now_minute, const SolarTimes& st);  // [0,4): 0=SR 1=SM 2=SS 3=solar-midnight
```

- Greenhouse constants: lat 40.167, lon −105.102 (Longmont CO). `utc_offset_min`
  comes from the time component each cycle (DST-correct by construction).
- Phase mapping: day half SR→SM→SS spans [0,2]; night half SS→midnight→nextSR
  spans [2,4). Solar-midnight ≈ midpoint(SS, SR+24h).
- Lives in the pure header so ESP32, unit tests, and the replay harness share
  the exact same math (replay derives day-of-year/minute from row timestamps).
- ESPHome `sun:` component is NOT used — one implementation, replay-testable.

## B2. Band curve engine (deterministic, NVS-persisted anchors)

```c
struct BandAnchors { float sr, sm, ss, mid; };          // value at SR, solar-noon, SS, solar-midnight
float band_value_at_phase(const BandAnchors& a, float phase);  // cosine interp between anchors
```

Served-band parameterization (compact: 36 anchor values + 12 widths ≈ 48 new
tunables, each `restore_value: yes` → survives reboot offline):

| Series | Params |
|---|---|
| House temp low / target / high curves (explicit — asymmetry matters: tight midday ceiling, loose night floor) | 12 anchors |
| House VPD low / target / high curves | 12 anchors |
| Per-zone VPD target curve ×4 (center/south/west/east) | 16 |
| Per-zone VPD half-widths (below, above) ×4 | 8 |
| Zone priority rank ×4 (`zone_priority_{zone}`, 1=highest) | 4 |

Anchor data (deterministic, from the researched envelopes + owner Vanda table +
§3.1 house reconciliation; SR / SM / SS / solar-midnight order):

- House temp low **60 76 66 60**, target **66 84 73 64**, high **72 86 80 70** °F
- House VPD low **0.40 0.60 0.45 0.42**, target **0.60 1.05 0.60 0.50**, high **0.90 1.40 0.90 0.75** kPa
- Zone VPD targets: center **0.60 1.05 0.60 0.50** (w −0.20/+0.35); south **0.85 1.18 0.95 0.75** (−0.18/+0.22); west **0.60 1.10 0.70 0.57** (−0.16/+0.22); east **0.80 1.22 0.90 0.74** (−0.15/+0.23)
- Zone priority: center=1, south=2, west=3, east=4 (Vanda>Cannabis>Lime>Pepper)

Each cycle `controls.yaml` computes: `temp_target = curve(phase)`,
`temp_low/high = target ∓ widths` and writes them into the **existing**
`Setpoints.temp_low/temp_high/vpd_low/vpd_high` fields → `determine_mode_band_first`
is consumed UNCHANGED. The dispatcher **stops pushing** temp_low/high/vpd_low/high
(they become read-only readbacks of the computed band); it pushes **anchors**
(rarely — only when crop profiles change). min/target/max are reconstructible at
every instant: `min = target − below`, `max = target + above`.

## B3. Per-zone VPD sub-FSM + priority arbiter (pure C++)

```c
enum ZoneId { ZONE_CENTER, ZONE_SOUTH, ZONE_WEST, ZONE_EAST, ZONE_COUNT };
struct ZoneBand { float target, low, high; };
struct ZoneWetIntent { bool wants_wet; float urgency; const char* reason; };
ZoneWetIntent zone_wet_intent(float zone_vpd, const ZoneBand&, /*shared rails*/);
// urgency = normalized delta: (vpd - target) / (high - target), 0 when below target
int arbitrate_wet_zone(const ZoneWetIntent intents[ZONE_COUNT], const int priority_rank[ZONE_COUNT], ...);
```

- Center has no dedicated VPD sensor yet (HW-1 gap) → uses house average as proxy.
- EAST has no mister relay → its intent is tracked/telemetered but actuation maps
  through the existing west/center adjacency factor.
- The arbiter replaces the ad-hoc stress score + `east_adjacency_boost` ordering
  in the controls.yaml mister machine: explicit rank wins; the existing fairness
  watchdog, SAF-4 duty caps, and daily-volume ceiling stay as hard bounds.
- Temp-vs-VPD arbitration becomes **normalized** (owner's "equality statement"):
  errors are divided by their band half-width before lexicographic comparison;
  the larger normalized delta picks the leading axis.

## B4. Strip list → solar-derived replacements

| Deleted (fields + tunables + paths) | Replacement |
|---|---|
| `night_start_hour`/`night_end_hour` (ENV-2 clock window) | night = `phase >= 2.0` (generic: never heat-to-chase-humidity at night — rule kept, Vanda framing gone) |
| `dusk_cutoff_hour` + `sw_dusk_cutoff_enabled` clock rail | `wet_taper_before_sunset_min` (default 120): routine wetting blocked when `minutes_to_sunset < taper` or `phase >= 2` |
| `fog_window_start/end` fixed hours | fog window = day phase `[0,2)` minus taper |
| `direct_wet_stress_*` (4 fields) + `fog_stress_window_*` (3 fields) | ONE rule: stress wetting past taper allowed while `vpd > band_high` + dew margin, never past sunset (`phase < 2`) |
| CYC-4 micropulse path (6 fields, dedicated timer/lockout in controls.yaml) | DELETED. Night emergency = `vpd > band_high` (night band edges govern) via normal SEALED_MIST under existing interlocks |
| `dawn_rehydrate_start_hour/minute`, `midday_drench_hour/minute` fixed clocks | `dawn_boost_offset_min` (from SR), `midday_boost_offset_min` (from SM); windows/cadence knobs kept (generic zone-boost, planner-tunable) |
| dispatcher-pushed `temp_low/high`, `vpd_low/high` as control inputs | computed on-chip from anchors (B2) |

KEPT unchanged: all safety rails, relay min-on/off + Heat2 latch + fan lead-lag,
SEALED_MIST/THERMAL_RELIEF/backoff machinery, dwell gate, summer-vent gate
(generic outdoor-advantage), R2-3 dry override, vpd_min_safe rescue, occupancy,
dew-margin gates, SAF-4 duty caps, FRT-6 feed hold, SAF-1 degraded fallback.

## B5. Buttons (controls.yaml + globals)

- Deadline latches (epoch-ms deadlines, not bare bools): `manual_fans_until_ms`,
  `manual_fog_until_ms`, `vent_bypass_until_ms`. Tunable
  `manual_override_timeout_min` (default 10, cfg readback).
- FANS button (momentary toggle): latch → **both fans ON + vent OPEN** for the
  timeout, applied after the mode table with force semantics (min-off bypass,
  like the proven fog-pulse `force_on` pattern). Re-press → clear latch.
- HUMID button: same, **fogger only**.
- VENT-BYPASS button: while latched, manual-fans runs with **vent CLOSED**
  (winter house-air pull). Fixes the inverted `vent_lock` (which killed fans).
- Precedence: latches sit ABOVE all automation/dwell/fog-safety gates and BELOW
  nothing except the re-press/timeout. (Owner spec: supersede everything.)
  SENSOR_FAULT relay-lock does not block manual fans/vent (human present,
  air movement is safe); it continues to block automated wetting.
- New unit tests + invariant: a manual latch must produce its relay set on the
  very next resolve cycle regardless of mode.

## B6. New telemetry (the “evidence” surface)

Published sensors → ingestor → DB: `solar_sunrise_min`, `solar_noon_min`,
`solar_sunset_min`, `solar_phase`; `house_temp_target/delta`,
`house_vpd_target/delta`; per-zone `zone_vpd_target_{c,s,w,e}`,
`zone_vpd_delta_{c,s,w,e}`, `zone_wet_granted` (which zone the arbiter chose +
rank); `band_source` ("onchip_curve"). Plus cfg_* readbacks for every new
tunable (CI: no-new-fire-and-forget).

## B7. Dispatcher / DB contract

- `crop_band_anchors` table (canonical): `(crop_type, growth_stage, season,
  series ENUM[temp_target,vpd_target], anchor ENUM[sr,sm,ss,mid], value)` +
  width columns per (crop, series). Rows for vanda (owner table), cannabis-veg,
  lime, pepper, house (Vanda-anchored reconciliation §3.1).
- Dispatcher: computes ephemeris (astral) for audit + emits per-zone bands into
  `setpoint_snapshot` (new `zone`,`band_role`,`target_value` columns) every
  cycle FOR SCORING; pushes anchor tunables only on change. Compliance views
  re-keyed to solar phase.
- crops: cannabis(south), lime(west) activated; zone targets re-pointed
  (`fn_zone_vpd_targets`: south→cannabis, west→lime, east→pepper); season fn
  un-hardcoded.

## B8. Gate plan

Replay WILL intentionally diverge (that is the point). Per freeze rule 8:
PR carries replay-diff output + explicit `REPLAY_DIFF_THRESHOLD_PCT` override +
this contract as the documented rationale + invariant suite (updated #21/#24 →
solar-based night rules) + unit-test delta. OTA preflight gates remain hard:
no open critical alerts, ≤1 OTA/week, bake satisfied, sensor-health post-OTA
with auto-rollback to `last-good.ota.bin` (2026.5.17). Services to restart
post-merge: `verdify-ingestor`, `verdify-mcp` (entity_map + schema additions).
