# Greenhouse physics model — maximum crop-environment consistency while floating

**2026-06-18.** First-principles model for driving *maximum consistency and quality of
the environment the crop actually experiences*, while respecting the floating principle
(let the air roam, intervene minimally). Companion to the nature-alignment work
(`band-single-source-of-truth`, `docs/adr/0003`).

## 0. The resolution in one line

> **Float the air; constrain the plant.** Consistency is enforced on the crop's
> *physiological experience* (leaf VPD, leaf temperature, DLI, the wet→dry root cycle,
> the day/night thermoperiod), **not** on the air state. The air (T, RH) is then free to
> roam along iso-comfort manifolds, driven by free solar/thermal physics, while the
> plant's experience stays in a tight, reproducible band — **every day the same
> high-quality day, regardless of the weather.**

"Consistency" does NOT mean a flat air temperature (the plant *needs* the diurnal rhythm).
It means **day-to-day reproducibility of the plant-relevant integrals and rhythm** — the
float absorbs the weather noise so the crop's daily experience is constant.

## 1. The broader physics (the coupled system)

The greenhouse is a coupled energy + moisture + radiation system. The crop experiences the
*leaf/root surface*, not the bulk air — that distinction is the whole game.

**Energy (air + thermal mass — two lumped capacitors):**

```
C_air · dT_air/dt = τ_g·A·I_solar          (solar gain, NIR+absorbed PAR)
                  − U·A·(T_air − T_out)      (envelope conduction/infiltration)
                  − k_vent·V(t)·(T_air−T_out)(ventilation, free)
                  + Q_heat(t)                (gas/electric heat)
                  − λ·(E_fog + E_transp)     (evaporative/transpirative cooling)
                  + h_m·A_m·(T_m − T_air)     (exchange with thermal mass)
C_m   · dT_m/dt   = (solar to mass) + h_m·A_m·(T_air − T_m) − σε·(radiative loss, clear nights)
```

The thermal mass `C_m` (benches, water, structure, medium) **low-pass-filters and lags**
the solar forcing — that *is* the smooth, ~3h-lagged natural curve. It is a property of the
building, not a crop requirement.

**Moisture (vapor balance → VPD):**

```
V_air · dw/dt = E_transp + E_fog + E_mist            (sources)
              − (k_vent·V + infiltration)·(w − w_out) (exchange)
              − Condensation(T_cold_surface)          (sink)
VPD = e_s(T_air)·(1 − RH),   RH = w / w_sat(T_air)     (T↔RH coupling)
```

VPD couples T and RH: **a whole manifold of (T, RH) gives the same VPD.** Evaporative
actuators (fog/mist) are *doubly coupled* — they cool (sensible) AND humidify (latent),
moving T and VPD together.

**Radiation / light:** PAR drives photosynthesis and the **DLI** integral (∫PAR over the
photoperiod); NIR drives heat; longwave-out drives clear-night radiative cooling (frost
risk). Shade trades heat for light — a hard coupling (`−T` costs `−DLI`).

**Leaf level (what the crop feels):**

```
Leaf energy balance:  R_abs = h_conv·(T_leaf − T_air) + λ·E_leaf
   ⇒ T_leaf = T_air + (R_abs − λ·E_leaf)/h_conv
Transpiration:        E_leaf = g_s · g_bl · (e_s(T_leaf) − e_air)
```

`T_leaf ≠ T_air`: in high light with low transpiration a leaf runs several °C above air;
transpiration pulls it back. **Airflow sets `g_bl` (boundary-layer conductance)** — so fans
are a first-class actuator: they govern transpiration, the leaf-temp offset, uniformity,
and disease pressure, *without* spending heat/water. `g_s` (stomata) is a plant state,
entrained by the photoperiod.

**Root zone (bare-root Vanda):** the velamen wet→dry cycle. Roots must *dry between
waterings*; VPD too low at night → rot, too high → desiccation of exposed roots. This is an
integral/cycle constraint, not a setpoint.

## 2. Reduced-order grey-box model (what to actually run)

States `x = [T_air, T_m, w_air, DLI_accum, R_wet]`; inputs `u = [Q_heat, V_vent, E_fog,
E_mist, shade, fan]`; disturbances `d = [I_solar, T_out, w_out, wind]` (measured + **forecast**).
The equations of §1 with lumped coefficients. This predicts the *free-running* trajectory
given the solar/weather forecast — the core of anticipatory control.

**Grounding (system-ID on 21 days of 15-min history):**
- **Thermal time constant `τ = C/UA ≈ 3h`** — from the diurnal phase lag (indoor temp peak
  lags solar by ~2.6h; `tan(ψ)=ωτ` ⇒ τ≈3h). Frequency-domain estimate; robust to control noise.
- The indoor diurnal swing (8.7°F) is **damped below** outdoor (11.2°F) — control + mass
  already compress the range significantly.
- VPD relaxes faster (~1h scale) and is **actuator-dominated** (fog/vent move it quickly).
- **Modeling lesson:** a naïve time-domain regression of the *passive* model on this data
  gives **R²≈0.04** (and a nonphysical negative coupling) — because the greenhouse is
  heavily controlled, the actuators mask the physics. **Proper grey-box ID must include the
  actuator inputs (`equipment_state`) or be fit only on free-running windows.** This is a
  required build step, not optional.

## 3. The plant-comfort manifold (the corridor, in physiology space)

Define the constraint set in the crop's coordinates, **not** air coordinates:

| Variable | Constraint | Physics term |
|---|---|---|
| Leaf VPD | `[VPD_lo(φ), VPD_hi(φ)]` (tighter at night) | transpiration driver |
| Leaf temp | `[T_lo(φ), T_hi(φ)]` + **DIF** (day−night Δ) | metabolic rate, morphology |
| DLI (end of photoperiod) | `≥ DLI_target` | growth integral |
| Root cycle | wet fraction ∈ band; **dry-down completes daily** | velamen, anti-rot |
| Airflow at canopy | `≥ g_bl_min` | boundary layer, disease |

`φ` = circadian phase, **entrained by the photoperiod (dawn/dusk), not the clock** — this is
what "adapts to the solar cycle" should mean. The corridor is *wide in air space, tight in
plant space*: the air rides the iso-comfort contours (e.g., the curve of constant leaf-VPD
in (T,RH) space) freely. **That is how floating and consistency coexist.**

## 4. Actuator cost model (the optimizer's vocabulary)

Each actuator moves the state in a direction at a cost; coupling matters:

| Actuator | State effect | Cost | Key coupling |
|---|---|---|---|
| Vent | T,w → outdoor | ~free | only helps when outdoor is favorable |
| Shade | −T, −PAR | ~free | **costs DLI** (don't shade if DLI is the binding constraint) |
| Fan | +g_bl → +transpiration, −leaf-Δ, +uniformity | small elec | the "free" leaf-VPD + uniformity lever |
| Heat | +T | $$ gas/elec | raises VPD (drier) — a dehum lever too |
| Fog | −T, −VPD | water | sensible+latent coupled |
| Mist | −VPD local, +root wet | water | zone-targeted; drives the wet/dry cycle |

Cheapest-effective-first ordering (passive → vent → fan → fog → mist → heat/shade) is a
direct consequence: spend free physics before spending energy/water.

## 5. Control hierarchy (where floating lives, where consistency lives)

- **Inner loop (fast, on-chip — the FLOAT):** keep the plant variables inside the §3
  manifold; **zero actuation while inside**; at an edge, the cheapest §4 actuator nudges
  back with the minimum dose. This is the deployed firmware band + hysteresis — already
  first-principles-correct *as a corridor-keeper* (it must stop *chasing a target line*; see
  ADR0003 reconsideration below).
- **Outer loop (slow, planner → MPC — the CONSISTENCY):** using the §2 model + the
  weather/solar **forecast**, (a) shape the day's manifold from crop physiology + the
  photoperiod, and (b) schedule *anticipatory, minimal* actuation that **guarantees the
  daily integrals and rhythm** (DLI met, DIF delivered, VPD profile in band, wet/dry cycle
  completed) while absorbing weather variation. Re-solve each cycle (receding horizon).
  iris/Hermes is the seed of this layer — it should tune the *constraints and costs*, not
  push instantaneous setpoints.

The inner loop floats (weather-driven, minimum intervention); the outer loop makes the
**plant's daily experience reproducible** (consistent) by pre-acting against the forecast.
Maximum consistency + quality + floating, simultaneously.

## 6. Consistency, defined

`Quality(day) = time-in-manifold × DLI-achievement × thermoperiod-fidelity × wet/dry-cycle-completion`
`Consistency = low day-to-day variance of Quality and of the VPD/temp *profiles*` (not of the
air at any instant). Grade on **outcomes** (the above), not on distance from a fabricated
target line. The float is what makes consistency cheap: by absorbing weather into the air's
free roam, the plant's experience is held constant for near-zero energy.

## 7. Reconciliation with the current build + what to change

- `crop_band_anchors` (the corridor) and the edge-hysteresis FSM are the **right
  primitives** — keep them; reinterpret the band as the *plant tolerance manifold*,
  photoperiod-anchored.
- **ADR0003 "track/pinch the target 24/7" (`band_track_fraction`) is, from first
  principles, the opposite of floating** — it makes the controller chase a line, maximizing
  intervention to minimize a deviation metric the plant doesn't care about. The
  highest-leverage, lowest-risk, no-OTA change is to **set `band_track_fraction → 0`** (float
  within the corridor; act only at edges).
- **Re-grade on outcomes (§6)**, retiring "distance-from-target."
- Build toward MPC: (1) actuator-aware grey-box ID (§2), (2) wire the forecast into
  anticipation, (3) MPC optimizer with the §3 constraints + §4 costs, (4) planner tunes
  constraints/costs.

## 8. Sensor gaps (to close for full plant-level control)

Air sensors only see the bulk; the model degrades to proxies without:
- **Canopy/leaf IR temperature** → `T_leaf`, leaf-VPD (the true transpiration driver).
- **Canopy PAR (quantum) sensor** → real DLI (vs the broadband `solar_irradiance` proxy).
- **Canopy anemometer** → `g_bl` (boundary layer / uniformity / disease).
- **Bench/root weight or velamen-wetness** → the wet→dry cycle directly.

Until then: model `T_leaf` from the air energy balance + airflow estimate; DLI from
broadband solar × PAR fraction; treat these as estimated, not measured.

## 9. Migration path (lowest-risk first)

1. **Flip to float:** `band_track_fraction → 0`; corridor-keep, don't chase. *(Reversible, no OTA.)*
2. **Corridor from physiology + photoperiod** (dawn/dusk-anchored day/night structure).
3. **Forecast-driven anticipation** in the planner (pre-act minimally; you already have `weather_forecast`).
4. **Actuator-aware grey-box ID** → parameterize §2 from history (with `equipment_state`).
5. **MPC** with §3 constraints + §4 costs; planner tunes constraints/costs, not setpoints.
6. **Re-grade on §6 outcomes.**
7. Close the **sensor gaps** (§8) to lift from air-proxy to true leaf/root control.
