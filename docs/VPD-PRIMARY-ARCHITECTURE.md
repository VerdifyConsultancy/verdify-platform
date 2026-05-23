# VPD-Primary Control Architecture

**Status:** Historical design note. Production firmware now runs the unified
band-first controller with explicit VENTILATE mist assist and per-circuit
lighting state machines; keep this file as background for why high-VPD stress
drives the greenhouse, not as the live state-machine contract.
**Date:** 2026-04-09
**Author:** Jason Vallery + Claude Opus 4.6

## Core Insight

Dutch growers fight low VPD (too humid). Verdify fights high VPD (too dry). At 5,090 ft
with 15% outdoor RH, the humidity axis dominates plant outcomes 8 months of the year.
Temperature is mostly a VPD input, not an independent control objective.

The binary intake vent (24×24" motorized louver, open/close only) makes the vent decision
a mode switch, not a proportional knob. The state machine must explicitly model two
operational regimes: vent-open (free cooling, humidity loss) and vent-closed (humidity
retained, heat builds, evaporative cooling effective).

## State Machine: 6 VPD × 4 Thermal = 24 states

### Primary axis: VPD management (6 states)

| State | Trigger | Vent Open Behavior | Vent Closed Behavior |
|-------|---------|-------------------|---------------------|
| VPD_LOW | VPD < vpd_low | Force vent open | Brief vent pulses for dehumidification |
| VPD_IDLE | vpd_low ≤ VPD ≤ vpd_high | Nothing additional | Nothing additional |
| VPD_WATCH | VPD > vpd_high, dwell starts | Consider closing vent | Reduce HAF (future) |
| VPD_MIST | VPD > vpd_high + hyst, after WATCH | Close vent first, then mist | Mister pulse rotation |
| VPD_FOG | VPD sustained despite misting | Vent closed + fog pulse + mist | Fog pulse + mist |
| VPD_EMERGENCY | VPD > safety_vpd_max | Everything: vent closed, fog, all misters | Same |

Key change: VPD_MIST forces vent closed BEFORE engaging normal misters. Open-vent
misting wastes ~70% of water to immediate evaporation into exhaust airflow at
15% outdoor RH. Current firmware has one explicit exception: when temperature
requires `VENTILATE` but VPD remains high, bounded vent-mist assist may pulse
misters while the vent stays open because temperature is the primary objective.

### Secondary axis: thermal management (4 states)

| State | Trigger | Equipment |
|-------|---------|-----------|
| HEAT | temp < temp_low | Electric first, gas joins if sustained. Vent forced closed. |
| TEMP_IDLE | temp_low ≤ temp ≤ temp_high | Vent mode from VPD/enthalpy logic |
| COOL_FAN | temp > temp_high | Fans staged. Vent opens IF VPD allows. |
| COOL_EVAP | temp > temp_high + d_cool_stage_2, fans saturated | Vent closed + fog as evaporative cooler |

COOL_EVAP is not emergency cooling — it's the correct tool at 15% RH. Vent closed + fog
produces ~5,600 BTU/hr evaporative cooling that stays in the greenhouse.

### Conflict resolution priority

safety → VPD → thermal → cost

1. VPD_MIST and VPD_FOG force vent closed (no exceptions except safety_max)
2. COOL_FAN wants vent open — if VPD_MIST also active, VPD wins, transition to COOL_EVAP
3. HEAT forces vent closed
4. safety_max (95°F) overrides everything — vent opens, all equipment runs

## Binary Vent Oscillation Pattern (hot dry days)

```
Vent opens (thermal relief) → VPD climbs → VPD_WATCH (60s dwell)
  → VPD sustained → SEALED_MIST closes the vent immediately
  → Misters pulse (60s on / 25s gap) → VPD drops
  → Misters stop when VPD/zone demand clears; later VENTILATE/THERMAL_RELIEF opens the vent if needed
  → Cycle repeats
  
After mist_max_closed_vent_s: mandatory thermal relief opening
```

Note: `mist_vent_close_lead_s` and `mist_vent_reopen_delay_s` are reserved
ESPHome globals with readbacks, but the current firmware does not consume them.
They are not Tier 1 planner knobs until a firmware PR implements the semantics.

## Tunables (current tactical subset)

### VPD response
| # | Parameter | Range | Default |
|---|-----------|-------|---------|
| 1 | vpd_hysteresis | 0.1-0.5 kPa | 0.3 |
| 2 | vpd_watch_dwell_s | 30-120s | 60 |
| 3 | mister_engage_kpa | 1.0-1.8 | 1.6 |
| 4 | mister_all_kpa | 1.3-2.2 | 1.9 |
| 5 | mister_pulse_on_s | 30-90s | 60 |
| 6 | mister_pulse_gap_s | 10-60s | 45 |
| 7 | mister_vpd_weight | 1.0-3.0 | 1.5 |
| 8 | mister_water_budget_gal | 100-300 | 300 |

### Vent coordination (binary)
| # | Parameter | Range | Default |
|---|-----------|-------|---------|
| 9 | mist_max_closed_vent_s | 120-900s | 600 |
| 10 | vent_enthalpy_open_delta | -5 to 0 kJ/kg | -2 |
| 11 | vent_enthalpy_close_delta | 0 to +20 kJ/kg | 1 |
| 12 | vent_min_on_s | 10-300s | 60 |
| 13 | vent_min_off_s | 10-300s | 60 |

### Fog (graduated)
| # | Parameter | Range | Default |
|---|-----------|-------|---------|
| 15 | fog_pulse_on_s | 15-90s | 30 |
| 16 | fog_pulse_gap_s | 30-300s | 120 |
| 17 | fog_escalation_kpa | 0.1-0.5 | 0.4 |

### Thermal
| # | Parameter | Range | Default |
|---|-----------|-------|---------|
| 18 | d_cool_stage_2 | 2-5°F | 3 |
| 19 | bias_heat | retired/context only | 0 |
| 20 | bias_cool | retired/context only | 0 |
| 21 | min_heat_on_s | 60-300s | 120 |
| 22 | min_heat_off_s | 120-600s | 300 |

`bias_heat`, `bias_cool`, and `d_heat_stage_2` are retained for legacy
readback/fallback visibility only. Routine plans must use the current Tier 1
surface from the generated AI Tunables reference.

### Switches
| # | Parameter | Values | Default |
|---|-----------|--------|---------|
| 23 | sw_fog_enabled | 0/1 | 1 |
| 24 | sw_economiser_enabled | 0/1 | 1 |

Note: sw_fog_closes_vent removed — vent always closes during fog in the new design.

## Daily Rhythm (hot dry day example)

- **05:00 Pre-dawn** — Vent closed, DROP cooling (future). Slab releasing heat.
- **07:00 Morning ramp** — Vent opens (enthalpy favorable). Free cooling + CO2.
- **11:00 VPD ramp** — VPD_WATCH (60s dwell). Vent still open.
- **11:01 VPD_MIST** — Vent closes → misters pulse → VPD drops → vent reopens.
- **11:01-15:00** — Vent oscillates between thermal relief and sealed misting.
- **12:00-13:00 Peak** — COOL_EVAP: vent closed, fog + misters, sealed-volume cooling.
- **15:00-16:00** — Decline. Vent open more, misting less, pulse gap widens.
- **19:00 Evening** — Vent closed for humidity retention. Conservative posture.

## Implementation Phases

1. **Phase 1** — VPD_WATCH + fog graduation + vent coordination tunables
2. **Phase 2** — Thermal axis collapse (HEAT_S1/S2 → HEAT, COOL_S1/S2/S3 → COOL/COOL_EVAP)
3. **Phase 3** — DROP protocol + ADT temperature integration
4. **Phase 4** — HAF fan speed control + proportional vent (hardware)
