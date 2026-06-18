# Control + Graphs — Current State Snapshot (2026-06-18)

A single coherent snapshot of what is actually live across **firmware, climate
control, the pinch, and the homepage graphs**, with the device-vs-HEAD
distinction made explicit (it is the thing most easily gotten wrong). Pointers
to the authoritative deeper docs at the end.

## TL;DR
- **Device firmware = `cc1bb19` (`2026.6.16.0225`, "curve-only-fog-gates").**
  On this binary the **pinch is UNWIRED** — the controller **floats within the
  RAW `[low, high]` crop-tolerance band** and acts only at the raw edges.
- **Repo HEAD firmware = band-compliance (pinch armed/wired), STAGED not flashed.**
  OTA is Jason's gate (freeze rules: ≤1 OTA/week, 48 h bake, no open criticals).
- **`band_track_fraction` = 0.25** live (planner intent / dispatcher-pushed to
  NVS) but **inert on the running device** (the running binary ignores it).
- **Homepage graphs** show the **pinched** band (reads `band_track_fraction`
  live) + crisp event-based equipment stripes. The pinched band is the planner's
  control-**target** corridor — **NOT** the running device's actuation boundary
  (which is the wider raw band). They converge via an OTA *or* #377 (`f → 0`).

## 1. Firmware
- **Running:** `cc1bb19` / `2026.6.16.0225` (source ref `curve-only-fog-gates`),
  per `firmware/artifacts/last-good.version`. Architecture is the clean model:
  one solar-ephemeris diurnal band curve, one 8-mode supervisory FSM, one
  single-arbitration allocator, staged hysteresis, no PID. It **floats inside
  the band** (`climate_band_error()` = 0 anywhere in `[low,high]`) and
  `apply_band_track_pinch()` is present but disarmed (`band_track_fraction=0`,
  unwired) → tracking behavior byte-identical to `main` at flash time.
- **Staged at HEAD (band-compliance, epic #359 / ADR-0003 sprint):** arms
  tracking (pinch wired into `determine_mode_band_first` + `resolve_equipment`),
  symmetric stage-2 escalation, deterministic outdoor-aware bidirectional dehum
  (BC-13), fog-first wetting ladder (BC-14), deletes the legacy controller.
  **STAGED, not flashed** (`docs/reviews/band-compliance-ota-signoff-2026-06-17.md`).
- Authoritative internals spec: `docs/firmware-fsm-spec.md`.

## 2. Climate control — the escalation ladder (verified from `greenhouse_logic.h`)
Per dimension, actuators engage in this order (inner→outer = first/frequent →
last-resort/rare). On the **running** fw the engage point is the **raw** band
edge; at **HEAD** it is the **pinched** edge.
- **TEMP hot** (cool): Vent → Fan 1 → Fan 2 (`+cool_stage2_over_high_f`) → Fog
  (evap assist, VPD-gated during vent).
- **TEMP cold** (heat): Heat 1 (lower-quartile `band_heat_target_f`) → Heat 2
  (low edge).
- **VPD dry** (humidify): Fog (band edge, dwell-gated) → Misters S1/S2
  (`+fog_escalation_kpa`).
- **VPD wet** (dehum): Dehum-Vent → +Heat-1 assist (estimator + dwell) → both
  fans (`-dehum_aggressive_kpa`). Hard safety rails: `safety_min/max`,
  `vpd_min/max_safe`.
- **Cross-coupling** (why every actuator shows on both panels): Heat = cold +
  wet; Fog = hot + dry; Vent/Fans = hot + wet(dehum); Misters = dry. Temperature
  cooling out-ranks humidification (can't seal-and-mist while venting), so VPD
  excursions are *tolerated while cooling is active* — visible across the two
  panels at matching timestamps.

## 3. The pinch (`band_track_fraction`)
- **Live value 0.25** (relaxed from 0.50 → `2d5e245`), in the registry default,
  the planner prompt, the firmware globals, and pushed to device NVS.
- **Effect today: none on the device** (running `cc1bb19` ignores it). It will
  take effect only after the band-compliance OTA.
- **Direction of record is ADR-0004 (floating corridor): `band_track_fraction →
  0`** (#377, P0) — stop chasing the target, float within crop tolerance, act at
  edges. ADR-0003 ("track the target" / pinch) is **superseded** by ADR-0004.
- Net: the pinch is a transitional knob. The end-state is `f = 0` (no pinch),
  which also makes the graphed band == the raw band == the device's *current*
  actuation band.

## 4. Homepage graphs (graphs.verdify.ai `site-home`, panels 30/31/40)
- **Hero layers:** pinched Target Band (rides `v_band_curve`, reads live
  `band_track_fraction`), dashed Target centerline, Greenhouse trace, Outdoor +
  forecast, Solar (hidden axis). Band single-source-of-truth = `crop_band_anchors`
  → `fn_crop_band_value` → `mv_band_curve`/`v_band_curve` (device-truth harmonic).
- **Equipment overlay (redesigned 2026-06-18):** fixed-y wide gutter stripes,
  escalation-ordered (least-frequent furthest from centerline), ALL actuators on
  BOTH panels, consistent color; VPD dry-side **top rail** + wet-side **sub-zero
  status lane**; 3 mister circuits merged. Rendered event-based (raw
  `equipment_state`, `stepAfter`, `ELSE y_low` not NULL, `showPoints:never`,
  `fillBelowTo`) → crisp + granular + dotless; relay y-offsets `hideFrom.tooltip`.
  Full recipe + workflow + gotchas: **`docs/grafana-graph-authoring.md`**.
- **Truth caveat:** the shaded band is **planner-intent (pinched)**, not the
  running device's actuation boundary (raw, wider). This is exactly why climate
  sits outside the band with actuators idle. **To make the graph match the
  device:** flip `band_track_fraction → 0` (#377 — pinched becomes the full raw
  corridor the device actually uses) *or* OTA the band-compliance fw (device
  starts tracking the pinched band). Tracked under #371 (homepage corridor view).

## 5. Open work (GitHub `VerdifyConsultancy/verdify-platform`)
- **#359** EPIC floating-corridor (ADR-0004). **#377** [FLOAT-1, P0] `f → 0`.
  **#378** [FLOAT-2] band edges = crop tolerance. **#379** [FLOAT-3] grey-box ID
  → MPC. **#371** [BC-12] outcome grading + homepage corridor view (graph half
  largely done this session; grading metrics remain). **#365** planner objective
  = outcomes. **#369** dead-code/tunable purge.
- **Gated:** band-compliance OTA (Jason). Confirm the live device firmware
  version before relying on any pinch-wired (HEAD) behavior.

## Authoritative references
- Firmware internals: `docs/firmware-fsm-spec.md`
- Decisions: `docs/adr/0004-floating-corridor-control.md` (current),
  `docs/adr/0003-band-compliance-track-the-target.md` (superseded)
- Band SoT: `docs/band-traceability-contract.md`
- Physics model: `docs/reviews/greenhouse-physics-model-floating-control-2026-06-18.md`
- OTA sign-off: `docs/reviews/band-compliance-ota-signoff-2026-06-17.md`
- Graphs authoring: `docs/grafana-graph-authoring.md`
- Brand/colors + panel inventory: `docs/grafana-brand-system.md`, `docs/grafana-panel-catalog.md`
