-- Migration 145: Vanda target band + season fix + per-zone grading band layer
--
-- Implements the Vanda zone-control design (docs/design/vanda-zone-control-design.md
-- §3, §5) and the band-layer additions of the band+compliance rearchitecture
-- (docs/design/band-compliance-architecture.md §4, §5). Backlog items:
--   D0  fn_current_season() IMMUTABLE -> STABLE (June-1 stale-season bug)
--   T1  clear stale Canna (id 2) + deactivate stale checklist row (id 5)
--   B0a backfill crop_target_profiles.crop_catalog_id for pepper/strawberry
--   B0b author summer (active-season) profile rows for active crops
--   D2  re-author orchid profile rows to Vanda v1.0 (night VPD 0.75, smooth anchors)
--   D1  rewrite fn_band_setpoints (smooth cos^2 thermal-lag engine) + fn_center_band_setpoints
--   D6  shared fn_diurnal_interp + solar sunrise/sunset helpers (one shape engine)
--   D3  season-aware fn_zone_vpd_targets + VPD inversion guard
--   D4  fn_zone_band(zone,ts) per-zone GRADING band (ideal + stress) + v_zone_band
--   D5  fn_achievable_envelope + achievable_envelope table + served-curve clamp wrap +
--       fn_active_noncenter_stress
--   D7  crop_target_profiles _default rows (empty-zone grading)
--   D8  repoint v_target_curve -> v_zone_band, THEN deprecate fn_target_band/_smooth
--   N1  insert vanda_orchid_active nutrient recipe (is_active=FALSE)
--
-- INTERNAL ORDERING IS LOAD-BEARING (design §5.1): backfill before catalog-joins;
-- Canna clear before is_active joins; DELETE orchid before INSERT (unique-key);
-- helpers before callers; _default rows before fn_zone_band; v_target_curve repoint
-- before any deprecation.
--
-- Compliance is OFF the live control path (setpoint-server.py:309-311 calls only
-- fn_band_setpoints / fn_house_vpd_control_band / fn_zone_vpd_targets), so the served
-- band change ships DB-only. RESTARTS (CLAUDE.md rule 7): bounce setpoint-server
-- (dispatcher) + verdify-mcp. No firmware OTA. Attach make firmware-replay +
-- recorded THRESHOLD_PCT validation evidence for the setpoint-policy change.

-- =====================================================================
-- D0: fn_current_season() IMMUTABLE -> STABLE  (must be FIRST)
-- =====================================================================
-- Declared IMMUTABLE (pg_proc.provolatile='i') but reads now(); PostgreSQL may
-- constant-fold it to a stale season across the June-1 spring->summer flip. STABLE
-- is the correct volatility for a function that reads the clock. Body unchanged.
CREATE OR REPLACE FUNCTION public.fn_current_season()
RETURNS text
LANGUAGE plpgsql
STABLE
AS $function$
BEGIN
  RETURN CASE EXTRACT(MONTH FROM now())
    WHEN 3 THEN 'spring' WHEN 4 THEN 'spring' WHEN 5 THEN 'spring'
    WHEN 6 THEN 'summer' WHEN 7 THEN 'summer' WHEN 8 THEN 'summer'
    WHEN 9 THEN 'fall' WHEN 10 THEN 'fall' WHEN 11 THEN 'fall'
    ELSE 'winter'
  END;
END;
$function$;

-- =====================================================================
-- T1 (a): clear stale Canna (crops id 2) -> patio, write provenance event
-- =====================================================================
-- Must precede the is_active catalog join so Canna's wide VPD band cannot leak
-- into the house / zone bands. crop_events uses column `ts` (verified schema),
-- NOT created_at.
UPDATE crops
   SET is_active = false,
       stage = 'cleared',
       cleared_at = now()
 WHERE id = 2
   AND is_active IS DISTINCT FROM false;

INSERT INTO crop_events (crop_id, event_type, notes, source, ts)
SELECT 2, 'removed', 'Canna to patio summer 2026 (migration 145)', 'migration', now()
 WHERE NOT EXISTS (
   SELECT 1 FROM crop_events
    WHERE crop_id = 2 AND event_type = 'removed'
      AND notes = 'Canna to patio summer 2026 (migration 145)'
 );

-- =====================================================================
-- T1 (b): deactivate stale checklist template row (id 5, "Water canna lilies")
-- =====================================================================
UPDATE daily_checklist_template
   SET is_active = false
 WHERE id = 5
   AND is_active IS DISTINCT FROM false;

-- =====================================================================
-- B0a: backfill crop_target_profiles.crop_catalog_id for pepper/strawberry
-- =====================================================================
-- catalog slugs are plural (peppers/strawberries) vs the singular profile
-- crop_type. Must precede the catalog-join rewrites or pepper/strawberry drop out.
UPDATE crop_target_profiles ctp
   SET crop_catalog_id = cc.id
  FROM crop_catalog cc
 WHERE ctp.crop_catalog_id IS NULL
   AND cc.slug = CASE ctp.crop_type
                   WHEN 'pepper' THEN 'peppers'
                   WHEN 'strawberry' THEN 'strawberries'
                   ELSE ctp.crop_type
                 END;

-- =====================================================================
-- D2: re-author orchid profile rows to Vanda v1.0 (DELETE then INSERT)
-- =====================================================================
-- DELETE precedes INSERT to avoid the unique-key collision on
-- (crop_type, growth_stage, hour_of_day, season, greenhouse_id).
DELETE FROM crop_target_profiles WHERE crop_type = 'orchid';

-- Spring (active-season) rows per design §3.3 anchor table. night band 61-67F /
-- VPD 0.75-0.85; midday served peak 78-88F / VPD 0.95-1.20; stress 55/100,
-- 0.50/1.50; dli 12; source vanda_spec_v1.0.
INSERT INTO crop_target_profiles
  (crop_type, growth_stage, hour_of_day, season,
   temp_ideal_min, temp_ideal_max, temp_stress_low, temp_stress_high,
   vpd_ideal_min, vpd_ideal_max, vpd_stress_low, vpd_stress_high,
   dli_target_mol, source, greenhouse_id, crop_catalog_id)
VALUES
  ('orchid','vegetative', 0,'spring', 61.0,67.0,55,100, 0.75,0.85,0.50,1.50,12,'vanda_spec_v1.0','vallery',9),
  ('orchid','vegetative', 1,'spring', 61.0,67.0,55,100, 0.75,0.85,0.50,1.50,12,'vanda_spec_v1.0','vallery',9),
  ('orchid','vegetative', 2,'spring', 61.0,67.0,55,100, 0.75,0.85,0.50,1.50,12,'vanda_spec_v1.0','vallery',9),
  ('orchid','vegetative', 3,'spring', 61.0,67.0,55,100, 0.75,0.85,0.50,1.50,12,'vanda_spec_v1.0','vallery',9),
  ('orchid','vegetative', 4,'spring', 61.0,67.0,55,100, 0.75,0.85,0.50,1.50,12,'vanda_spec_v1.0','vallery',9),
  ('orchid','vegetative', 5,'spring', 61.0,67.0,55,100, 0.75,0.85,0.50,1.50,12,'vanda_spec_v1.0','vallery',9),
  ('orchid','vegetative', 6,'spring', 61.0,67.0,55,100, 0.75,0.85,0.50,1.50,12,'vanda_spec_v1.0','vallery',9),
  ('orchid','vegetative', 7,'spring', 61.2,67.2,55,100, 0.75,0.85,0.50,1.50,12,'vanda_spec_v1.0','vallery',9),
  ('orchid','vegetative', 8,'spring', 62.4,68.8,55,100, 0.76,0.88,0.50,1.50,12,'vanda_spec_v1.0','vallery',9),
  ('orchid','vegetative', 9,'spring', 64.8,71.7,55,100, 0.79,0.93,0.50,1.50,12,'vanda_spec_v1.0','vallery',9),
  ('orchid','vegetative',10,'spring', 67.8,75.5,55,100, 0.82,0.99,0.50,1.50,12,'vanda_spec_v1.0','vallery',9),
  ('orchid','vegetative',11,'spring', 71.2,79.5,55,100, 0.86,1.06,0.50,1.50,12,'vanda_spec_v1.0','vallery',9),
  ('orchid','vegetative',12,'spring', 74.2,83.3,55,100, 0.89,1.12,0.50,1.50,12,'vanda_spec_v1.0','vallery',9),
  ('orchid','vegetative',13,'spring', 76.6,86.2,55,100, 0.92,1.17,0.50,1.50,12,'vanda_spec_v1.0','vallery',9),
  ('orchid','vegetative',14,'spring', 77.8,87.8,55,100, 0.95,1.20,0.50,1.50,12,'vanda_spec_v1.0','vallery',9),
  ('orchid','vegetative',15,'spring', 77.8,87.8,55,100, 0.95,1.20,0.50,1.50,12,'vanda_spec_v1.0','vallery',9),
  ('orchid','vegetative',16,'spring', 76.6,86.2,55,100, 0.92,1.17,0.50,1.50,12,'vanda_spec_v1.0','vallery',9),
  ('orchid','vegetative',17,'spring', 74.2,83.3,55,100, 0.89,1.12,0.50,1.50,12,'vanda_spec_v1.0','vallery',9),
  ('orchid','vegetative',18,'spring', 71.2,79.5,55,100, 0.86,1.06,0.50,1.50,12,'vanda_spec_v1.0','vallery',9),
  ('orchid','vegetative',19,'spring', 67.8,75.5,55,100, 0.82,0.99,0.50,1.50,12,'vanda_spec_v1.0','vallery',9),
  ('orchid','vegetative',20,'spring', 64.8,71.7,55,100, 0.79,0.93,0.50,1.50,12,'vanda_spec_v1.0','vallery',9),
  ('orchid','vegetative',21,'spring', 62.4,68.8,55,100, 0.76,0.88,0.50,1.50,12,'vanda_spec_v1.0','vallery',9),
  ('orchid','vegetative',22,'spring', 61.2,67.2,55,100, 0.75,0.85,0.50,1.50,12,'vanda_spec_v1.0','vallery',9),
  ('orchid','vegetative',23,'spring', 61.0,67.0,55,100, 0.75,0.85,0.50,1.50,12,'vanda_spec_v1.0','vallery',9);

-- =====================================================================
-- B0b: author summer (active-season) rows
-- =====================================================================
-- Longmont Vanda active season spans spring+summer; author identical summer
-- orchid rows so the June-1 season switch never serves NULL. Also copy the
-- other active crops' spring rows into summer (nearest-season author) so the
-- house band has every active crop present in summer.
INSERT INTO crop_target_profiles
  (crop_type, growth_stage, hour_of_day, season,
   temp_ideal_min, temp_ideal_max, temp_stress_low, temp_stress_high,
   vpd_ideal_min, vpd_ideal_max, vpd_stress_low, vpd_stress_high,
   dli_target_mol, source, greenhouse_id, crop_catalog_id)
SELECT crop_type, growth_stage, hour_of_day, 'summer',
       temp_ideal_min, temp_ideal_max, temp_stress_low, temp_stress_high,
       vpd_ideal_min, vpd_ideal_max, vpd_stress_low, vpd_stress_high,
       dli_target_mol,
       CASE WHEN crop_type = 'orchid' THEN source ELSE source || '+summer_from_spring' END,
       greenhouse_id, crop_catalog_id
  FROM crop_target_profiles src
 WHERE src.season = 'spring'
   AND src.crop_type IN (
       SELECT DISTINCT CASE c.name
                          WHEN 'Vanda Orchids' THEN 'orchid'
                          ELSE lower(c.name)
                        END
         FROM crops c
        WHERE c.is_active = true AND c.greenhouse_id = src.greenhouse_id
   )
   AND NOT EXISTS (
       SELECT 1 FROM crop_target_profiles dst
        WHERE dst.crop_type = src.crop_type
          AND dst.growth_stage = src.growth_stage
          AND dst.hour_of_day = src.hour_of_day
          AND dst.season = 'summer'
          AND dst.greenhouse_id = src.greenhouse_id
   );

-- =====================================================================
-- D7: empty-zone _default grading rows (spring + summer), before fn_zone_band
-- =====================================================================
-- Non-joined house-comfort band (ideal 60-80F / stress 45-95F, vpd 0.4-1.4 /
-- 0.2-2.0) so fn_zone_band never returns NULL for north/west/south-after-T1.
-- crop_catalog_id stays NULL (not a real crop) so it never participates in the
-- catalog-join active-crop bands.
INSERT INTO crop_target_profiles
  (crop_type, growth_stage, hour_of_day, season,
   temp_ideal_min, temp_ideal_max, temp_stress_low, temp_stress_high,
   vpd_ideal_min, vpd_ideal_max, vpd_stress_low, vpd_stress_high,
   dli_target_mol, source, greenhouse_id, crop_catalog_id)
SELECT '_default','vegetative', h, s,
       60.0, 80.0, 45.0, 95.0,
       0.40, 1.40, 0.20, 2.00,
       12, 'house_default_v1.0', 'vallery', NULL
  FROM generate_series(0,23) h
  CROSS JOIN (VALUES ('spring'),('summer')) seasons(s)
 WHERE NOT EXISTS (
   SELECT 1 FROM crop_target_profiles d
    WHERE d.crop_type = '_default' AND d.growth_stage = 'vegetative'
      AND d.hour_of_day = h AND d.season = s AND d.greenhouse_id = 'vallery'
 );

-- =====================================================================
-- D6: shared diurnal-interp + solar sunrise/sunset helpers (helpers before callers)
-- =====================================================================
-- IMMUTABLE binary-search zero-finders over fn_solar_altitude (itself IMMUTABLE).
-- No now() / no mutable state. Return the fractional local hour of the
-- sunrise/sunset altitude zero-crossing for the calendar day of target_ts.
CREATE OR REPLACE FUNCTION public.fn_solar_sunrise_hour(target_ts timestamptz)
RETURNS double precision
LANGUAGE plpgsql
IMMUTABLE
AS $function$
DECLARE
    day_start timestamptz;
    lo double precision := 0.0;
    hi double precision := 12.0;
    mid double precision;
    i int;
    a_lo double precision;
    a_mid double precision;
BEGIN
    -- midnight local for the target day, expressed as a timestamptz
    day_start := date_trunc('day', target_ts AT TIME ZONE 'America/Denver') AT TIME ZONE 'America/Denver';
    a_lo := fn_solar_altitude(day_start + (lo || ' hours')::interval);
    -- If the sun is already up at local midnight (polar edge case / bad input),
    -- fall back to a sane default.
    IF a_lo > 0 THEN RETURN 6.0; END IF;
    FOR i IN 1..30 LOOP
        mid := (lo + hi) / 2.0;
        a_mid := fn_solar_altitude(day_start + (mid || ' hours')::interval);
        IF a_mid > 0 THEN hi := mid; ELSE lo := mid; END IF;
    END LOOP;
    RETURN (lo + hi) / 2.0;
END;
$function$;

CREATE OR REPLACE FUNCTION public.fn_solar_sunset_hour(target_ts timestamptz)
RETURNS double precision
LANGUAGE plpgsql
IMMUTABLE
AS $function$
DECLARE
    day_start timestamptz;
    lo double precision := 12.0;
    hi double precision := 24.0;
    mid double precision;
    i int;
    a_lo double precision;
    a_mid double precision;
BEGIN
    day_start := date_trunc('day', target_ts AT TIME ZONE 'America/Denver') AT TIME ZONE 'America/Denver';
    a_lo := fn_solar_altitude(day_start + (lo || ' hours')::interval);
    -- If sun already down at local noon (bad input), fall back.
    IF a_lo < 0 THEN RETURN 20.0; END IF;
    FOR i IN 1..30 LOOP
        mid := (lo + hi) / 2.0;
        a_mid := fn_solar_altitude(day_start + (mid || ' hours')::interval);
        IF a_mid > 0 THEN lo := mid; ELSE hi := mid; END IF;
    END LOOP;
    RETURN (lo + hi) / 2.0;
END;
$function$;

-- Shared cos^2 thermal-lag shape engine (Vanda design §3.4). Given the night
-- endpoint and day endpoint of any band value, returns the smoothly-interpolated
-- value at target_ts using a solar-tracked, thermal-lagged sun_factor. peak =
-- solar_noon + 2h thermal lag; W = half-day + 1h tail. C1-continuous (no slope
-- breaks). Sunrise/sunset/solar-noon derive from the solar helpers so the curve
-- expands/contracts seasonally (SEA-2). IMMUTABLE (no now()).
CREATE OR REPLACE FUNCTION public.fn_diurnal_interp(
    target_ts timestamptz,
    night_val double precision,
    day_val   double precision
) RETURNS double precision
LANGUAGE plpgsql
IMMUTABLE
AS $function$
DECLARE
    local_hour double precision;
    sunrise double precision;
    sunset double precision;
    solar_noon double precision;
    peak double precision;
    w double precision;
    sun_factor double precision;
    arg double precision;
BEGIN
    local_hour := EXTRACT(hour FROM target_ts AT TIME ZONE 'America/Denver')
                + EXTRACT(minute FROM target_ts AT TIME ZONE 'America/Denver') / 60.0;
    sunrise := fn_solar_sunrise_hour(target_ts);
    sunset  := fn_solar_sunset_hour(target_ts);
    solar_noon := (sunrise + sunset) / 2.0;
    peak := solar_noon + 2.0;                 -- thermal lag (design §3.4)
    w := (sunset - sunrise) / 2.0 + 1.0;      -- half-day + 1h tail
    IF w <= 0 THEN w := 7.0; END IF;          -- defensive

    IF abs(local_hour - peak) < w THEN
        arg := (local_hour - peak) * PI() / (2.0 * w);
        sun_factor := cos(arg);
        sun_factor := sun_factor * sun_factor;   -- cos^2
    ELSE
        sun_factor := 0.0;
    END IF;

    RETURN night_val + (day_val - night_val) * sun_factor;
END;
$function$;

-- =====================================================================
-- D1: fn_center_band_setpoints + fn_band_setpoints (smooth cos^2 engine)
-- =====================================================================
-- Internal helper: resolve the night/day endpoints of the orchid (catalog 9,
-- is_active) band for a given season, with a documented nearest-season fallback
-- so no hour/season ever returns NULL. night = mean(hours 0-5), day = mean(hours
-- 13-15). Re-authoring the profile re-shapes the curve with no code change.
CREATE OR REPLACE FUNCTION public.fn_center_band_setpoints(target_ts timestamptz)
RETURNS TABLE(temp_low double precision, temp_high double precision,
              vpd_low double precision, vpd_high double precision)
LANGUAGE plpgsql
STABLE ROWS 1
AS $function$
DECLARE
    v_season text;
    night_tl double precision; night_th double precision; night_vl double precision; night_vh double precision;
    day_tl double precision; day_th double precision; day_vl double precision; day_vh double precision;
BEGIN
    v_season := fn_current_season();

    -- endpoints from the active orchid (center) profile rows, catalog+is_active join
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

    -- nearest-season fallback: if the current season has no rows, use spring
    -- (the authored active-season baseline) so no hour ever returns NULL.
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

    -- final defensive fallback to the design anchors if the profile is missing
    -- entirely (e.g. orchid de-activated) so the served band is never NULL.
    night_tl := COALESCE(night_tl, 61.0); night_th := COALESCE(night_th, 67.0);
    night_vl := COALESCE(night_vl, 0.75);  night_vh := COALESCE(night_vh, 0.85);
    day_tl   := COALESCE(day_tl, 77.4);    day_th   := COALESCE(day_th, 87.3);
    day_vl   := COALESCE(day_vl, 0.94);    day_vh   := COALESCE(day_vh, 1.19);

    temp_low  := fn_diurnal_interp(target_ts, night_tl, day_tl);
    temp_high := fn_diurnal_interp(target_ts, night_th, day_th);
    vpd_low   := fn_diurnal_interp(target_ts, night_vl, day_vl);
    vpd_high  := fn_diurnal_interp(target_ts, night_vh, day_vh);
    RETURN NEXT;
END;
$function$;

-- THE SINGLE SERVED CONTROL LINE (drop-in name+signature for the dispatcher).
-- In migration 145 this serves the orchid-anchored center temp band directly
-- (Vanda priority). Migration 146 WRAPS this with the achievable-envelope clamp
-- + non-center safety floors/ceilings; defining the simple body here keeps 145
-- self-contained and 146's CREATE OR REPLACE replaces only the body.
-- VPD is served from the house control band (non-orchid crops set the house
-- VPD), which is exactly why the orchid night VPD floor (0.75) does NOT inflate
-- the house vpd_low (the inversion guard, design §5 "Inversion guard (critical)").
CREATE OR REPLACE FUNCTION public.fn_band_setpoints(target_ts timestamptz)
RETURNS TABLE(temp_low double precision, temp_high double precision,
              vpd_low double precision, vpd_high double precision)
LANGUAGE plpgsql
STABLE ROWS 1
AS $function$
DECLARE
    c record;
    v_floor double precision;
    v_ceil double precision;
    v_vlow double precision;
    v_vhigh double precision;
BEGIN
    SELECT * INTO c FROM fn_center_band_setpoints(target_ts);

    -- decision #1 safety: never serve a ceiling above any active non-center
    -- crop's temp_stress_high, nor a floor below its temp_stress_low. Post-T1
    -- this is lettuce/strawberry/pepper.
    SELECT MAX(s.temp_stress_low), MIN(s.temp_stress_high)
      INTO v_floor, v_ceil
      FROM fn_active_noncenter_stress(target_ts) s;

    temp_low  := GREATEST(c.temp_low, COALESCE(v_floor, c.temp_low));
    temp_high := LEAST(c.temp_high, COALESCE(v_ceil, c.temp_high));
    IF temp_low > temp_high THEN temp_low := temp_high; END IF;

    -- VPD served line = house control band (non-orchid), with inversion clamp.
    SELECT h.house_vpd_low, h.house_vpd_high
      INTO v_vlow, v_vhigh
      FROM fn_house_vpd_control_band(target_ts) h;
    IF v_vlow IS NULL OR v_vhigh IS NULL THEN
        v_vlow := c.vpd_low; v_vhigh := c.vpd_high;
    END IF;
    IF v_vlow > v_vhigh THEN v_vlow := v_vhigh; END IF;

    vpd_low := v_vlow;
    vpd_high := v_vhigh;
    RETURN NEXT;
END;
$function$;

-- =====================================================================
-- D4 dependency: fn_active_noncenter_stress(ts) — non-priority safety bounds
-- =====================================================================
-- The tightest stress envelope across active NON-center crops (the food crops in
-- east post-T1). Used by the served-line safety floor/ceiling. Catalog+is_active
-- join, season-aware. Returns one row per active non-center crop_type.
CREATE OR REPLACE FUNCTION public.fn_active_noncenter_stress(target_ts timestamptz)
RETURNS TABLE(crop_type text, temp_stress_low double precision, temp_stress_high double precision,
              vpd_stress_low double precision, vpd_stress_high double precision)
LANGUAGE plpgsql
STABLE
AS $function$
DECLARE
    v_season text := fn_current_season();
    v_hour int := EXTRACT(hour FROM target_ts AT TIME ZONE 'America/Denver')::int;
BEGIN
    RETURN QUERY
    SELECT p.crop_type, p.temp_stress_low, p.temp_stress_high,
           p.vpd_stress_low, p.vpd_stress_high
      FROM crop_target_profiles p
      JOIN crops c ON c.crop_catalog_id = p.crop_catalog_id
                  AND c.is_active
                  AND c.greenhouse_id = p.greenhouse_id
     WHERE p.greenhouse_id = 'vallery'
       AND p.hour_of_day = v_hour
       AND p.season = COALESCE(
             (SELECT v_season WHERE EXISTS (
                 SELECT 1 FROM crop_target_profiles p2
                  WHERE p2.greenhouse_id = 'vallery' AND p2.season = v_season
                    AND p2.crop_catalog_id IS NOT NULL)),
             'spring')
       AND p.crop_catalog_id IS DISTINCT FROM 9   -- exclude center/orchid
       AND p.crop_catalog_id IS NOT NULL;         -- exclude _default
END;
$function$;

-- =====================================================================
-- D4: fn_zone_band(zone, ts) — per-zone GRADING band (ideal + stress)
-- =====================================================================
-- center -> orchid (single crop, priority); east -> intersection(ideal) /
-- union(stress) over its active crops; empty zones -> _default. Catalog/is_active/
-- season join (the migration-145 correctness fix). STABLE; reads current is_active
-- state (live grading band, not a historical audit band). Never returns NULL.
CREATE OR REPLACE FUNCTION public.fn_zone_band(
    p_zone text,
    p_ts   timestamptz,
    p_greenhouse_id text DEFAULT 'vallery'
) RETURNS TABLE(
    zone text,
    temp_low double precision, temp_high double precision,
    temp_stress_low double precision, temp_stress_high double precision,
    vpd_low double precision, vpd_high double precision,
    vpd_stress_low double precision, vpd_stress_high double precision,
    crop_basis text,
    is_proxy boolean
) LANGUAGE plpgsql STABLE AS $function$
DECLARE
    v_season text := fn_current_season();
    v_hour int := EXTRACT(hour FROM p_ts AT TIME ZONE 'America/Denver')::int;
    v_zone_id int;
    v_has_active boolean;
BEGIN
    -- map zone name -> crops.zone label (they are identical strings: center/east/
    -- north/south/west)
    -- season fallback: if current season has no joinable rows, use spring
    IF NOT EXISTS (SELECT 1 FROM crop_target_profiles
                    WHERE greenhouse_id = p_greenhouse_id AND season = v_season
                      AND crop_catalog_id IS NOT NULL) THEN
        v_season := 'spring';
    END IF;

    -- Does this zone hold any active joinable crop?
    SELECT EXISTS (
        SELECT 1
          FROM crops c
          JOIN crop_target_profiles p
            ON p.crop_catalog_id = c.crop_catalog_id
           AND p.greenhouse_id = c.greenhouse_id
         WHERE c.zone = p_zone
           AND c.is_active
           AND c.greenhouse_id = p_greenhouse_id
           AND p.hour_of_day = v_hour
           AND p.season = v_season
    ) INTO v_has_active;

    IF v_has_active THEN
        RETURN QUERY
        SELECT
            p_zone,
            MAX(p.temp_ideal_min)::double precision,    -- ideal = intersection
            MIN(p.temp_ideal_max)::double precision,
            MIN(p.temp_stress_low)::double precision,   -- stress = union
            MAX(p.temp_stress_high)::double precision,
            MAX(p.vpd_ideal_min)::double precision,
            MIN(p.vpd_ideal_max)::double precision,
            MIN(p.vpd_stress_low)::double precision,
            MAX(p.vpd_stress_high)::double precision,
            string_agg(DISTINCT p.crop_type, '∩' ORDER BY p.crop_type),
            (p_zone = 'center')                          -- center uses vpd_avg proxy (HW-1 pending)
          FROM crops c
          JOIN crop_target_profiles p
            ON p.crop_catalog_id = c.crop_catalog_id
           AND p.greenhouse_id = c.greenhouse_id
         WHERE c.zone = p_zone
           AND c.is_active
           AND c.greenhouse_id = p_greenhouse_id
           AND p.hour_of_day = v_hour
           AND p.season = v_season;
    ELSE
        -- empty zone -> _default house-comfort band
        RETURN QUERY
        SELECT
            p_zone,
            d.temp_ideal_min, d.temp_ideal_max, d.temp_stress_low, d.temp_stress_high,
            d.vpd_ideal_min, d.vpd_ideal_max, d.vpd_stress_low, d.vpd_stress_high,
            '_default'::text,
            false
          FROM crop_target_profiles d
         WHERE d.crop_type = '_default'
           AND d.greenhouse_id = p_greenhouse_id
           AND d.hour_of_day = v_hour
           AND d.season = v_season
         LIMIT 1;
    END IF;
END;
$function$;

-- =====================================================================
-- D3: season-aware fn_zone_vpd_targets + inversion guard
-- =====================================================================
-- Replace season='spring' with fn_current_season() (+ spring fallback). Keep the
-- name-based zone->crop CASE (center->orchid) but make it is_active-aware. Center
-- default 0.80 -> 0.85; west default 1.2 -> 1.5 (design §5.1(h)/D3).
CREATE OR REPLACE FUNCTION public.fn_zone_vpd_targets(target_ts timestamptz)
RETURNS TABLE(vpd_target_south double precision, vpd_target_west double precision,
              vpd_target_east double precision, vpd_target_center double precision)
LANGUAGE plpgsql
STABLE ROWS 1
AS $function$
DECLARE
    local_hour int;
    frac float;
    next_hour int;
    v_season text;
BEGIN
    local_hour := EXTRACT(hour FROM target_ts AT TIME ZONE 'America/Denver');
    frac := EXTRACT(minute FROM target_ts AT TIME ZONE 'America/Denver') / 60.0;
    next_hour := (local_hour + 1) % 24;
    v_season := fn_current_season();
    IF NOT EXISTS (SELECT 1 FROM crop_target_profiles
                    WHERE season = v_season AND greenhouse_id = 'vallery'
                      AND crop_catalog_id IS NOT NULL) THEN
        v_season := 'spring';
    END IF;

    RETURN QUERY
    WITH zone_crops AS (
        SELECT zone,
            CASE name
                WHEN 'Vanda Orchids' THEN 'orchid'
                WHEN 'Canna Lilies' THEN 'canna'
                ELSE lower(name)
            END AS crop_type
        FROM crops WHERE is_active = true AND greenhouse_id = 'vallery'
    ),
    h0 AS (
        SELECT zc.zone, MIN(p.vpd_ideal_max) AS vpd_max
        FROM zone_crops zc
        JOIN crop_target_profiles p ON p.crop_type = zc.crop_type
            AND p.hour_of_day = local_hour AND p.season = v_season
            AND p.greenhouse_id = 'vallery'
        GROUP BY zc.zone
    ),
    h1 AS (
        SELECT zc.zone, MIN(p.vpd_ideal_max) AS vpd_max
        FROM zone_crops zc
        JOIN crop_target_profiles p ON p.crop_type = zc.crop_type
            AND p.hour_of_day = next_hour AND p.season = v_season
            AND p.greenhouse_id = 'vallery'
        GROUP BY zc.zone
    )
    SELECT
        COALESCE((SELECT h0.vpd_max + frac * (h1.vpd_max - h0.vpd_max) FROM h0 JOIN h1 ON h0.zone = h1.zone WHERE h0.zone = 'south'), 1.5),
        COALESCE((SELECT h0.vpd_max + frac * (h1.vpd_max - h0.vpd_max) FROM h0 JOIN h1 ON h0.zone = h1.zone WHERE h0.zone = 'west'), 1.5),
        COALESCE((SELECT h0.vpd_max + frac * (h1.vpd_max - h0.vpd_max) FROM h0 JOIN h1 ON h0.zone = h1.zone WHERE h0.zone = 'east'), 1.0),
        COALESCE((SELECT h0.vpd_max + frac * (h1.vpd_max - h0.vpd_max) FROM h0 JOIN h1 ON h0.zone = h1.zone WHERE h0.zone = 'center'), 0.85);
END;
$function$;

-- Belt-and-suspenders inversion clamp in fn_house_vpd_control_band: if the
-- computed house low ever exceeds the house high, clamp low to high. The existing
-- body already enforces a min-width, but the orchid 0.75 night floor makes this
-- defensive clamp explicit (design §5 inversion guard). We re-create the function
-- with an added final clamp; the rest of the body is byte-identical to the live def.
CREATE OR REPLACE FUNCTION public.fn_house_vpd_control_band(target_ts timestamp with time zone)
RETURNS TABLE(crop_vpd_low double precision, crop_vpd_high double precision,
              vpd_target_south double precision, vpd_target_west double precision,
              vpd_target_east double precision, vpd_target_center double precision,
              zone_vpd_min double precision, zone_vpd_median double precision,
              zone_vpd_max double precision, house_vpd_low double precision,
              house_vpd_high double precision, house_vpd_min_width_kpa double precision,
              house_vpd_low_margin_kpa double precision)
LANGUAGE plpgsql
STABLE ROWS 1
AS $function$
DECLARE
    v_base_low double precision;
    v_base_high double precision;
    v_south double precision;
    v_west double precision;
    v_east double precision;
    v_center double precision;
    v_targets double precision[];
    v_n integer;
    v_house_low double precision;
    v_house_high double precision;
    v_min_width constant double precision := 0.55;
    v_low_margin constant double precision := 0.20;
BEGIN
    SELECT b.vpd_low, b.vpd_high
      INTO v_base_low, v_base_high
      FROM fn_center_band_setpoints(target_ts) AS b
     LIMIT 1;

    IF v_base_low IS NULL OR v_base_high IS NULL THEN
        RETURN;
    END IF;

    SELECT z.vpd_target_south, z.vpd_target_west, z.vpd_target_east, z.vpd_target_center
      INTO v_south, v_west, v_east, v_center
      FROM fn_zone_vpd_targets(target_ts) AS z
     LIMIT 1;

    SELECT array_agg(v ORDER BY v)
      INTO v_targets
      FROM (VALUES (v_south), (v_west), (v_east), (v_center)) AS target_values(v)
     WHERE v IS NOT NULL AND v > 0 AND v < 10;

    v_n := COALESCE(array_length(v_targets, 1), 0);

    crop_vpd_low := v_base_low;
    crop_vpd_high := v_base_high;
    vpd_target_south := v_south;
    vpd_target_west := v_west;
    vpd_target_east := v_east;
    vpd_target_center := v_center;
    house_vpd_min_width_kpa := v_min_width;
    house_vpd_low_margin_kpa := v_low_margin;

    IF v_n = 0 THEN
        house_vpd_low := v_base_low;
        house_vpd_high := v_base_high;
        RETURN NEXT;
        RETURN;
    END IF;

    zone_vpd_min := v_targets[1];
    zone_vpd_max := v_targets[v_n];
    IF mod(v_n, 2) = 1 THEN
        zone_vpd_median := v_targets[(v_n + 1) / 2];
    ELSE
        zone_vpd_median := (v_targets[v_n / 2] + v_targets[(v_n / 2) + 1]) / 2.0;
    END IF;

    v_house_high := least(zone_vpd_max, greatest(v_base_high, zone_vpd_median));
    v_house_low := greatest(v_base_low, zone_vpd_min - v_low_margin);
    v_house_low := least(v_house_low, v_house_high - v_min_width);
    v_house_low := greatest(0.1, v_house_low);

    IF v_house_high - v_house_low < v_min_width THEN
        v_house_low := greatest(0.1, v_house_high - v_min_width);
    END IF;

    -- inversion guard (design §5): never return low > high.
    IF v_house_low > v_house_high THEN
        v_house_low := v_house_high;
    END IF;

    house_vpd_low := round(v_house_low::numeric, 3)::double precision;
    house_vpd_high := round(v_house_high::numeric, 3)::double precision;
    RETURN NEXT;
END;
$function$;

-- =====================================================================
-- D5: achievable_envelope table + fn_achievable_envelope accessor
-- =====================================================================
-- The per-season physically-achievable envelope (decision #3). Precomputed
-- (seasonal timescale), NOT a live climate scan. The refresh_achievable_envelope
-- job (ingestor-owned) populates it; this migration creates the table + accessor
-- and seeds a conservative authority-only spring+summer baseline so the served
-- line never reads NULL before the first job run. The accessor (point-lookup +
-- hourly interpolation) is dispatcher-safe (<5ms). The fn_band_setpoints wrap
-- that consumes the cap lands in migration 146 (additive).
CREATE TABLE IF NOT EXISTS achievable_envelope (
    greenhouse_id text NOT NULL DEFAULT 'vallery',
    zone text NOT NULL,
    season text NOT NULL,
    hour_of_day int NOT NULL,
    env_temp_lo_floor double precision NOT NULL,
    env_temp_hi_cap double precision NOT NULL,
    env_temp_achievable_p50 double precision,
    env_vpd_lo_floor double precision,
    env_vpd_hi_cap double precision,
    cap_source text NOT NULL,
    authority_inputs jsonb,
    historical_inputs jsonb,
    derived_at timestamptz NOT NULL DEFAULT now(),
    is_active boolean NOT NULL DEFAULT true,
    PRIMARY KEY (greenhouse_id, zone, season, hour_of_day)
);

COMMENT ON TABLE achievable_envelope IS
'Per-(greenhouse,zone,season,hour) physically-achievable temp/VPD envelope (band-compliance design §5). '
'env_temp_hi_cap = max(authority Term A: outdoor_p50 + k*solar_p50 + cooling_margin ; '
'historical Term B: indoor_p90 saturated-only, hot-sample-gated) then min(agronomic_ideal - overheat_slack). '
'env_temp_achievable_p50 = expected achievable median for the feasibility/concession layer. '
'Populated by refresh_achievable_envelope (ingestor). Seeded here with an authority-only conservative baseline.';

-- Seed a conservative authority-only baseline for center (spring+summer) so the
-- served line (wrapped in 146) has a non-NULL cap on day one. Values follow the
-- design §5.4 worked example: cap derived from outdoor_p50 + k*solar + margin,
-- clamped to agronomic_ideal - overheat_slack. Floor = a low house comfort floor.
-- The ingestor refresh will overwrite these with the live-data derivation.
INSERT INTO achievable_envelope
  (greenhouse_id, zone, season, hour_of_day,
   env_temp_lo_floor, env_temp_hi_cap, env_temp_achievable_p50,
   env_vpd_lo_floor, env_vpd_hi_cap, cap_source, authority_inputs)
SELECT 'vallery', 'center', s.season, h.hour,
       55.0,
       -- conservative cap: high enough that the box can reach it (un-pins
       -- cooling) yet below the 95F agronomic ideal; flat-ish midday, lower at
       -- night. This is a seed, refreshed seasonally by the ingestor job.
       CASE
         WHEN h.hour BETWEEN 11 AND 17 THEN 90.0
         WHEN h.hour BETWEEN 8 AND 20 THEN 82.0
         ELSE 72.0
       END,
       CASE
         WHEN h.hour BETWEEN 11 AND 17 THEN 88.0
         WHEN h.hour BETWEEN 8 AND 20 THEN 78.0
         ELSE 66.0
       END,
       0.30, 1.80, 'authority_seed',
       jsonb_build_object('seed', true, 'note',
         'migration-145 conservative seed; replaced by refresh_achievable_envelope')
  FROM (VALUES ('spring'),('summer')) s(season)
  CROSS JOIN generate_series(0,23) h(hour)
ON CONFLICT (greenhouse_id, zone, season, hour_of_day) DO NOTHING;

-- Accessor: point lookup with linear interpolation between hour h and h+1.
-- Nearest-season fallback (spring) + never-NULL: returns the conservative cap
-- so the served line always fails open-to-achievable, never open-to-ideal.
CREATE OR REPLACE FUNCTION public.fn_achievable_envelope(
    p_zone text,
    p_season text,
    p_ts timestamptz,
    p_greenhouse_id text DEFAULT 'vallery'
) RETURNS TABLE(
    env_temp_low_floor double precision,
    env_temp_hi_cap double precision,
    env_temp_achievable_p50 double precision,
    env_vpd_lo_floor double precision,
    env_vpd_hi_cap double precision,
    cap_source text
) LANGUAGE plpgsql STABLE ROWS 1 AS $function$
DECLARE
    h0 int;
    h1 int;
    frac double precision;
    v_season text := p_season;
    r0 record;
    r1 record;
BEGIN
    h0 := EXTRACT(hour FROM p_ts AT TIME ZONE 'America/Denver')::int;
    frac := EXTRACT(minute FROM p_ts AT TIME ZONE 'America/Denver') / 60.0;
    h1 := (h0 + 1) % 24;

    -- nearest-season fallback if requested season absent
    IF NOT EXISTS (SELECT 1 FROM achievable_envelope
                    WHERE greenhouse_id = p_greenhouse_id AND zone = p_zone
                      AND season = v_season AND is_active) THEN
        v_season := 'spring';
    END IF;

    SELECT * INTO r0 FROM achievable_envelope
     WHERE greenhouse_id = p_greenhouse_id AND zone = p_zone
       AND season = v_season AND hour_of_day = h0 AND is_active;
    SELECT * INTO r1 FROM achievable_envelope
     WHERE greenhouse_id = p_greenhouse_id AND zone = p_zone
       AND season = v_season AND hour_of_day = h1 AND is_active;

    IF r0 IS NULL THEN
        -- never NULL: open-to-achievable conservative fallback
        env_temp_low_floor := 55.0;
        env_temp_hi_cap := 90.0;
        env_temp_achievable_p50 := 88.0;
        env_vpd_lo_floor := 0.30;
        env_vpd_hi_cap := 1.80;
        cap_source := 'fallback';
        RETURN NEXT;
        RETURN;
    END IF;

    IF r1 IS NULL THEN r1 := r0; END IF;

    env_temp_low_floor := r0.env_temp_lo_floor + frac * (r1.env_temp_lo_floor - r0.env_temp_lo_floor);
    env_temp_hi_cap := r0.env_temp_hi_cap + frac * (r1.env_temp_hi_cap - r0.env_temp_hi_cap);
    env_temp_achievable_p50 := COALESCE(r0.env_temp_achievable_p50, r0.env_temp_hi_cap)
        + frac * (COALESCE(r1.env_temp_achievable_p50, r1.env_temp_hi_cap)
                  - COALESCE(r0.env_temp_achievable_p50, r0.env_temp_hi_cap));
    env_vpd_lo_floor := COALESCE(r0.env_vpd_lo_floor, 0.30);
    env_vpd_hi_cap := COALESCE(r0.env_vpd_hi_cap, 1.80);
    cap_source := r0.cap_source;
    RETURN NEXT;
END;
$function$;

-- =====================================================================
-- D7 / D8: v_zone_band surface view; repoint v_target_curve BEFORE deprecation
-- =====================================================================
-- Thin surface over fn_zone_band for all five zones at now() (compliance +
-- dashboards). LATERAL into the STABLE fn (this is a view, not a CAgg, so LATERAL-
-- to-fn is fine here; the CAgg constraint applies only to migration 146).
CREATE OR REPLACE VIEW v_zone_band AS
SELECT b.*
  FROM (VALUES ('center'),('east'),('north'),('south'),('west')) z(zone)
  CROSS JOIN LATERAL fn_zone_band(z.zone, now()) b;

COMMENT ON VIEW v_zone_band IS
'Per-zone live grading band (ideal + stress) at now() for all 5 zones. center->orchid; '
'east->intersection(ideal)/union(stress); empty zones->_default. Replaces fn_target_band_smooth as '
'the dashboard band surface (v_target_curve repointed here).';

-- D8: repoint v_target_curve OFF fn_target_band_smooth (which it depends on) and
-- onto v_zone_band BEFORE any deprecation. Preserve the old column names/shape so
-- existing dashboards keep working. v_target_curve historically emitted the smooth
-- center curve over the day at 5-min cadence; we now build it from the served
-- center band (fn_band_setpoints) for the temp/vpd target columns and the
-- center-zone fn_zone_band for the stress columns. This removes the dependency on
-- fn_target_band_smooth so the subsequent DROP cannot CASCADE-delete the view.
CREATE OR REPLACE VIEW v_target_curve AS
SELECT gs AS ts,
       (fn_band_setpoints(gs)).temp_low  AS target_temp_min,
       (fn_band_setpoints(gs)).temp_high AS target_temp_max,
       (fn_zone_band('center', gs)).temp_stress_low  AS stress_temp_low,
       (fn_zone_band('center', gs)).temp_stress_high AS stress_temp_high,
       (fn_band_setpoints(gs)).vpd_low   AS target_vpd_min,
       (fn_band_setpoints(gs)).vpd_high  AS target_vpd_max,
       (fn_zone_band('center', gs)).vpd_stress_low  AS stress_vpd_low,
       (fn_zone_band('center', gs)).vpd_stress_high AS stress_vpd_high,
       12.0::double precision AS target_dli
  FROM generate_series(
         (date_trunc('day', now() AT TIME ZONE 'America/Denver') AT TIME ZONE 'America/Denver'),
         (date_trunc('day', now() AT TIME ZONE 'America/Denver') AT TIME ZONE 'America/Denver') + interval '24 hours',
         interval '5 minutes'
       ) gs(gs);

COMMENT ON VIEW v_target_curve IS
'Served center diurnal target curve (temp/vpd) + center-zone stress edges, 5-min cadence over today. '
'Repointed off the deprecated fn_target_band_smooth onto fn_band_setpoints + fn_zone_band (migration 145).';

-- D8: deprecate fn_target_band / fn_target_band_smooth. v_target_curve no longer
-- depends on either, so we COMMENT-deprecate now. We do NOT DROP fn_target_band_smooth
-- in 145 (one-cycle grace; migration 146 §8.2 step 10 performs the explicit drops
-- after confirming zero readers). fn_target_band keeps the catalog (not name) join
-- so any remaining dashboard reader is correct in the meantime.
CREATE OR REPLACE FUNCTION public.fn_target_band(target_ts timestamp with time zone)
RETURNS TABLE(target_temp_min double precision, target_temp_max double precision,
              stress_temp_low double precision, stress_temp_high double precision,
              target_vpd_min double precision, target_vpd_max double precision,
              stress_vpd_low double precision, stress_vpd_high double precision,
              target_dli double precision)
LANGUAGE sql
STABLE
AS $function$
    SELECT
        MAX(p.temp_ideal_min)::float,
        MIN(p.temp_ideal_max)::float,
        MIN(p.temp_stress_low)::float,
        MAX(p.temp_stress_high)::float,
        MAX(p.vpd_ideal_min)::float,
        MIN(p.vpd_ideal_max)::float,
        MIN(p.vpd_stress_low)::float,
        MAX(p.vpd_stress_high)::float,
        MAX(p.dli_target_mol)::float
    FROM crop_target_profiles p
    JOIN crops c ON c.crop_catalog_id = p.crop_catalog_id
                AND c.is_active AND c.greenhouse_id = p.greenhouse_id
    WHERE p.hour_of_day = EXTRACT(HOUR FROM target_ts AT TIME ZONE 'America/Denver')::int
      AND p.season = fn_current_season()
      AND p.greenhouse_id = 'vallery';
$function$;

COMMENT ON FUNCTION public.fn_target_band(timestamptz) IS
'DEPRECATED (migration 145): house MAX(min)/MIN(max) step band. Use fn_band_setpoints (served) or '
'fn_zone_band (per-zone grading). Repointed to the catalog/is_active join; scheduled for DROP after one cycle.';
COMMENT ON FUNCTION public.fn_target_band_smooth(timestamptz) IS
'DEPRECATED (migration 145): empirical p25-p75 self-referential cosine band. Replaced by fn_band_setpoints '
'(declarative agronomic anchor + achievable envelope). v_target_curve repointed off it; DROP in migration 146.';

-- =====================================================================
-- N1: insert vanda_orchid_active nutrient recipe (is_active=FALSE)
-- =====================================================================
-- crop_id 5 = Vanda Orchids. Single-salt MSU 13-3-15 RO formula. is_active=FALSE
-- until the operator confirms salt on-hand + dosing path (avoid blind dose, SAF-2).
-- nutrient_recipes has no salt_model/product_name column today (flagged to
-- coordinator in design §5.3); the single-salt nature is documented in notes and
-- stock_a/stock_b are left NULL so no A/B dose math runs. Bounce verdify-mcp so the
-- recipe enters plan context.
INSERT INTO nutrient_recipes
  (name, crop_id, stage, target_ec, target_ph_low, target_ph_high,
   n_ppm, p_ppm, k_ppm, ca_ppm, mg_ppm, fe_ppm, stock_a_ml_per_l, stock_b_ml_per_l, notes, is_active)
SELECT 'vanda_orchid_active', 5, 'vegetative', 0.40, 5.6, 6.2,
   50, 11.5, 57.7, 30.8, 7.7, 1.5, NULL, NULL,
   'Bare-root Vanda RO feed v1.0. SINGLE-SALT MSU 13-3-15 (8Ca-2Mg) RO formula / alt Jacks 15-5-15 CalMag LX. '
   'target_ec ABSOLUTE on RO base (~0), = spec +0.3-0.5 over RO. P/K computed as ELEMENTAL (not oxide). '
   'stock_a/stock_b NULL: NOT 2-part GH Flora -- do NOT use A/B ml/L dose math; dose by mixing to target_ec. '
   'AM feed only; 60-90min absorption hold after; NO organics/particulate (FRT-2).',
   FALSE
WHERE NOT EXISTS (SELECT 1 FROM nutrient_recipes WHERE name = 'vanda_orchid_active');
