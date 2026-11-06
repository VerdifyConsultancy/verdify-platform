# ADR-0004 — Floating-corridor control: float within crop tolerance, act at the edges

**Status:** Accepted (2026-06-18). **Supersedes ADR-0003** (band-compliance / track-the-target).
**Basis:** `docs/reviews/greenhouse-physics-model-floating-control-2026-06-18.md` (first-principles
physics model) + the 2026-06-18 nature-alignment data (`band-single-source-of-truth`).

## Context

ADR-0003 set the regime to **track the smooth diurnal target curve 24/7**, with "cost is
explicitly NOT a driver" and "narrow the deadband toward target" (#362/BC-3). A
first-principles review showed this is backwards:

- The plant has a **tolerance envelope**, not an instantaneous setpoint. It is equally
  healthy anywhere inside its envelope, so any energy/water spent moving the climate *within*
  tolerance is pure waste.
- The smooth, ~3h-lagged natural diurnal curve is **the solar forcing low-pass-filtered by the
  greenhouse thermal mass (τ≈3h, measured)** — it emerges for free if you *don't* fight it.
  Chasing a target line spends energy to fight the thermal mass (we measured the old curve
  running targets ~4°F hot every morning and declining while nature was still warming).
- "Distance from a target line" is not a plant-health metric.

We do **not** have leaf-level sensors (leaf VPD/temp, quantum PAR/DLI, root weight). But
**air VPD is the industry-standard plant-stress proxy** and we measure it (zone-resolved),
plus air temp, broadband solar, and equipment state — enough to implement floating well.

## Decision

1. **Float the air within the crop's tolerance corridor** — the band `low/high` edges *are*
   the control band. Inside the corridor: **zero actuation**. At an edge: the **cheapest
   effective actuator** nudges back with the minimum dose. `band_track_fraction → 0`.
2. **The target line is a grading/centering reference, NOT a control objective.** The corridor
   (band width) is set to the **crop's real tolerance**, not a tight chase-band.
3. **Cost (energy + water + actuator wear) IS a first-class driver** — minimize intervention
   subject to staying inside the corridor. (Reverses ADR-0003 "cost is not a driver.")
4. **Constrain on what we can measure:** air VPD + air temp (zone-resolved) as the corridor
   axes; a **DLI proxy** from broadband solar; **DIF** from day/night air temp; the wet→dry
   cycle from mister duty + soil moisture. These are the achievable proxy for plant
   physiology; leaf-level sensing (ADR future) is the ceiling, not a prerequisite.
5. **Grade on outcomes**, not target-distance: `time-in-corridor × DLI-achieved ×
   DIF-delivered × wet/dry-completion − (energy + water + cycling)`.
6. **Consistency = day-to-day reproducibility of the crop's experience**, not a flat air
   temperature (the plant needs the diurnal rhythm). The float absorbs weather noise; an
   anticipatory layer guarantees the daily integrals.
7. **Anticipate with the forecast.** The planner pre-acts minimally against `weather_forecast`;
   it tunes the corridor **constraints and costs**, not instantaneous setpoints. Evolve toward
   **MPC** once an actuator-aware grey-box model is identified.

## What carries over from ADR-0003 (still correct)

- The band **single source of truth** (`band_defaults.yaml` → `crop_band_anchors` → device via
  the dispatcher → dashboard); zero device/DB divergence (achieved 2026-06-18, 0.02°F).
- The **nature-aligned anchor shape** (peak at the ~3h thermal lag; smooth, photoperiod-derived).
- The **edge-acting hysteresis FSM** — correct *as a corridor-keeper*; it must stop *chasing a
  target line*.
- The **bidirectional dehum selector** (vent-vs-heat by outdoor moisture) and **fog-first
  wetting** — these *are* cheapest-actuator-first. (#410 adds a flag-gated **held-temp
  vent candidate** to the selector — `vent_plus_heat_hold`, ADR-0003 §6.4 addendum:
  vent while heat1 holds the current temp, duty-cycled off a measured-temp floor exit
  at the heat-demand line. Selector-compatible with floating: it fires only on a
  too-wet excursion at the corridor edge and holds the *measured* temp, not a target
  line; `dehum_vent_hold_enabled` ships OFF.)
- **Anti-chatter / dwell**, **sensor-input integrity**, and **dead-code purge** — all still
  needed (more so: floating acts at edges, so edge cleanliness + sensor trust matter).
- **Single-arbitration FSM** (no dual PID).

## What this reverses (was ADR-0003)

- The pinch / target-centered tracking (`band_track_fraction>0`, narrow-deadband-toward-target).
- "Cost is not a driver" → cost **is** a driver (minimum intervention).
- The band-grade / target-distance metric (the peak-at-target tent) → **outcome grading**.
- "Planner objective = band compliance" → planner objective = **outcomes + efficiency within
  the corridor**.

## Consequences

GitHub replan under the *Greenhouse Control Optimization* milestone: epic #359 reframed to
floating; #362 (pinch) closed as superseded; #365/#371 (planner-objective / metric) reframed
to outcomes; the done band-single-source/dehum/fog/fan2 issues closed; new issues for the
float flip, corridor-width, outcome grading, DLI-deficit lighting, forecast anticipation, and
actuator-aware ID → MPC. Migration is sequenced lowest-risk-first; step 1 (`band_track_fraction
→ 0`) is a no-OTA, reversible planner push.

## Amendment: the night dry-out is an energy-accepted floor (2026-11-05, Jason, #1365)

Jason, 2026-11-05 (first-hand, recorded in `docs/design/dashboard/decisions.md`): "the overnight humidity needs to
be dryer than perhaps the vanda guidance ... make sure we set a target that ensures the greenhouse drys out
overnight", and "drier outdoor air is best, and then of course it's colder air so we have to heat that air, so it's
still energy, but accepted." This amends Decision 3 (cost is a first-class driver) for night VPD only:

1. **The night VPD floor is a hard goal, not a cost trade-off.** The floor is `max(served vpd_low, 0.74 kPa)`
   (`night_dryout_floor_kpa` in `firmware/lib/greenhouse_logic.h`). The W5 report's 0.50 kPa floor was not adopted.
   The goal is a nightly dry-out: at least 4 h at or above the floor between 00:00 and 06:00 America/Denver, so the
   roots and the floor dry out every night.
2. **Energy is spent for it, within a cap.** The lever is outdoor-air vent pulses with electric heat1 reheat, only
   when the outdoor air is confirmed drier (outdoor dewpoint at least `night_dryout_dp_margin_f` below indoor from a
   fresh Tempest reading) and never chilling the house below the temperature band floor. heat2 (gas) never runs for
   humidity. The nightly energy is capped by the tunable `night_dryout_kwh_cap`, which defaults higher than the
   report's 5 kWh because the target is drier (10 kWh at release). Shipped in firmware 2026.11.6.0938.36c7630c (#1385),
   switched by `sw_night_dryout_enabled`.
3. **Heat-only humidity chasing stays blocked** (ENV-2): heating a closed house to lift VPD underdelivers, because
   the indoor dewpoint follows the temperature (W5 report, section E).
4. **Graded nightly.** `daily_band_compliance` carries `night_dryout_graded_min`, `night_dryout_min` and
   `night_dried_out` (migration 305), next to the served-band time in band of migration 290. Scoring against an
   achievable envelope as well as the served band is a follow-up.
5. **Not changed:** the corridor stays a float everywhere else, and minimum intervention still governs daytime and
   temperature. Dry winter nights (VPD above the band) are accepted for now and rechecked in December.

The door screen-window is closed for the winter (2026-11-05), so nights from then are closed-door; treat that as a
confounder in any before/after comparison.
