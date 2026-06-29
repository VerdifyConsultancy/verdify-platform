-- 174-retune-house-bands-new-architecture.sql
--
-- RE-TUNE the house bands under the new target±width architecture (Jason: "we
-- should adjust our bands given the new architecture / what do you think is best").
--
-- 1. House TEMP target flattened so the equal-width ±5 band stays safe at BOTH
--    ends without a tight band:
--      sr 66→67, sm 84→82, ss 73 (keep), mid 65→67.
--    Effect (target ± 5): night heat-floor 60→62 °F (orchid Vandas want warm
--    nights — protects the night-humidity fix), midday vent 89→87 °F (easier on
--    cannabis/citrus), still a healthy ~15 °F diurnal swing.
-- 2. House TEMP low/high kept parallel at target ± 5 (recomputed from the new
--    target) — constant 10 °F width, no bump.
-- 3. House VPD converted to target ± width (it was the last band still built as
--    three independent harmonics): vpd_low = target − 0.18, vpd_high = target +
--    0.25 → constant 0.43 kPa width, ~current values, parallel edges.
--
-- Per-zone bands (orchid 0.70 night etc.) are intentionally untouched — they are
-- mid-stage in the dry-night ramp; tomorrow's overnight re-probe drives the next step.
--
-- crop_band_anchors is the single source of truth: live on the graph + synced to
-- the ESP32 via the dispatcher (anchors mode), no OTA, fully reversible. Data-only.

-- 1. Temp target (flatten + warm night).
UPDATE crop_band_anchors SET value = 67, updated_at = now()
 WHERE crop_type='house' AND series='temp_target' AND anchor='sr';
UPDATE crop_band_anchors SET value = 82, updated_at = now()
 WHERE crop_type='house' AND series='temp_target' AND anchor='sm';
UPDATE crop_band_anchors SET value = 67, updated_at = now()
 WHERE crop_type='house' AND series='temp_target' AND anchor='mid';
-- (ss stays 73)

-- 2. Temp low/high = target ± 5 (parallel, recomputed from the NEW target above).
UPDATE crop_band_anchors low
   SET value = tgt.value - 5.0, updated_at = now()
  FROM crop_band_anchors tgt
 WHERE low.crop_type='house' AND low.series='temp_low'
   AND tgt.crop_type='house' AND tgt.series='temp_target'
   AND tgt.anchor=low.anchor AND tgt.greenhouse_id=low.greenhouse_id;
UPDATE crop_band_anchors high
   SET value = tgt.value + 5.0, updated_at = now()
  FROM crop_band_anchors tgt
 WHERE high.crop_type='house' AND high.series='temp_high'
   AND tgt.crop_type='house' AND tgt.series='temp_target'
   AND tgt.anchor=high.anchor AND tgt.greenhouse_id=high.greenhouse_id;

-- 3. House VPD → target ± width (0.18 below / 0.25 above), parallel edges.
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
