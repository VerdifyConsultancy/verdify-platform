-- 165-band-solar-noon-repoint.sql
-- ============================================================================
-- Re-point the served house band onto the firmware-v2 deterministic solar
-- curve so the dashboard band (and the legacy dispatcher band) peak at SOLAR
-- NOON, matching the firmware on-chip band — fixes the ~2h offset the owner
-- sees on the Temp/VPD compliance panels.
--
-- ROOT CAUSE (audited 2026-06-11): fn_center_band_setpoints shapes its diurnal
-- curve via fn_diurnal_interp(target_ts, night, day), which sets
-- `peak := solar_noon + 2.0` ("thermal lag"). That +2h pushes the band peak to
-- ~15:00 MDT while solar noon (and the firmware on-chip band, and the solar
-- actuals) peak at ~13:00. fn_crop_band_value('house', series, ts) [migration
-- 164] is the exact mirror of firmware greenhouse_solar.h band_value_at_phase()
-- at this site (lat 40.167, lon -105.102) and peaks at solar noon with no +2h.
--
-- This CREATE OR REPLACE keeps the (temp_low, temp_high, vpd_low, vpd_high)
-- OUT signature UNCHANGED — every one of the 30+ consumers (fn_band_timeline,
-- fn_house_vpd_control_band, the panels) is non-breaking. The firmware already
-- runs solar-noon-anchored (sw_onchip_band_enabled on-chip band, OTA'd
-- 2026-06-11), and ignores the dispatcher's pushed band, so removing the +2h
-- from the served band changes the dashboard (correct) and the now-inert legacy
-- push (no control effect).
--
-- The old crop_target_profiles endpoint read is kept ONLY as a COALESCE
-- last-resort fallback (if the house crop_band_anchors rows are ever missing),
-- so the band is never NULL.
--
-- SAFETY: non-self-transactional (CREATE OR REPLACE only). Rollback-validate by
-- wrapping in an outer BEGIN; ... ROLLBACK;. Idempotent.
-- ============================================================================

CREATE OR REPLACE FUNCTION public.fn_center_band_setpoints(target_ts timestamp with time zone)
 RETURNS TABLE(temp_low double precision, temp_high double precision, vpd_low double precision, vpd_high double precision)
 LANGUAGE plpgsql
 STABLE ROWS 1
AS $function$
DECLARE
    v_season text;
    night_tl double precision; night_th double precision; night_vl double precision; night_vh double precision;
    day_tl double precision; day_th double precision; day_vl double precision; day_vh double precision;
    -- firmware-v2 deterministic band (solar-noon-anchored, fn_crop_band_value)
    band_tl double precision; band_th double precision; band_vl double precision; band_vh double precision;
BEGIN
    v_season := fn_current_season();

    -- ── PRIMARY: the firmware-v2 deterministic house band (solar-phase) ──────
    -- Exact mirror of the on-chip band the ESP32 enforces. Peaks at solar noon.
    band_tl := fn_crop_band_value('house', 'temp_low',  target_ts);
    band_th := fn_crop_band_value('house', 'temp_high', target_ts);
    band_vl := fn_crop_band_value('house', 'vpd_low',   target_ts);
    band_vh := fn_crop_band_value('house', 'vpd_high',  target_ts);

    -- ── FALLBACK endpoints from the active orchid (center) profile rows ──────
    -- Only used if the house anchors are missing (band_* NULL). Keeps the
    -- legacy day/night-endpoint + fn_diurnal_interp shape as a safety net so no
    -- hour ever returns NULL.
    IF band_tl IS NULL OR band_th IS NULL OR band_vl IS NULL OR band_vh IS NULL THEN
        SELECT avg(p.temp_ideal_min) FILTER (WHERE p.hour_of_day BETWEEN 0 AND 5),
               avg(p.temp_ideal_max) FILTER (WHERE p.hour_of_day BETWEEN 0 AND 5),
               avg(p.vpd_ideal_min)  FILTER (WHERE p.hour_of_day BETWEEN 0 AND 5),
               avg(p.vpd_ideal_max)  FILTER (WHERE p.hour_of_day BETWEEN 0 AND 5),
               avg(p.temp_ideal_min) FILTER (WHERE p.hour_of_day BETWEEN 13 AND 15),
               avg(p.temp_ideal_max) FILTER (WHERE p.hour_of_day BETWEEN 13 AND 15),
               avg(p.vpd_ideal_min)  FILTER (WHERE p.hour_of_day BETWEEN 13 AND 15),
               avg(p.vpd_ideal_max)  FILTER (WHERE p.hour_of_day BETWEEN 13 AND 15)
          INTO night_tl, night_th, night_vl, night_vh, day_tl, day_th, day_vl, day_vh
          FROM crop_target_profiles p
          JOIN crops c ON c.crop_catalog_id = p.crop_catalog_id
                      AND c.is_active
                      AND c.greenhouse_id = p.greenhouse_id
         WHERE p.crop_catalog_id = 9            -- orchid
           AND p.greenhouse_id = 'vallery'
           AND p.season = v_season;

        IF night_tl IS NULL OR day_tl IS NULL THEN
            SELECT avg(p.temp_ideal_min) FILTER (WHERE p.hour_of_day BETWEEN 0 AND 5),
                   avg(p.temp_ideal_max) FILTER (WHERE p.hour_of_day BETWEEN 0 AND 5),
                   avg(p.vpd_ideal_min)  FILTER (WHERE p.hour_of_day BETWEEN 0 AND 5),
                   avg(p.vpd_ideal_max)  FILTER (WHERE p.hour_of_day BETWEEN 0 AND 5),
                   avg(p.temp_ideal_min) FILTER (WHERE p.hour_of_day BETWEEN 13 AND 15),
                   avg(p.temp_ideal_max) FILTER (WHERE p.hour_of_day BETWEEN 13 AND 15),
                   avg(p.vpd_ideal_min)  FILTER (WHERE p.hour_of_day BETWEEN 13 AND 15),
                   avg(p.vpd_ideal_max)  FILTER (WHERE p.hour_of_day BETWEEN 13 AND 15)
              INTO night_tl, night_th, night_vl, night_vh, day_tl, day_th, day_vl, day_vh
              FROM crop_target_profiles p
              JOIN crops c ON c.crop_catalog_id = p.crop_catalog_id
                          AND c.is_active
                          AND c.greenhouse_id = p.greenhouse_id
             WHERE p.crop_catalog_id = 9
               AND p.greenhouse_id = 'vallery'
               AND p.season = 'spring';
        END IF;

        night_tl := COALESCE(night_tl, 61.0); night_th := COALESCE(night_th, 67.0);
        night_vl := COALESCE(night_vl, 0.75);  night_vh := COALESCE(night_vh, 0.85);
        day_tl   := COALESCE(day_tl, 77.4);    day_th   := COALESCE(day_th, 87.3);
        day_vl   := COALESCE(day_vl, 0.94);    day_vh   := COALESCE(day_vh, 1.19);

        band_tl := COALESCE(band_tl, fn_diurnal_interp(target_ts, night_tl, day_tl));
        band_th := COALESCE(band_th, fn_diurnal_interp(target_ts, night_th, day_th));
        band_vl := COALESCE(band_vl, fn_diurnal_interp(target_ts, night_vl, day_vl));
        band_vh := COALESCE(band_vh, fn_diurnal_interp(target_ts, night_vh, day_vh));
    END IF;

    temp_low  := band_tl;
    temp_high := band_th;
    vpd_low   := band_vl;
    vpd_high  := band_vh;
    RETURN NEXT;
END;
$function$;

DO $$
DECLARE
    peak_hour integer;
    v_noon double precision;
BEGIN
    -- Verify the band now peaks at SOLAR NOON (~13:00 MDT), not 15:00.
    SELECT hr INTO peak_hour
    FROM (
        SELECT h.hr,
               (fn_center_band_setpoints(
                   (date_trunc('day', now() AT TIME ZONE 'America/Denver') + (h.hr || ' hours')::interval)
                   AT TIME ZONE 'America/Denver')).temp_high AS th
        FROM generate_series(0, 23) h(hr)
    ) s
    ORDER BY th DESC NULLS LAST
    LIMIT 1;
    v_noon := (fn_solar_sunrise_hour(now()) + fn_solar_sunset_hour(now())) / 2.0;
    RAISE NOTICE 'migration 165 OK: band temp_high peaks at hour % (solar noon ~%h). Expect peak within 1h of solar noon.',
        peak_hour, round(v_noon::numeric, 1);
    IF peak_hour > round(v_noon)::int + 1 OR peak_hour < round(v_noon)::int - 1 THEN
        RAISE WARNING 'migration 165: band peak hour % is >1h from solar noon %h — review fn_crop_band_value house anchors', peak_hour, round(v_noon::numeric,1);
    END IF;
END $$;
