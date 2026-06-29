-- 170-band-harmonic-smooth-curve.sql
-- =============================================================================
-- Make the band curve SMOOTH (kill the lumpiness, Jason 2026-06-15, esp. temp).
--
-- The band was interpolated by a PIECEWISE cosine-ease between the 4 solar
-- anchors (SR/SM/SS/MID): value = a + (b-a)*(1-cos(pi*t))/2. A cosine-ease has
-- ZERO slope at both ends of every segment, so the curve flattens into a little
-- plateau at each of the 4 anchors and steepens between them — four plateaus =
-- a visibly "lumpy" wave, worst on temp whose anchors are far apart (66/84/73/65).
--
-- Replace it with a single SMOOTH periodic function through the same 4 anchors:
-- a 4-point harmonic (discrete-Fourier) interpolation. With anchors at solar
-- phase 0/1/2/3 (SR/noon/SS/solar-midnight) and theta = pi*phase/2:
--     c0 = (sr+sm+ss+mid)/4      c1 = (sr-ss)/2
--     s1 = (sm-mid)/2            c2 = (sr-sm+ss-mid)/4
--     value = c0 + c1*cos(theta) + s1*sin(theta) + c2*cos(2*theta)
-- This passes EXACTLY through every anchor (phase 0->sr, 1->sm, 2->ss, 3->mid)
-- but is C-infinity smooth — non-zero, matched slopes at the anchors, no plateaus.
-- "Compute it on a curve" — one harmonic curve, not four stitched eases.
--
-- Scope: fn_crop_band_value is the single interpolation point — fn_zone_vpd_targets,
-- fn_band_setpoints, fn_band_timeline, mv_band_curve and the graphs all read
-- through it, so every band (house + per-zone, graph + audit) smooths at once.
-- The firmware greenhouse_solar.h band_value_at_phase + ingestor/solar.py mirror
-- get the identical harmonic interp in the companion firmware OTA so the device
-- tracks the same smooth curve (until then the on-chip cosine-ease differs by
-- <~1F/<~0.05kPa between anchors — within the safety rails).
--
-- Anchors are UNCHANGED (migration 168 dry-night values stand); only the
-- interpolation BETWEEN them changes. Anchor values are bounded; the harmonic
-- fit may overshoot a hair between anchors but the firmware safety_* rails clamp.
--
-- ROLLBACK SAFETY: CREATE OR REPLACE FUNCTION only, no schema change, no
-- CONCURRENTLY, no top-level COMMIT -> NON-self-transactional, safe-to-wrap.
-- Revert: restore the cosine-ease body (the prior RETURN line).
-- =============================================================================

\set ON_ERROR_STOP on

CREATE OR REPLACE FUNCTION public.fn_crop_band_value(
    p_crop_type text, p_series text, p_ts timestamp with time zone,
    p_season text DEFAULT NULL::text, p_growth_stage text DEFAULT 'default'::text,
    p_greenhouse_id text DEFAULT 'vallery'::text)
 RETURNS double precision
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
    theta    double precision;
    c0 double precision; c1 double precision; s1 double precision; c2 double precision;
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

    -- Smooth 4-anchor harmonic interpolation (no plateaus). Passes through
    -- SR/SM/SS/MID at solar phase 0/1/2/3; mirrors greenhouse_solar.h.
    phase := fn_solar_phase(p_ts);          -- continuous 0..4
    theta := pi() * phase / 2.0;            -- 0..2π
    c0 := (v_sr + v_sm + v_ss + v_mid) / 4.0;
    c1 := (v_sr - v_ss) / 2.0;
    s1 := (v_sm - v_mid) / 2.0;
    c2 := (v_sr - v_sm + v_ss - v_mid) / 4.0;
    RETURN c0 + c1 * cos(theta) + s1 * sin(theta) + c2 * cos(2.0 * theta);
END;
$function$;

COMMENT ON FUNCTION public.fn_crop_band_value(text,text,timestamptz,text,text,text) IS
'Deterministic crop+solar band value. Smooth 4-anchor harmonic (Fourier) '
'interpolation through SR/SM/SS/MID (migration 170) — passes through every '
'anchor, C-infinity smooth (no cosine-ease plateaus). Mirrors firmware '
'greenhouse_solar.h band_value_at_phase.';
