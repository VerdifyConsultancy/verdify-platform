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
