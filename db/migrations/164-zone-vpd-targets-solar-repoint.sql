-- 164-zone-vpd-targets-solar-repoint.sql
-- =============================================================================
-- Firmware v2 (#287 / contract B7): re-point fn_zone_vpd_targets onto the
-- canonical crop_band_anchors table — south→cannabis, west→citrus(lime),
-- east→pepper, center→orchid — keyed to SOLAR PHASE, with NO hardcoded
-- season literals.
--
-- Depends on migration 161 (crop_band_anchors + seeds; guarded below) and on
-- the migration-145 solar helpers (fn_solar_sunrise_hour / fn_solar_sunset_hour
-- / fn_current_season; guarded below).
--
-- WHAT CHANGES (previous definition read 2026-06-10 via
--   pg_get_functiondef('fn_zone_vpd_targets(timestamptz)'::regprocedure)):
--   * OLD: zone→crop derived from active `crops` rows (CASE on crops.name),
--     hour-of-day linear interpolation over crop_target_profiles.vpd_ideal_max
--     (per-zone MIN across co-resident crops), with a `v_season := 'spring'`
--     fallback when the current season has no profile rows.
--   * NEW: explicit zone→crop wiring per contract B7 (south→cannabis,
--     west→citrus, east→pepper, center→orchid — design §3.4: "trivially
--     re-pointed" by editing this one mapping), cosine interpolation between
--     the 4 solar anchors of the crop's vpd_target curve in crop_band_anchors
--     (exactly mirroring the ESP32's band_value_at_phase, contract B2), and
--     season resolved ONLY via fn_current_season() — the seeded anchors are
--     season='all', so no 'spring'/'summer' literal exists anywhere in the
--     chain (passes the migration-159 #253 tripwire, re-runnable after this).
--   * INTERFACE IDENTICAL: same argument, same OUT columns
--     (vpd_target_south, vpd_target_west, vpd_target_east, vpd_target_center;
--     all double precision), STABLE, ROWS 1 — consumers
--     (scripts/setpoint-server.py, ingestor/tasks/dispatcher.py, api/main.py:
--     `SELECT * FROM fn_zone_vpd_targets(now())`) are untouched.
--   * SEMANTIC SHIFT (intentional, the point of the contract): the returned
--     number is now the deterministic zone vpd TARGET curve value, not the
--     crop_target_profiles vpd_ideal_max ceiling. Band min/max remain
--     reconstructible: target -/+ the crop_band_anchors half-widths.
--     crop_target_profiles / fn_zone_band compliance GRADING is untouched.
--   * Explicit wiring also makes east deterministic: the legacy active-crops
--     join would have MIN'd pepper with the still-active lettuce/strawberry
--     rows in east; contract B7 says east→pepper.
--
-- New helpers (both reusable by the B7 compliance-view re-key work):
--   fn_solar_phase(ts)      — contract-B1 phase in [0,4): 0=SR, 1=solar noon,
--                             2=SS, 3=solar midnight; built on the existing
--                             migration-145 sunrise/sunset bisection helpers.
--                             Pre-dawn hours reuse the same calendar day's
--                             SR/SS for "last night" (anchor drift across
--                             midnight is ~2 min/day — negligible; the
--                             dispatcher's astral ephemeris is the audit ref).
--   fn_crop_band_value(...) — anchor lookup (growth_stage/season fallback to
--                             'default'/'all') + cosine interpolation; NULL if
--                             the crop has no complete 4-anchor set, so
--                             callers can fall back deterministically.
--
-- #23 rollback-replay safety: NON-self-transactional — no top-level
-- BEGIN;/COMMIT; and no commit-forcing statement. SAFE to wrap in an outer
-- `BEGIN; ... ROLLBACK;` for rollback validation. Idempotent: guards +
-- CREATE OR REPLACE only.
--
-- RESTARTS (CLAUDE.md rule 7): does not touch verdify_schemas/**,
-- ingestor/entity_map.py, or mcp/server.py — no restart obligation. The
-- dispatcher resolves fn_zone_vpd_targets per cycle (no caching), so the new
-- definition takes effect on the next cycle without a bounce.
--
-- ROLLBACK (documented; for a disposable validation DB, never live): restore
-- the previous fn_zone_vpd_targets body from db/schema.sql (or
-- pg_get_functiondef on a pre-164 DB), then:
--   DROP FUNCTION IF EXISTS public.fn_crop_band_value(text,text,timestamptz,text,text,text);
--   DROP FUNCTION IF EXISTS public.fn_solar_phase(timestamptz);
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────
-- 164.0  Guards: 161 applied; 145 solar/season helpers present.
-- ─────────────────────────────────────────────────────────────────────
DO $guard$
DECLARE
    n int;
BEGIN
    IF to_regclass('public.crop_band_anchors') IS NULL THEN
        RAISE EXCEPTION 'migration 164 prerequisite: crop_band_anchors missing — apply migration 161 first';
    END IF;
    SELECT count(DISTINCT anchor) INTO n
      FROM public.crop_band_anchors
     WHERE crop_type = 'house' AND series = 'vpd_target' AND greenhouse_id = 'vallery';
    IF n < 4 THEN
        RAISE EXCEPTION 'migration 164 prerequisite: house vpd_target anchor set incomplete (%/4) — apply migration 161 first', n;
    END IF;
    IF to_regprocedure('public.fn_solar_sunrise_hour(timestamptz)') IS NULL
       OR to_regprocedure('public.fn_solar_sunset_hour(timestamptz)') IS NULL
       OR to_regprocedure('public.fn_current_season()') IS NULL THEN
        RAISE EXCEPTION 'migration 164 prerequisite: migration-145 solar/season helpers missing';
    END IF;
END
$guard$;

-- ─────────────────────────────────────────────────────────────────────
-- 164.1  fn_solar_phase: contract-B1 solar phase in [0,4)
-- ─────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.fn_solar_phase(target_ts timestamptz)
RETURNS double precision
LANGUAGE plpgsql
STABLE
AS $function$
DECLARE
    h     double precision;  -- local decimal hour of target_ts
    sr    double precision;  -- sunrise, local decimal hours
    ss    double precision;  -- sunset, local decimal hours
    sm    double precision;  -- solar noon ~ midpoint(SR, SS)
    hh    double precision;
    mid_n double precision;  -- solar midnight ~ midpoint(SS, SR+24)
BEGIN
    h  := EXTRACT(epoch FROM (target_ts AT TIME ZONE 'America/Denver')
                          - date_trunc('day', target_ts AT TIME ZONE 'America/Denver')) / 3600.0;
    sr := fn_solar_sunrise_hour(target_ts);
    ss := fn_solar_sunset_hour(target_ts);
    sm := (sr + ss) / 2.0;

    IF h >= sr AND h < sm THEN
        RETURN (h - sr) / (sm - sr);                -- [0,1): sunrise -> solar noon
    ELSIF h >= sm AND h < ss THEN
        RETURN 1.0 + (h - sm) / (ss - sm);          -- [1,2): solar noon -> sunset
    END IF;

    -- Night half [2,4): sunset -> solar midnight -> next sunrise. Pre-dawn
    -- hours (h < sr) wrap forward by 24h and reuse this day's SR/SS.
    hh    := CASE WHEN h < sr THEN h + 24.0 ELSE h END;
    mid_n := (ss + sr + 24.0) / 2.0;
    IF hh < mid_n THEN
        RETURN 2.0 + (hh - ss) / (mid_n - ss);      -- [2,3): sunset -> solar midnight
    END IF;
    RETURN LEAST(3.0 + (hh - mid_n) / (sr + 24.0 - mid_n), 3.9999999);  -- [3,4)
END;
$function$;

COMMENT ON FUNCTION public.fn_solar_phase(timestamptz) IS
'Contract-B1 solar phase in [0,4): 0=sunrise, 1=solar noon, 2=sunset, 3=solar '
'midnight (migration 164). DB mirror of the ESP32 solar_phase(); reuses the '
'migration-145 sunrise/sunset bisection helpers (lat 40.167, lon -105.102).';

-- ─────────────────────────────────────────────────────────────────────
-- 164.2  fn_crop_band_value: anchor lookup + cosine interpolation
-- ─────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.fn_crop_band_value(
    p_crop_type     text,
    p_series        text,
    p_ts            timestamptz,
    p_season        text DEFAULT NULL,        -- NULL -> fn_current_season()
    p_growth_stage  text DEFAULT 'default',
    p_greenhouse_id text DEFAULT 'vallery'
) RETURNS double precision
LANGUAGE plpgsql
STABLE
AS $function$
DECLARE
    v_season text := COALESCE(p_season, fn_current_season());
    v_stage  text := COALESCE(p_growth_stage, 'default');
    v_sr     double precision;
    v_sm     double precision;
    v_ss     double precision;
    v_mid    double precision;
    phase    double precision;
    seg      int;
    t        double precision;
    a        double precision;
    b        double precision;
BEGIN
    -- Resolve one row per anchor, preferring exact growth_stage/season matches
    -- over the 'default'/'all' fallbacks.
    SELECT max(value) FILTER (WHERE anchor = 'sr'),
           max(value) FILTER (WHERE anchor = 'sm'),
           max(value) FILTER (WHERE anchor = 'ss'),
           max(value) FILTER (WHERE anchor = 'mid')
      INTO v_sr, v_sm, v_ss, v_mid
      FROM (
        SELECT DISTINCT ON (anchor) anchor, value
          FROM public.crop_band_anchors
         WHERE crop_type     = p_crop_type
           AND series        = p_series
           AND greenhouse_id = p_greenhouse_id
           AND growth_stage IN (v_stage, 'default')
           AND season       IN (v_season, 'all')
         ORDER BY anchor,
                  (growth_stage = v_stage) DESC,
                  (season = v_season) DESC
      ) resolved;

    IF v_sr IS NULL OR v_sm IS NULL OR v_ss IS NULL OR v_mid IS NULL THEN
        RETURN NULL;  -- incomplete anchor set -> caller falls back
    END IF;

    -- Cosine interpolation between consecutive anchors (sr->sm->ss->mid->sr),
    -- mirroring the ESP32 band_value_at_phase() (contract B2).
    phase := fn_solar_phase(p_ts);
    seg   := floor(phase)::int;       -- 0..3
    t     := phase - seg;
    a := CASE seg WHEN 0 THEN v_sr WHEN 1 THEN v_sm WHEN 2 THEN v_ss ELSE v_mid END;
    b := CASE seg WHEN 0 THEN v_sm WHEN 1 THEN v_ss WHEN 2 THEN v_mid ELSE v_sr END;
    RETURN a + (b - a) * (1.0 - cos(pi() * t)) / 2.0;
END;
$function$;

COMMENT ON FUNCTION public.fn_crop_band_value(text, text, timestamptz, text, text, text) IS
'Deterministic crop+solar band value (migration 164, contract B2/B7): cosine '
'interpolation between the crop_band_anchors 4-point curve at fn_solar_phase(ts). '
'growth_stage/season fall back to ''default''/''all''. NULL when the crop lacks a '
'complete anchor set (callers fall back to the house curve).';

-- ─────────────────────────────────────────────────────────────────────
-- 164.3  fn_zone_vpd_targets: explicit contract-B7 zone wiring
-- ─────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.fn_zone_vpd_targets(target_ts timestamp with time zone)
RETURNS TABLE(vpd_target_south double precision,
              vpd_target_west  double precision,
              vpd_target_east  double precision,
              vpd_target_center double precision)
LANGUAGE plpgsql
STABLE ROWS 1
AS $function$
DECLARE
    -- Season resolved ONCE via fn_current_season() and passed down — no
    -- hardcoded season literal anywhere (migration-159 #253 tripwire).
    v_season text := fn_current_season();
    v_house  double precision;
BEGIN
    -- House vpd_target curve = deterministic fallback for any zone whose crop
    -- lacks a complete anchor set; the trailing literals are a last-resort
    -- safety net only (kept from the pre-164 definition).
    v_house := fn_crop_band_value('house', 'vpd_target', target_ts, v_season);

    -- Zone -> crop wiring per contract B7 (design §3.4: re-point by editing
    -- this mapping): south→cannabis, west→citrus(lime), east→pepper,
    -- center→orchid(Vanda).
    RETURN QUERY SELECT
        COALESCE(fn_crop_band_value('cannabis', 'vpd_target', target_ts, v_season), v_house, 1.5),
        COALESCE(fn_crop_band_value('citrus',   'vpd_target', target_ts, v_season), v_house, 1.5),
        COALESCE(fn_crop_band_value('pepper',   'vpd_target', target_ts, v_season), v_house, 1.0),
        COALESCE(fn_crop_band_value('orchid',   'vpd_target', target_ts, v_season), v_house, 0.85);
END;
$function$;

COMMENT ON FUNCTION public.fn_zone_vpd_targets(timestamptz) IS
'Per-zone deterministic VPD targets (migration 164, contract B7): cosine-'
'interpolated crop_band_anchors vpd_target curves at the solar phase. Wiring: '
'south=cannabis, west=citrus(lime), east=pepper, center=orchid; house curve '
'fallback. Min/max reconstruct as target -/+ crop_band_anchors widths. '
'Supersedes the crops x crop_target_profiles vpd_ideal_max resolution '
'(crop_target_profiles remains the compliance GRADING source).';

-- ─────────────────────────────────────────────────────────────────────
-- 164.4  Smoke assertion: phase in range, 4 sane zone targets NOW.
-- ─────────────────────────────────────────────────────────────────────
DO $assert$
DECLARE
    ph  double precision;
    rec record;
BEGIN
    ph := fn_solar_phase(now());
    IF ph IS NULL OR ph < 0.0 OR ph >= 4.0 THEN
        RAISE EXCEPTION 'migration 164: fn_solar_phase(now()) out of [0,4): %', ph;
    END IF;

    SELECT * INTO rec FROM fn_zone_vpd_targets(now());
    IF rec.vpd_target_south  IS NULL OR rec.vpd_target_south  NOT BETWEEN 0.2 AND 2.5 OR
       rec.vpd_target_west   IS NULL OR rec.vpd_target_west   NOT BETWEEN 0.2 AND 2.5 OR
       rec.vpd_target_east   IS NULL OR rec.vpd_target_east   NOT BETWEEN 0.2 AND 2.5 OR
       rec.vpd_target_center IS NULL OR rec.vpd_target_center NOT BETWEEN 0.2 AND 2.5 THEN
        RAISE EXCEPTION 'migration 164: fn_zone_vpd_targets(now()) insane: s=% w=% e=% c=%',
            rec.vpd_target_south, rec.vpd_target_west, rec.vpd_target_east, rec.vpd_target_center;
    END IF;
    RAISE NOTICE 'migration 164 OK: solar_phase=%, zone vpd targets s=% w=% e=% c=%.',
        round(ph::numeric, 3), round(rec.vpd_target_south::numeric, 3),
        round(rec.vpd_target_west::numeric, 3), round(rec.vpd_target_east::numeric, 3),
        round(rec.vpd_target_center::numeric, 3);
END
$assert$;
