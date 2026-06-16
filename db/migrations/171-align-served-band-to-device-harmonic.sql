-- 171-align-served-band-to-device-harmonic.sql
--
-- END-TO-END BAND ALIGNMENT (Jason, 2026-06-15): make the SERVED/displayed band
-- equal the curve the DEVICE actually calculates.
--
-- Problem. The ESP32 serves a pure harmonic curve computed on-chip from the
-- `crop_band_anchors` 'house' anchors (firmware band_value_at_phase == DB
-- fn_crop_band_value == ingestor solar.py, all identical). But fn_band_setpoints
-- — which feeds fn_band_timeline (18 site dashboards), the API, the planner's
-- context, and compliance scoring — took the harmonic and then CLAMPED it with
-- crop_target_profiles-derived non-center stress limits (fn_active_noncenter_stress)
-- and the achievable envelope (fn_achievable_envelope). Those clamps are HOURLY /
-- stepped, so clamping the smooth curve pinned temp_high to flat plateaus (e.g.
-- 72.0 F across 03:00-13:00) = the "lumpy temp band" the operator sees, while the
-- unclamped VPD path (fn_house_vpd_control_band) stayed smooth. Net: the graphs +
-- planner saw a band that DIVERGED from what the device serves (up to ~14 F at
-- midday) and was lumpy for temp only.
--
-- Fix. fn_band_setpoints now returns fn_crop_band_value('house', ...) directly for
-- all four edges — byte-for-byte the device's curve. Both temp and VPD use the
-- SAME smooth harmonic approach. This cascades automatically through fn_band_timeline
-- and every consumer, so the whole upper stack (pydantic surfaces, DB, dispatcher
-- legacy fallback, planner context, site + graphs) shows the one device curve.
--
-- Safety / blast radius.
--   * NO device impact: prod runs VERDIFY_BAND_SOURCE=anchors, so the device reads
--     its own NVS anchors, never fn_band_setpoints. (In legacy mode this function
--     was a fallback device-write source; aligning it to the harmonic is also
--     correct there — legacy mode would now serve the same curve the anchors do.)
--   * The crop_target_profiles non-center-stress / achievable-envelope logic is NOT
--     deleted: fn_active_noncenter_stress, fn_achievable_envelope, fn_center_band_setpoints
--     remain and still back the compliance-GRADING surface (fn_band_trace crop_* vs
--     fw_* columns). They are simply no longer mixed into the served TARGET band.
--   * KNOWN GAP this exposes (intentionally, was hidden before): the 'house' anchors
--     can exceed a non-center crop's stress ceiling (the clamp used to mask this in
--     the display while the device served the hot curve anyway). That is a crop-science
--     anchor-tuning question on crop_band_anchors 'house' — now VISIBLE instead of
--     papered over. Follow-up, not addressed here.
--
-- Rollback: CREATE OR REPLACE back to the prior body (git history). This migration
-- is non-self-transactional (plain CREATE OR REPLACE, no COMMIT / no CONCURRENTLY);
-- safe to rollback-validate under an outer BEGIN; ... ROLLBACK;.

CREATE OR REPLACE FUNCTION public.fn_band_setpoints(target_ts timestamp with time zone)
 RETURNS TABLE(temp_low double precision, temp_high double precision, vpd_low double precision, vpd_high double precision)
 LANGUAGE sql
 STABLE ROWS 1
AS $function$
  -- The device's curve: harmonic over the 'house' crop_band_anchors at the solar
  -- phase of target_ts. Identical to mv_band_curve's house columns and to the
  -- on-chip band_value_at_phase the ESP32 computes. One source of truth.
  SELECT fn_crop_band_value('house', 'temp_low',  target_ts) AS temp_low,
         fn_crop_band_value('house', 'temp_high', target_ts) AS temp_high,
         fn_crop_band_value('house', 'vpd_low',   target_ts) AS vpd_low,
         fn_crop_band_value('house', 'vpd_high',  target_ts) AS vpd_high;
$function$;

COMMENT ON FUNCTION public.fn_band_setpoints(timestamp with time zone) IS
  'Served house band = the device''s harmonic curve (fn_crop_band_value house). '
  'Realigned 2026-06-15 (migration 171) to match what the ESP32 calculates on-chip; '
  'the prior crop_target_profiles non-center/envelope clamps now live only on the '
  'compliance-grading surface (fn_band_trace), not the served target band.';
