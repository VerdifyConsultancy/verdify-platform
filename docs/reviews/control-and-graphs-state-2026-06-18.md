# Control + Graphs — Current State Snapshot (2026-06-18)

A single coherent snapshot of what is actually live across **firmware, climate
control, the pinch, and the homepage graphs**. The device firmware was verified
from telemetry (`diagnostics.firmware_version`), not from the rollback-floor file
(`last-good.version`, which deliberately lags during the 48 h bake — that trap
caused a wrong "cc1bb19" claim earlier in the session; corrected here).

## TL;DR
- **Device firmware = `2026.6.17.2042.dcc6078` (band-compliance), OTA'd 2026-06-17
  ~20:43 MDT** (verified: 351 diagnostics rows/6h). The **pinch is WIRED** — the
  controller **tracks the pinched band toward the target** (`band_track_fraction`
  is live and effective).
- **`band_track_fraction = 0.25`** live on device (planner-pushed, confirmed in
  `setpoint_snapshot`). So the device **tracks the pinched band**; the homepage
  graphs' pinched band IS (modulo arbitration) the device's control band.
- **Firmware delta `dcc6078 → main HEAD` = one file, `globals.yaml`**: the
  `band_track_fraction` *cold-start default* 0.30→0.25 (+ a comment). With
  `restore_value:no` and the dispatcher pushing 0.25 every cycle, that is
  **runtime-moot** (affects only the first ~1 cycle after a cold boot). So the
  device is **behaviorally aligned with main**; **no OTA is required**. The
  `dcc6078` 48 h bake runs until ~2026-06-19 20:43 MDT; re-flashing now would
  interrupt it for a cosmetic change — don't. The globals default folds into the
  next real firmware OTA.
- **`last-good` (rollback floor) = `cc1bb19` (2026.6.16)** — the prior baked
  binary, retained until `dcc6078` finishes its 48 h bake. NOT the running fw.

## 1. Firmware
- **Running:** `dcc6078` = the full band-compliance sprint (18 commits): pinch
  armed/wired into `determine_mode_band_first` + `resolve_equipment`
  (`firmware/lib/greenhouse_logic.h` ~1326/1338/1350/1672/2255), symmetric
  stage-2 escalation, deterministic outdoor-aware bidirectional dehum (BC-13),
  fog-first wetting ladder (BC-14), legacy controller deleted. Architecture: one
  solar-ephemeris diurnal band curve, one 8-mode FSM, one single-arbitration
  allocator, staged hysteresis, no PID. OTA receipts:
  `docs/reviews/band-compliance-ota-signoff-2026-06-17.md`; deploy procedure +
  the false-rollback gotcha in project memory `firmware-ota-from-laptop-2026-06-17`.
- **At HEAD:** identical except the `globals.yaml` cold-start default (runtime-moot).
- Internals spec: `docs/firmware-fsm-spec.md`.

## 2. Climate control — the escalation ladder (verified from `greenhouse_logic.h`)
Per dimension, actuators engage in order (inner→outer = first/frequent →
last-resort/rare); on the running fw the engage point is the **pinched** band edge.
- **TEMP hot** (cool): Vent → Fan 1 → Fan 2 (`+cool_stage2_over_high_f`) → Fog.
- **TEMP cold** (heat): Heat 1 (lower-quartile `band_heat_target_f`) → Heat 2.
- **VPD dry** (humidify): Fog (band edge, dwell-gated) → Misters S1/S2 (`+fog_escalation_kpa`).
- **VPD wet** (dehum): Dehum-Vent → +Heat-1 assist (estimator+dwell) → both fans.
  Hard rails: `safety_min/max`, `vpd_min/max_safe`.
- **Cross-coupling:** Heat = cold+wet; Fog = hot+dry; Vent/Fans = hot+wet(dehum);
  Misters = dry. **Temperature cooling out-ranks humidification** (can't seal-and-
  mist while venting), so VPD excursions are *tolerated while cooling is active* —
  visible across the two panels at matching timestamps. This (not a wider raw
  band) is why VPD can sit above the band with misters idle.

## 3. The pinch (`band_track_fraction`)
- **Live value 0.25, effective on the device** (dcc6078 wires it). Relaxed from
  0.50 → 0.25 this session (`2d5e245`).
- **Direction of record is ADR-0004 (floating corridor): `band_track_fraction →
  0`** (#377, P0) — float within crop tolerance, act at edges. ADR-0003 ("track
  the target" / pinch) is **superseded** by ADR-0004. So the pinch is a
  transitional knob; the planned end-state is `f = 0` (no pinch = full crop
  corridor). Until #377 is decided, live = `f = 0.25` (pinch), which is what the
  device tracks and the graphs show.

## 4. Homepage graphs (graphs.verdify.ai `site-home`, panels 30/31/40)
- **Hero layers:** pinched Target Band (rides `v_band_curve`, reads live
  `band_track_fraction`), dashed Target centerline, Greenhouse trace, Outdoor +
  forecast, Solar (hidden axis). Band SoT = `crop_band_anchors` →
  `fn_crop_band_value` → `mv_band_curve`/`v_band_curve`.
- **Equipment overlay (redesigned 2026-06-18):** fixed-y wide gutter stripes,
  escalation-ordered (least-frequent furthest), ALL actuators on BOTH panels,
  consistent color; VPD dry-side **top rail** + wet-side **sub-zero status lane**;
  3 mister circuits merged. Event-based render (raw `equipment_state`, `stepAfter`,
  `ELSE y_low` not NULL, `showPoints:never`, `fillBelowTo`, relay y-offsets
  `hideFrom.tooltip`) → crisp, granular, dotless. Recipe + gotchas:
  **`docs/grafana-graph-authoring.md`**.
- **Truth:** because the device runs band-compliance @ pinch 0.25, **the graphed
  pinched band IS the device's control band** (the actuators try to hold it).
  VPD-above-band-idle = the cooling-priority arbitration above, not a wider
  tolerance. If #377 flips `f → 0`, the graph (reading `band_track_fraction` live)
  auto-widens to the full crop-tolerance corridor and the device floats it.

## 5. End-to-end alignment vs `main` (2026-06-18)
- **Git:** clean + pushed (`3c4b808`). **Pods:** all app digests = main-latest
  (`overlays/prod`; api/mcp/ingestor/planner/setpoint/lab). **Firmware:** device
  `dcc6078` = main behaviorally (HEAD delta runtime-moot) → no OTA needed.
  **Docs:** current. **ArgoCD:** reconcile 2 minor items (HA-gap-backfill CronJob,
  migrate Job-hook). **Planner drift:** open critical `planner_tunable_range_drift`
  — active/future plans still carry retired `fog_stress_*` / `direct_wet_stress_*`
  tunables (registry retired them); deactivate those plan rows to clear it.

## Authoritative references
- Firmware internals: `docs/firmware-fsm-spec.md`
- Decisions: `docs/adr/0004-floating-corridor-control.md` (current),
  `docs/adr/0003-band-compliance-track-the-target.md` (superseded)
- Band SoT: `docs/band-traceability-contract.md`
- OTA: `docs/reviews/band-compliance-ota-signoff-2026-06-17.md` + memory
  `firmware-ota-from-laptop-2026-06-17`
- Graphs authoring: `docs/grafana-graph-authoring.md`;
  brand/inventory: `docs/grafana-brand-system.md`, `docs/grafana-panel-catalog.md`
