-- 173-temp-band-parallel-equal-width.sql
--
-- EQUAL-WIDTH TEMP BAND (Jason chose "parallel edges ±5°F", 2026-06-15). The house
-- temp band's low/target/high were three INDEPENDENT harmonic curves fitted to
-- three independent anchor sets. They carried different 2nd-harmonic content, so
-- their extrema landed at different times → the band WIDTH swung 8–14 °F across the
-- day, and temp_low's anchors {60,76,66,62} had the strongest 2nd-harmonic → the
-- visible "bump" in the bottom edge. (VPD looks equal-width because its low/high
-- anchors are nearly parallel.)
--
-- Fix: make temp_low and temp_high PARALLEL to temp_target — low = target − 5 and
-- high = target + 5 at every anchor. Because the harmonic value() is linear in its
-- anchors, offsetting all four anchors by a constant shifts the whole curve by that
-- constant, so the low/high curves become exact parallel copies of the target:
--   * width = temp_high − temp_low = 10 °F, CONSTANT across the entire cycle;
--   * the low edge inherits the target's gentle shape — no independent bump.
-- This is the same target±width construction the per-zone VPD bands already use.
--
-- DEVICE + GRAPH: crop_band_anchors is the single source of truth — the dispatcher
-- syncs these anchors into the device NVS (anchors mode) and fn_crop_band_value /
-- mv_band_curve render them, so this is live on both with NO firmware OTA. It is a
-- data change, fully reversible (re-UPDATE the anchors).
--
-- ACCEPTED TRADE-OFF (Jason): a constant ±5 around a target that swings 64→84 °F
-- means midday venting moves to ~89 °F (target_noon 84 + 5) and the night heat-floor
-- to ~59 °F (target_mid 64 − 5) — ~3 °F looser at each end than the old artifacted
-- band. If that night floor reads too cold for the orchids in practice, raise
-- band_temp_target_mid (the night target) rather than shrinking the width.

UPDATE crop_band_anchors low
   SET value = tgt.value - 5.0, updated_at = now()
  FROM crop_band_anchors tgt
 WHERE low.crop_type = 'house' AND low.series = 'temp_low'
   AND tgt.crop_type = 'house' AND tgt.series = 'temp_target'
   AND tgt.anchor = low.anchor
   AND tgt.greenhouse_id = low.greenhouse_id;

UPDATE crop_band_anchors high
   SET value = tgt.value + 5.0, updated_at = now()
  FROM crop_band_anchors tgt
 WHERE high.crop_type = 'house' AND high.series = 'temp_high'
   AND tgt.crop_type = 'house' AND tgt.series = 'temp_target'
   AND tgt.anchor = high.anchor
   AND tgt.greenhouse_id = high.greenhouse_id;
