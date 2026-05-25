# Firmware Sprint-15 Spec: Summer Thermal-Driven Ventilation + Enthalpy-Gated Seal Override

**Status:** approved, ready to route
**Author:** iris-dev (coordinator analysis 2026-04-20)
**Routes to:** `firmware` agent — branch `firmware/sprint-15-summer-vent`
**Depends on:** none (post sprint-14 readbacks)
**Coordinator PRs expected:** `verdify_schemas/tunables.py`, `ingestor/entity_map.py`, `docs/tunable-cascade.md`

## Context

Today's data (2026-04-20) showed the greenhouse sealed for 50.7% of the 8-hour peak stress window while indoor was **6-14°F hotter than outdoor** (indoor 91.9°F peak, outdoor 77.9-81°F) and also more humid (indoor 52-66% RH vs outdoor 7-9%). Enthalpy decisively favored venting for 4 consecutive hours. Firmware chose sealing every time.

The exterior vent is on the screen door and is **open during summer** — the intake is effectively unrestricted. Fans + vent can extract full CFM (~4,900 CFM). Active cooling via outdoor-air exchange is genuinely viable when outdoor conditions favor it. The bottleneck is not physics — it is firmware logic. The state machine prefers VPD-seal over thermal-vent even when outdoor enthalpy is clearly better. That was tolerable when the vent was effectively undersized; in summer with the screen door open, the bias is actively wrong.

## Root cause from the firmware audit

`firmware/lib/greenhouse_logic.h::determine_mode()` priority order:

1. SAFETY_COOL / SAFETY_HEAT (safety rails — untouchable)
2. THERMAL_RELIEF (in-progress 90s burst — untouchable)
3. Was-sealed exit logic (continues from prior cycle)
4. **VPD seal entry** (line 236) — dominant precedence
5. Relief-cycle breaker (reactive force-vent after 3 failed seals)
6. DEHUM_VENT
7. Temperature-driven VENTILATE (line 259) — **loses to step 4 today**

The gap: `enthalpy_delta` exists in `SensorInputs` (line 45) but is **never consumed** in decision logic. `econ_block` is a binary gate, not a real enthalpy comparator. There is no logic path that says "outdoor is cooler and drier — just vent, don't bother sealing."

## Proposed firmware state machine adjustment

Add one new decision path, **ABOVE** the current VPD-seal entry, that can short-circuit sealing when outdoor conditions clearly favor venting.

### New: "thermal-driven vent preference" gate

```c
// NEW: Step 3.5 — thermal-driven vent preference (summer scenario).
// When outdoor air is substantially cooler AND drier than indoor, prefer
// VENTILATE over SEALED_MIST even if VPD is climbing. The screen-door
// intake (open in summer) makes outdoor-exchange cooling fully effective.
// Prior logic prioritized VPD-seal unconditionally; on hot dry days that
// sealed the greenhouse against its own best heat sink.
bool outdoor_data_fresh = (in.outdoor_data_age_s < sp.outdoor_staleness_max_s);
bool outdoor_cooler = (in.outdoor_temp_f < (in.temp_f - sp.vent_prefer_temp_delta_f));
bool outdoor_drier_dp = (in.outdoor_dewpoint_f < (in.dewpoint_f - sp.vent_prefer_dp_delta_f));
bool temp_above_band = (in.temp_f > (sp.temp_low + sp.temp_hysteresis));
bool vent_preferred = sp.sw_summer_vent_enabled
                     && outdoor_data_fresh
                     && outdoor_cooler
                     && outdoor_drier_dp
                     && temp_above_band;

if (vent_preferred) {
    // Skip VPD-seal path. Fall through to VENTILATE (step 7).
    vpd_wants_seal = false;
    state.override_summer_vent = true;
}
```

### Equipment behavior in this mode

When the new path triggers → VENTILATE fires at step 7:
- `vent = true` (open)
- `fan1 = true` always; `fan2 = true` if `temp > Thigh + d_cool_stage_2` (existing logic)
- `fog = false` (do not humidify while venting dry air)
- Mister OFF (do not waste water)

No new equipment paths needed — just a new gate that steers into the existing VENTILATE mode.

### Exit condition

Return to normal decision cascade (including VPD-seal) when any of:
- Outdoor data goes stale (> `outdoor_staleness_max_s`)
- Outdoor heats above indoor (delta flips)
- Outdoor dewpoint rises above indoor (humidity invasion risk)
- Temp drops below `temp_low + temp_hysteresis` (no more cooling needed)
- `sw_summer_vent_enabled = false` (operator override, e.g., winter)

All automatic — no new state to manage beyond `state.override_summer_vent` boolean for telemetry.

## New tunables (5)

| Name | Type | Default | Range | Purpose |
|---|---|---|---|---|
| `sw_summer_vent_enabled` | switch | 1 (on) | 0/1 | Master enable. Operator can disable the whole feature (winter, screen-door closed). Default ON — firmware behavior today is wrong in summer, so on-by-default + explicit opt-out is safer. |
| `vent_prefer_temp_delta_f` | num | 5.0 | 2-15 | Outdoor must be at least N°F cooler than indoor to trigger gate. Below 2 = noise; above 15 = vent rarely fires. |
| `vent_prefer_dp_delta_f` | num | 5.0 | 2-15 | Outdoor dewpoint must be at least N°F below indoor dewpoint. Guards against venting into humid outdoor. |
| `outdoor_staleness_max_s` | num | 300 | 60-1800 | Max age of outdoor reading before gate disables. 5 min default (Tempest updates every 3 min typical). |
| `summer_vent_min_runtime_s` | num | 180 | 60-600 | Reserved/no-op in current firmware. It is exposed and read back, but the gate does not consume it yet. Existing `min_vent_on_s` is the active dwell today. |

## Sensor inputs needed

Currently absent in `SensorInputs`:
- `outdoor_temp_f` (float)
- `outdoor_dewpoint_f` (float, computed from outdoor temp + RH)
- `outdoor_data_age_s` (int, seconds since last Tempest update)

Source: Tempest weather station via HA → ESPHome template sensor → firmware global. Already available in HA per the sensor wiring review; just needs wiring into the firmware `SensorInputs` struct.

## cfg_* readback sensors needed (7 total)

Per `docs/tunable-cascade.md` convention, each dispatcher-pushable tunable needs readback:
- `cfg_sw_summer_vent_enabled` (bool)
- `cfg_vent_prefer_temp_delta_f` (float)
- `cfg_vent_prefer_dp_delta_f` (float)
- `cfg_outdoor_staleness_max_s` (int)
- `cfg_summer_vent_min_runtime_s` (int)
- `cfg_outdoor_temp_f` (float, current reading — useful for troubleshooting)
- `cfg_outdoor_dewpoint_f` (float, current computed DP)

## State machine telemetry additions

In `evaluate_overrides()`:
- New override flag: `override_summer_vent = true` when the new gate is actively suppressing a sealed-mist entry. Visible via existing override_events table. Lets Iris see when summer vent is active AND how often it is steering away from her planned sealed-mist approach.

In `ControlState`:
- `bool override_summer_vent` (default false)
- No persisted timer — gate is evaluated fresh each cycle.

## Files to modify

**Firmware side:**
- `firmware/lib/greenhouse_types.h` — +3 `SensorInputs` fields, +5 `Setpoints` fields (incl. 1 switch), +1 `ControlState` bool, +1 override enum entry
- `firmware/lib/greenhouse_logic.h` — add gate in `determine_mode()` (~25 LOC); add override set in `evaluate_overrides()` (~3 LOC)
- `firmware/greenhouse/sensors.yaml` — wire Tempest temp/RH → compute dewpoint → feed to SensorInputs; add 7 new cfg_* readback sensors
- `firmware/greenhouse/controls.yaml` — include 5 new tunables in the /setpoints pull handler with clamps; update Setpoints struct construction in determine_mode call site
- `firmware/greenhouse/globals.yaml` — +5 new global vars for tunables

**Contract/schema side (route through coordinator):**
- `verdify_schemas/tunables.py` — add 4 numerics to `NUMERIC_TUNABLES`, add `sw_summer_vent_enabled` to `SWITCH_TUNABLES`
- `ingestor/entity_map.py` — add 5 entries to `SETPOINT_MAP` + 7 to `CFG_READBACK_MAP`
- `docs/tunable-cascade.md` — new rows for the 5 tunables with all 12 columns filled

**Tests:**
- Bench unit tests for the new `determine_mode()` gate (outdoor cooler/warmer, DP higher/lower, data fresh/stale, switch on/off)
- Integration test: synthetic SensorInputs matching today's 13:00 data (indoor 91°F/65%, outdoor 77°F/8%) — verify gate triggers VENTILATE not SEALED_MIST
- Drift-guard tests for 5 new tunables (every in ALL_TUNABLES has Pydantic + entity_map + tunable-cascade doc entry)

## Pre-deploy validation

**Replay first, then direct activation:**
- Validate the gate against replay data and synthetic hot-dry cases before OTA.
- Do not add a runtime alternate controller or proposal path.
- Compare replay decisions to Iris's judgment and Jason's intuition.
- If decisions match expected ("would have vented, not sealed, on all four 12-15:00 hours today"), deploy the single live firmware path with normal OTA guardrails.

**OTA deploy guardrails:**
- `firmware/artifacts/last-good.ota.bin` current before deploy
- Post-deploy sensor-health sweep (sprint-17 infrastructure) — fail = auto-rollback
- 24h observation before concluding deploy successful

## Expected impact (based on 2026-04-20 data)

If sprint-15 had been live today:
- Gate would have triggered 12:00-16:00 MDT (4 hours): outdoor 76-81°F vs indoor 82-92°F, DP massively in favor
- Firmware would VENTILATE instead of SEALED_MIST for those 4 hours
- Indoor temp peak estimate: 82-85°F (vs actual 91.9°F) — 7-10°F cooler because active heat extraction
- VPD: would rise with dry-air intake but bounded — outdoor 1.4-2.0 kPa VPD with indoor cooling to 82°F gives indoor ~65-70% RH, VPD ~1.0-1.5 kPa (within band)
- No `firmware_relief_ceiling` alerts (vs 4 today)
- Water use: ~40-60% reduction (fewer misting cycles)
- Electric: +15-25% (fans running more)
- Net cost: roughly break-even or slight savings

## Out of scope for this sprint

- Winter/shoulder-season detection heuristics (operator sets `sw_summer_vent_enabled` manually for now; auto-detect from outdoor temp ranges is a future sprint)
- Variable fan speed (fan control is binary on/off; speed modulation is a hardware sprint)
- Fog coordination during VENTILATE (fog stays off when venting; no need to adapt)
- DEHUM_VENT mode changes (already vents; unchanged)
- SAFETY_* mode changes (safety rails are immutable)

## Routing protocol

1. `firmware` agent picks up this doc on next cycle.
2. Coordinator (iris-dev / Jason) lands `verdify_schemas/tunables.py` + `ingestor/entity_map.py` changes first (schema-first rule in `CLAUDE.md`).
3. `firmware` agent lands sprint-15 code on `firmware/sprint-15-summer-vent` branch.
4. Deploy the single active firmware path under normal OTA guardrails.
5. Run 24h observation.
6. Close sprint.
