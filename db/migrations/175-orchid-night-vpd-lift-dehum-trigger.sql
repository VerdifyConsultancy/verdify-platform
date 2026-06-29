-- 175-orchid-night-vpd-lift-dehum-trigger.sql
--
-- LIVE ORCHID WET-NIGHT FIX — advance the dry-night ramp migration 174 deferred.
--
-- Root cause (forensically confirmed 2026-06-16, ~06:45 MDT, orchids RH 84% / VPD
-- 0.39 with the device parked IDLE for 2 h):
--   * The device's band IS landing (gh_house_vpd_target=0.713 reported live), the
--     VPD reading is trusted (sensor_degraded=0), the economiser is not blocking
--     (econ_block=false), cold_dehum_allowed=true. So nothing structural blocks
--     DEHUM_VENT.
--   * The band-first FSM enters DEHUM_VENT only when vpd < (vpd_low - hysteresis).
--     House vpd_target had a MIDNIGHT DIP: sr/ss=0.90 but mid=0.70, so vpd_low =
--     target-0.18 dipped to 0.52 at midnight. With band_vpd_hysteresis clamped to
--     0.33*width (~0.142), the deep-night dehum trigger collapsed to ~0.40 kPa —
--     right at the live VPD (0.39). The controller sat in the hysteresis deadband
--     and let the orchids ride at RH ~84% all night (Vanda rot risk).
--   * outdoor 62 °F / 35 % RH (dewpoint ~34 °F) → venting dries fast and self-
--     limits at the temp floor (cold_dehum_allowed). The drying capacity exists;
--     the band simply never asked for it.
--
-- Fix: lift the night-side house vpd_target so vpd_low (the dehum equilibrium the
-- device dries UP toward) lands at ~0.77 kPa = RH ~70 % — a healthy Vanda night,
-- a decisive ~0.25 kPa above the live VPD so DEHUM_VENT engages and holds.
--   sr 0.90→0.92, ss 0.90→0.95, mid 0.70→0.95  (sm 1.10 day peak unchanged).
-- Equal-width edges preserved (174 architecture): vpd_low = target-0.18,
-- vpd_high = target+0.25 → vpd_high_mid 1.20 fog-ceiling caps over-dry at RH ~53 %.
--
-- crop_band_anchors is the single source of truth: live on the graph + synced to
-- the ESP32 via the dispatcher (anchors mode) within one reconcile (~5 min), no
-- OTA, fully reversible. Data-only, non-self-transactional.

-- 1. Lift the night-side VPD target (remove the midnight dip + advance the ramp).
UPDATE crop_band_anchors SET value = 0.92, updated_at = now()
 WHERE crop_type='house' AND series='vpd_target' AND anchor='sr';
UPDATE crop_band_anchors SET value = 0.95, updated_at = now()
 WHERE crop_type='house' AND series='vpd_target' AND anchor='ss';
UPDATE crop_band_anchors SET value = 0.95, updated_at = now()
 WHERE crop_type='house' AND series='vpd_target' AND anchor='mid';
-- (sm stays 1.10 — daytime peak)

-- 2. Recompute parallel edges from the new target (174 target±width architecture).
UPDATE crop_band_anchors low
   SET value = round((tgt.value - 0.18)::numeric, 3), updated_at = now()
  FROM crop_band_anchors tgt
 WHERE low.crop_type='house' AND low.series='vpd_low'
   AND tgt.crop_type='house' AND tgt.series='vpd_target'
   AND tgt.anchor=low.anchor AND tgt.greenhouse_id=low.greenhouse_id;
UPDATE crop_band_anchors high
   SET value = round((tgt.value + 0.25)::numeric, 3), updated_at = now()
  FROM crop_band_anchors tgt
 WHERE high.crop_type='house' AND high.series='vpd_high'
   AND tgt.crop_type='house' AND tgt.series='vpd_target'
   AND tgt.anchor=high.anchor AND tgt.greenhouse_id=high.greenhouse_id;
