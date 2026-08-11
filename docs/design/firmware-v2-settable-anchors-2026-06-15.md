# Firmware-v2: settable on-chip band anchors (complete anchors-mode)

**Date:** 2026-06-15 · **Status:** building · **Execution safeguards:** firmware preflight and rollback

## Problem

The greenhouse runs firmware-v2 in **on-chip band mode** (`sw_onchip_band_enabled=true`):
`Setpoints.vpd_low/high/target` and `temp_*` come from the NVS `band_*_{sr,sm,ss,mid}`
and `zone_vpd_target_*` anchor globals via `sv2_curve` (cosine over 4 solar anchors),
ignoring the dispatcher-pushed legacy band. This is correct and offline-first.

**The gap:** those anchor globals are exposed only as `globals` + `cfg_*` readback
sensors — there is **no settable entity or service** for them (0 of 144 number
entities; `api:` has no `services:`). So the dispatcher's anchor-sync
(`ingestor/tasks/band_anchors.py`, the "anchors-mode" half of the firmware-v2
contract) has nothing to push to: `anchors_supported()` is false, anchors-mode is
**inert**, and the on-chip curve is frozen at the firmware-baked initial_values
(the wet-night `vpd mid=0.50` that is soaking the Vandas). `crop_band_anchors`
edits (the intended source of truth) never reach the device.

Two rejected hacks: poke NVS via `restore_value:no` (firmware-baked values are still
not DB-driven), or flip `sw_onchip_band_enabled=false` to ride the legacy
zone-derived band (`fn_house_vpd_control_band`, ties `house_vpd_high` to
`zone_vpd_max` — opaque; abandons offline-first). Both are band-aids.

## Architecture (the fix)

Complete the anchors-mode pipeline so **the DB band curve is the single source of
truth that syncs to the on-chip, offline-first band**:

1. **DB = source of truth.** `crop_band_anchors` (migration 168 = smooth dry-night
   curve). The curve is a cosine over 4 solar anchors; **smoothness is enforced at
   the DB layer** — see "Smooth by construction" below — so a midnight wet-dip is
   not representable. Offline fallback = firmware `globals.yaml` defaults, kept equal
   to the seeded DB curve.
2. **Firmware: one heap-safe settable surface.** Add a single ESPHome API service
   `set_band_anchor(anchor_key: string, value: float)` that writes the matching
   anchor global (NVS-persisted, `restore_value:yes`). **One service, not 56 number
   entities** — the device already carries 144 numbers + 198 cfg sensors and has a
   heap-reboot history; +56 entities (~14 KB) is unsafe. Anchor sync is **rare** (only
   on crop/profile change), so a service fits the offline-first model: steady state =
   zero device writes, on-chip curve runs WiFi-down.
3. **Dispatcher: sync DB→device via the service.** `push_to_esp32` gains a `"service"`
   etype (keeps the single device-write chokepoint → writer-lease fence + device-write
   gate still apply). `band_anchors.anchors_supported()` checks the service is exposed.
   Anchor changes are pushed via `execute_service` and confirmed by the existing
   `cfg_*` readbacks. Once every anchor is cfg-confirmed (`anchors_live`), the legacy
   band push **auto-retires** (dispatcher.py:714 already gates `temp_low/high/vpd_low/high`
   on `not anchors_live`).
4. **Config.** `VERDIFY_BAND_SOURCE=anchors` (prod overlay device-write-configmap) —
   now functional.

## Firmware ↔ dispatcher contract

- **Service:** `set_band_anchor`, variables `anchor_key: string`, `value: float`.
- **Valid keys:** exactly `band_anchors.ANCHOR_SYNC_PARAMS` (56): `band_{series}_{anchor}`
  (series ∈ temp_low/target/high, vpd_low/target/high; anchor ∈ sr/sm/ss/mid),
  `zone_vpd_target_{zone}_{anchor}` (zone ∈ center/south/west/east),
  `zone_vpd_width_{below,above}_{zone}`, `zone_priority_{zone}`,
  and the 4 window params (`wet_taper_before_sunset_min`, `dawn_boost_offset_min`,
  `midday_boost_offset_min`, `manual_override_timeout_min`). The firmware dispatch is
  generated from this exact list (parity asserted by `FIRMWARE_V2_STAGED_REG`).
- **Confirm:** each key has a `cfg_<key>` template sensor reading the global; the
  dispatcher's `anchors_confirmed()` waits on `shared.cfg_readback[key] ≈ value`.
- **Unknown key:** the service logs a warning and no-ops (forward-compat).
- **`anchors_supported()`:** true iff the device exposes the `set_band_anchor`
  user-service (cached in `shared.esp32["services"]`). Fallback to the existing
  cfg-readback heuristic stays for safety.

## Smooth by construction (the "why is it lumpy" fix)

The 4-anchor cosine is already a smooth curve *between* anchors; lumpiness came from
hand-set anchor values that dipped wet at solar-midnight. To make a wet-night
**unrepresentable**, the DB curve is authored from two meaningful knobs per series —
`night_floor` (value at solar-midnight) and `day_peak` (value at solar-noon) — with
the sunrise/sunset shoulders derived as the cosine midpoint. Migration 168 already
sets such a smooth profile; a follow-on view/helper derives the 4 stored anchors from
`{night_floor, day_peak}` so future edits stay smooth. (Firmware curve math unchanged
— low OTA risk.)

## Deploy + verify

1. Land firmware (service + smooth defaults) + dispatcher (`service` etype) on `main`
   → full CI/CD (firmware-replay-diff with intentional-divergence threshold for the
   default change, invariants, unit tests; container-publish builds the ingestor).
2. OTA from laptop (`make firmware-deploy`, owner-authorized overrides). Sensor-health
   sweep + auto-rollback gate the flash.
3. Roll the ingestor to prod (CI digest) with `VERDIFY_BAND_SOURCE=anchors`.
4. **Verify:** `cfg_band_vpd_target_mid` etc. converge to the migration-168 values; the
   device's commanded overnight VPD floor rises (RH falls); re-probe overnight
   VPD/RH across nights (target night RH ~45–55%, vent runtime up). Single-writer
   intact (ingestor replicas:1, Recreate). Rollback: `VERDIFY_BAND_SOURCE=legacy` +
   restart (on-chip curve continues from NVS), or firmware rollback to last-good.

## Out of scope (separate, later)

Per-zone arbiter→actuation wiring (B5) and south/west duty caps (R9) — the daytime
flooding fix lands first via runtime mister tunables (no OTA). The settable-anchor
service is what those later changes also build on.
