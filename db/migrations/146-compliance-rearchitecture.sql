-- Migration 146: Graded + feasibility-aware compliance rearchitecture (ADDITIVE / dual-write)
--
-- Implements docs/design/band-compliance-architecture.md §6 (compliance engine),
-- §7 (consolidation), §8.2 (migration plan). Backlog items G1-G6.
--
-- STRICTLY AFTER migration 145 (a RAISE EXCEPTION guard blocks it until 145's
-- orchid stress bounds 55/100 are applied). ADDITIVE ONLY: the existing binary
-- compliance_pct / temp_compliance_pct / vpd_compliance_pct / stress_hours_*
-- columns are NOT dropped or mutated. The graded engine dual-writes into new
-- *_v2 columns. NO reward number moves here (that is migration 147).
--
-- DB-only, off the live control path (setpoint-server.py:309-311 reads only
-- fn_band_setpoints / fn_house_vpd_control_band / fn_zone_vpd_targets, never any
-- compliance function). firmware-replay-diff THRESHOLD_PCT=0 stays green; no OTA,
-- no 48h bake, no weekly-OTA budget. Attach
--   make firmware-replay OLD=<145-base> NEW=146   (expect 0% divergence).
--
-- RESTARTS (CLAUDE.md rule 7): bounce verdify-ingestor (writes the new columns via
-- tasks.py) + verdify-mcp (scorecard/context read the rollups). No dispatcher
-- bounce (it does not read compliance). The refresh_achievable_envelope job and
-- the mv_zone_band_grade matview refresh are ingestor-owned: bounce verdify-ingestor.

-- =====================================================================
-- 146.1  GUARD: 145 must be applied first (orchid stress bounds = 55/100)
-- =====================================================================
DO $guard$
DECLARE
    v_stress_high double precision;
    v_season text := fn_current_season();
BEGIN
    -- nearest-season fallback so the guard works pre/post June-1
    IF NOT EXISTS (SELECT 1 FROM crop_target_profiles
                    WHERE crop_type='orchid' AND season=v_season) THEN
        v_season := 'spring';
    END IF;
    SELECT MAX(temp_stress_high) INTO v_stress_high
      FROM crop_target_profiles
     WHERE crop_type='orchid' AND season=v_season AND greenhouse_id='vallery';

    IF v_stress_high IS NULL THEN
        RAISE EXCEPTION 'Migration 146 prerequisite failed: no orchid profile rows for season % (apply migration 145 first).', v_season;
    END IF;
    IF v_stress_high <> 100 THEN
        RAISE EXCEPTION 'Migration 146 prerequisite failed: orchid temp_stress_high=% (expected 100). Apply migration 145 D2 first.', v_stress_high;
    END IF;

    -- fn_zone_band and the envelope objects from 145 must exist.
    IF to_regprocedure('fn_zone_band(text,timestamptz,text)') IS NULL THEN
        RAISE EXCEPTION 'Migration 146 prerequisite failed: fn_zone_band missing (apply migration 145 first).';
    END IF;
    IF to_regclass('achievable_envelope') IS NULL THEN
        RAISE EXCEPTION 'Migration 146 prerequisite failed: achievable_envelope table missing (apply migration 145 first).';
    END IF;
END
$guard$;

-- =====================================================================
-- 146.2  fn_grade_credit — graded piecewise scalar (decision #2)  [G1]
-- =====================================================================
-- 1.0 in the ideal band, linear partial credit across each stress shoulder, 0
-- beyond stress. Continuous at all band edges. Pure scalar, IMMUTABLE.
CREATE OR REPLACE FUNCTION public.fn_grade_credit(
  x numeric, stress_lo numeric, ideal_lo numeric, ideal_hi numeric, stress_hi numeric
) RETURNS numeric LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE
    WHEN x IS NULL THEN NULL
    WHEN x BETWEEN ideal_lo AND ideal_hi THEN 1.0
    WHEN x < stress_lo OR x > stress_hi THEN 0.0
    WHEN x < ideal_lo  THEN GREATEST(0, (x - stress_lo)/NULLIF(ideal_lo - stress_lo, 0))
    ELSE                    GREATEST(0, (stress_hi - x)/NULLIF(stress_hi - ideal_hi, 0))
  END;
$$;

COMMENT ON FUNCTION public.fn_grade_credit(numeric,numeric,numeric,numeric,numeric) IS
'Graded compliance credit g in [0,1] for reading x against (stress_lo, ideal_lo, ideal_hi, stress_hi). '
'1.0 in ideal, linear partial across each stress shoulder, 0 beyond. Continuous at all edges. '
'Apply independently to temp and VPD; zone sample score = sqrt(g_temp*g_vpd). (band-compliance design §6.1)';

-- =====================================================================
-- 146.5  fn_band_setpoints WRAP — envelope clamp + non-center safety (decision #3/#1)
-- =====================================================================
-- Replaces the migration-145 body with the achievable-envelope-clamped served
-- line. The orchid center band is clamped DOWN to the achievable cap and UP to a
-- safety floor, and bounded by the active non-center crops' stress envelope. This
-- systematizes the Vanda hand-set 86-88F cap (design §4.2/§5.4). Still a drop-in:
-- same name/signature the dispatcher calls (setpoint-server.py:309).
CREATE OR REPLACE FUNCTION public.fn_band_setpoints(target_ts timestamptz)
RETURNS TABLE(temp_low double precision, temp_high double precision,
              vpd_low double precision, vpd_high double precision)
LANGUAGE plpgsql
STABLE ROWS 1
AS $function$
DECLARE
    c record;
    env record;
    v_floor double precision;
    v_ceil double precision;
    v_vlow double precision;
    v_vhigh double precision;
BEGIN
    SELECT * INTO c FROM fn_center_band_setpoints(target_ts);          -- orchid smooth band (145)
    SELECT * INTO env FROM fn_achievable_envelope('center', fn_current_season(), target_ts);

    -- decision #1 safety: never serve a ceiling above any active non-center
    -- crop's temp_stress_high, nor a floor below its temp_stress_low.
    SELECT MAX(s.temp_stress_low), MIN(s.temp_stress_high)
      INTO v_floor, v_ceil
      FROM fn_active_noncenter_stress(target_ts) s;

    -- temp_low  = max(center, non-center safety floor, envelope floor)
    -- temp_high = min(center, envelope cap, non-center safety ceiling)
    temp_low  := GREATEST(c.temp_low,
                          COALESCE(v_floor, c.temp_low),
                          COALESCE(env.env_temp_low_floor, c.temp_low));
    temp_high := LEAST(c.temp_high,
                       COALESCE(env.env_temp_hi_cap, c.temp_high),
                       COALESCE(v_ceil, c.temp_high));
    IF temp_low > temp_high THEN temp_low := temp_high; END IF;

    -- VPD served line = house control band (non-orchid), inversion-clamped.
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
-- 146.7a  compliance_zone_weights — priority weights (decision #1)  [G4]
-- =====================================================================
CREATE TABLE IF NOT EXISTS compliance_zone_weights (
  greenhouse_id text NOT NULL DEFAULT 'vallery',
  zone text NOT NULL,
  weight double precision NOT NULL,
  PRIMARY KEY (greenhouse_id, zone)
);

COMMENT ON TABLE compliance_zone_weights IS
'Per-zone weights for the priority-weighted house compliance aggregate (band-compliance design §6.3). '
'Seed: center 0.60 (priority, served line follows it), east 0.40 (food crops), north/south/west 0 '
'(no active crop; graded for dashboards but excluded from the house reward).';

INSERT INTO compliance_zone_weights (greenhouse_id, zone, weight) VALUES
  ('vallery','center',0.60),
  ('vallery','east',0.40),
  ('vallery','north',0.0),
  ('vallery','south',0.0),
  ('vallery','west',0.0)
ON CONFLICT (greenhouse_id, zone) DO NOTHING;

-- =====================================================================
-- 146.6  fn_zone_band_grade(start,end) — per-minute x per-zone graded credit  [G2]
-- =====================================================================
-- Extends the EXISTING time-bounded fn_band_trace shape (NOT the 5-UNION
-- v_setpoint_compliance). Per climate minute x graded zone {center,east,north}:
--   * per-zone reading: center=temp_avg/vpd_avg proxy; east=temp_east/vpd_east;
--     north=temp_north/vpd_north
--   * per-zone agronomic band from fn_zone_band (center=orchid; east=∩/∪; north=_default)
--   * served band from fn_band_setpoints (one house line) for feasibility temp ref
--   * g_temp, g_vpd via fn_grade_credit; zone_score = sqrt(g_temp*g_vpd)
--   * feasibility from forward-filled equipment_state relay state + outdoor temp
-- Time-bounded by argument (NEVER full-table) -> resolves the v_setpoint_compliance
-- >120s timeout (M11).
CREATE OR REPLACE FUNCTION public.fn_zone_band_grade(
    p_start timestamptz,
    p_end   timestamptz,
    p_greenhouse_id text DEFAULT 'vallery'
) RETURNS TABLE(
    ts timestamptz,
    zone text,
    reading_temp double precision,
    reading_vpd double precision,
    g_temp numeric,
    g_vpd numeric,
    zone_score numeric,
    feasibility text,
    proxy_center boolean
) LANGUAGE sql STABLE ROWS 100000 AS $function$
WITH climate_window AS (
    SELECT c.ts,
           c.temp_avg, c.vpd_avg,
           c.temp_east, c.vpd_east,
           c.temp_north, c.vpd_north,
           c.outdoor_temp_f, c.outdoor_rh_pct, c.rh_avg
      FROM climate c
     WHERE c.greenhouse_id = p_greenhouse_id
       AND c.ts >= p_start AND c.ts <= p_end
       AND c.temp_avg IS NOT NULL AND c.vpd_avg IS NOT NULL
),
-- per-(minute,zone) reading + agronomic band
zoned AS (
    SELECT cw.ts, z.zone,
           CASE z.zone WHEN 'center' THEN cw.temp_avg
                       WHEN 'east'   THEN cw.temp_east
                       WHEN 'north'  THEN cw.temp_north END AS reading_temp,
           CASE z.zone WHEN 'center' THEN cw.vpd_avg
                       WHEN 'east'   THEN cw.vpd_east
                       WHEN 'north'  THEN cw.vpd_north END AS reading_vpd,
           cw.outdoor_temp_f,
           b.temp_low, b.temp_high, b.temp_stress_low, b.temp_stress_high,
           b.vpd_low, b.vpd_high, b.vpd_stress_low, b.vpd_stress_high,
           b.is_proxy,
           sb.temp_high AS served_temp_high
      FROM climate_window cw
      CROSS JOIN (VALUES ('center'),('east'),('north')) z(zone)
      CROSS JOIN LATERAL fn_zone_band(z.zone, cw.ts, p_greenhouse_id) b
      CROSS JOIN LATERAL fn_band_setpoints(cw.ts) sb
),
-- forward-fill relay state from equipment_state (the existing precedent;
-- relay_truth window since 2026-05-24 is captured implicitly via these events).
relays AS (
    SELECT z.ts, z.zone, z.reading_temp, z.reading_vpd, z.outdoor_temp_f,
           z.temp_low, z.temp_high, z.temp_stress_low, z.temp_stress_high,
           z.vpd_low, z.vpd_high, z.vpd_stress_low, z.vpd_stress_high,
           z.is_proxy, z.served_temp_high,
           (SELECT e.state FROM equipment_state e WHERE e.equipment='vent'
              AND e.greenhouse_id=p_greenhouse_id AND e.ts <= z.ts ORDER BY e.ts DESC LIMIT 1) AS vent_on,
           (SELECT e.state FROM equipment_state e WHERE e.equipment='fan1'
              AND e.greenhouse_id=p_greenhouse_id AND e.ts <= z.ts ORDER BY e.ts DESC LIMIT 1) AS fan1_on,
           (SELECT e.state FROM equipment_state e WHERE e.equipment='fan2'
              AND e.greenhouse_id=p_greenhouse_id AND e.ts <= z.ts ORDER BY e.ts DESC LIMIT 1) AS fan2_on,
           (SELECT e.state FROM equipment_state e WHERE e.equipment='heat1'
              AND e.greenhouse_id=p_greenhouse_id AND e.ts <= z.ts ORDER BY e.ts DESC LIMIT 1) AS heat1_on,
           (SELECT e.state FROM equipment_state e WHERE e.equipment='heat2'
              AND e.greenhouse_id=p_greenhouse_id AND e.ts <= z.ts ORDER BY e.ts DESC LIMIT 1) AS heat2_on,
           (SELECT e.state FROM equipment_state e WHERE e.equipment='fog'
              AND e.greenhouse_id=p_greenhouse_id AND e.ts <= z.ts ORDER BY e.ts DESC LIMIT 1) AS fog_on,
           (SELECT e.state FROM equipment_state e WHERE e.equipment='mister_any'
              AND e.greenhouse_id=p_greenhouse_id AND e.ts <= z.ts ORDER BY e.ts DESC LIMIT 1) AS mister_on,
           -- did ANY relay event exist before this ts? (feasibility_unknown before relay coverage)
           EXISTS (SELECT 1 FROM equipment_state e
                    WHERE e.greenhouse_id=p_greenhouse_id AND e.ts <= z.ts) AS have_relay
      FROM zoned z
),
graded AS (
    SELECT r.*,
           fn_grade_credit(r.reading_temp::numeric, r.temp_stress_low::numeric, r.temp_low::numeric,
                           r.temp_high::numeric, r.temp_stress_high::numeric) AS gt,
           fn_grade_credit(r.reading_vpd::numeric, r.vpd_stress_low::numeric, r.vpd_low::numeric,
                           r.vpd_high::numeric, r.vpd_stress_high::numeric) AS gv
      FROM relays r
)
SELECT
    g.ts,
    g.zone,
    g.reading_temp,
    g.reading_vpd,
    g.gt AS g_temp,
    g.gv AS g_vpd,
    CASE WHEN g.gt IS NULL OR g.gv IS NULL THEN NULL
         ELSE sqrt(GREATEST(g.gt,0) * GREATEST(g.gv,0)) END AS zone_score,
    -- feasibility classifier (band-compliance design §6.2)
    CASE
      WHEN NOT g.have_relay THEN 'feasibility_unknown'
      WHEN g.gt IS NOT NULL AND g.gt < 1 AND g.reading_temp > g.temp_high THEN  -- HOT miss
           CASE WHEN COALESCE(g.vent_on,false) AND g.outdoor_temp_f >= g.served_temp_high THEN 'unachievable'
                WHEN COALESCE(g.vent_on,false) AND COALESCE(g.fan1_on,false) AND COALESCE(g.fan2_on,false)
                     AND g.outdoor_temp_f >= g.reading_temp THEN 'unachievable'
                ELSE 'controller' END
      WHEN g.gt IS NOT NULL AND g.gt < 1 AND g.reading_temp < g.temp_low THEN   -- COLD miss
           CASE WHEN COALESCE(g.heat1_on,false) AND COALESCE(g.heat2_on,false) THEN 'unachievable'
                ELSE 'controller' END
      WHEN g.gv IS NOT NULL AND g.gv < 1 AND g.reading_vpd > g.vpd_high THEN     -- VPD-HIGH (too dry)
           CASE WHEN COALESCE(g.vent_on,false)
                     AND (g.reading_temp > g.temp_high OR g.outdoor_temp_f >= g.served_temp_high) THEN 'unachievable'
                WHEN COALESCE(g.fog_on,false) OR COALESCE(g.mister_on,false) THEN 'unachievable'
                ELSE 'controller' END
      WHEN g.gv IS NOT NULL AND g.gv < 1 AND g.reading_vpd < g.vpd_low THEN      -- VPD-LOW (too humid)
           'controller'
      ELSE 'none'  -- no miss
    END AS feasibility,
    g.is_proxy AS proxy_center
FROM graded g;
$function$;

COMMENT ON FUNCTION public.fn_zone_band_grade(timestamptz,timestamptz,text) IS
'Per-minute x per-zone {center,east,north} graded credit (g_temp,g_vpd,zone_score=sqrt) + feasibility '
'label (controller|unachievable|feasibility_unknown|none). Extends fn_band_trace (time-bounded); replaces '
'the v_setpoint_compliance 5-UNION (resolves M11). center uses temp_avg/vpd_avg proxy (proxy_center=true).';

-- Materialization: a PLAIN materialized view (pg_cron NOT installed; CAgg-on-view +
-- LATERAL-to-fn is INVALID in TimescaleDB 2.25.2). Refreshed by the ingestor
-- (REFRESH MATERIALIZED VIEW CONCURRENTLY mv_zone_band_grade). Stores per
-- (bucket,zone) graded sums + feasibility split. Seeded over a recent window so it
-- is non-empty on creation; the ingestor extends it.
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_zone_band_grade AS
SELECT
    time_bucket('1 hour', g.ts) AS bucket,
    g.zone,
    count(*) AS n,
    sum(g.g_temp) AS sum_g_temp,
    sum(g.g_vpd) AS sum_g_vpd,
    sum(g.zone_score) AS sum_zone_score,
    sum(g.zone_score) FILTER (WHERE g.feasibility = 'unachievable') AS sum_score_unachievable,
    count(*) FILTER (WHERE g.feasibility = 'unachievable') AS n_unachievable,
    count(*) FILTER (WHERE g.feasibility = 'controller') AS n_controller,
    count(*) FILTER (WHERE g.feasibility = 'feasibility_unknown') AS n_unknown,
    bool_or(g.proxy_center) AS proxy_center
FROM fn_zone_band_grade(now() - interval '3 days', now()) g
GROUP BY 1, 2
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS mv_zone_band_grade_pk
    ON mv_zone_band_grade (bucket, zone);

COMMENT ON MATERIALIZED VIEW mv_zone_band_grade IS
'Hourly per-zone graded-compliance rollup (sums of g_temp/g_vpd/zone_score + feasibility splits). Plain '
'matview (pg_cron unavailable; CAgg-on-view invalid in TS 2.25.2). Refreshed by verdify-ingestor: '
'REFRESH MATERIALIZED VIEW CONCURRENTLY mv_zone_band_grade. Replaces v_setpoint_compliance.';

-- =====================================================================
-- 146.7b  fn_compliance_v2 + fn_house_compliance rollups  [G3]
-- =====================================================================
-- fn_compliance_v2 KEEPS the legacy 5-column prefix (zone, temp_pct, rh_pct,
-- vpd_pct, overall_pct) so the fn_compliance_pct shim and positional SELECT *
-- callers (gather-plan-context.sh:221) do not column-shift. rh_pct stays NULL
-- (as today). Then the graded/attributable columns follow.
CREATE OR REPLACE FUNCTION public.fn_compliance_v2(lookback interval)
RETURNS TABLE(
  zone text,
  temp_pct numeric, rh_pct numeric, vpd_pct numeric, overall_pct numeric,  -- legacy 5-col shape
  temp_pct_graded numeric, vpd_pct_graded numeric, overall_graded numeric,
  overall_controller_attributable numeric, unachievable_frac numeric
) LANGUAGE sql STABLE AS $function$
  WITH g AS (
    SELECT * FROM fn_zone_band_grade(now() - lookback, now())
  ),
  agg AS (
    SELECT
      g.zone,
      count(*) AS n,
      -- legacy binary-ish: fraction with full credit (g=1) as the "in band" proxy
      ROUND(100.0 * count(*) FILTER (WHERE g.g_temp >= 1) / NULLIF(count(*),0), 1) AS temp_pct,
      ROUND(100.0 * count(*) FILTER (WHERE g.g_vpd  >= 1) / NULLIF(count(*),0), 1) AS vpd_pct,
      ROUND(100.0 * count(*) FILTER (WHERE g.g_temp >= 1 AND g.g_vpd >= 1) / NULLIF(count(*),0), 1) AS overall_pct,
      -- graded
      ROUND(100.0 * avg(g.g_temp), 1) AS temp_pct_graded,
      ROUND(100.0 * avg(g.g_vpd), 1) AS vpd_pct_graded,
      ROUND(100.0 * avg(g.zone_score), 1) AS overall_graded,
      -- controller-attributable: unachievable misses scored as full credit (1.0)
      ROUND(100.0 * avg(CASE WHEN g.feasibility = 'unachievable' THEN 1.0
                             ELSE g.zone_score END), 1) AS overall_ctrl,
      ROUND(count(*) FILTER (WHERE g.feasibility = 'unachievable')::numeric
            / NULLIF(count(*),0), 4) AS unachievable_frac
      FROM g
     GROUP BY g.zone
  )
  SELECT
    agg.zone,
    agg.temp_pct, NULL::numeric AS rh_pct, agg.vpd_pct, agg.overall_pct,
    agg.temp_pct_graded, agg.vpd_pct_graded, agg.overall_graded,
    agg.overall_ctrl, agg.unachievable_frac
  FROM agg
  ORDER BY agg.zone;
$function$;

COMMENT ON FUNCTION public.fn_compliance_v2(interval) IS
'Per-zone compliance rollup over fn_zone_band_grade. Cols 1-5 preserve the legacy fn_compliance_pct shape '
'(zone, temp_pct, rh_pct=NULL, vpd_pct, overall_pct) for the shim; then graded temp/vpd/overall, '
'controller-attributable, and unachievable_frac (band-compliance design §6.4).';

-- House roll-up: priority-weighted aggregate of per-zone raw + controller-attributable.
CREATE OR REPLACE FUNCTION public.fn_house_compliance(lookback interval)
RETURNS TABLE(
  house_raw_graded_pct numeric,
  house_controller_attributable_pct numeric,
  house_unachievable_frac numeric
) LANGUAGE sql STABLE AS $function$
  WITH v AS (SELECT * FROM fn_compliance_v2(lookback)),
       w AS (SELECT zone, weight FROM compliance_zone_weights WHERE greenhouse_id='vallery')
  SELECT
    ROUND((SUM(w.weight::numeric * v.overall_graded) / NULLIF(SUM(w.weight::numeric) FILTER (WHERE v.overall_graded IS NOT NULL),0)), 1),
    ROUND((SUM(w.weight::numeric * v.overall_controller_attributable)
          / NULLIF(SUM(w.weight::numeric) FILTER (WHERE v.overall_controller_attributable IS NOT NULL),0)), 1),
    ROUND((SUM(w.weight::numeric * v.unachievable_frac)
          / NULLIF(SUM(w.weight::numeric) FILTER (WHERE v.unachievable_frac IS NOT NULL),0)), 4)
  FROM v JOIN w ON w.zone = v.zone
  WHERE w.weight > 0;  -- empty zones (weight 0) excluded from the house number
$function$;

COMMENT ON FUNCTION public.fn_house_compliance(interval) IS
'Priority-weighted house compliance (band-compliance design §6.3): center 0.60 / east 0.40 / others 0. '
'Emits raw graded + controller-attributable + unachievable_frac. Plugs drop-in into the planner_score form.';

-- =====================================================================
-- 146.8  daily_summary *_v2 columns + daily_zone_compliance  [G4]
-- =====================================================================
-- Existing binary columns are NOT mutated (dual-write co-existence).
ALTER TABLE daily_summary
  ADD COLUMN IF NOT EXISTS compliance_v2_raw_pct double precision,
  ADD COLUMN IF NOT EXISTS compliance_v2_attributable_pct double precision,
  ADD COLUMN IF NOT EXISTS compliance_v2_unachievable_frac double precision,
  ADD COLUMN IF NOT EXISTS graded_temp_compliance_pct double precision,
  ADD COLUMN IF NOT EXISTS graded_vpd_compliance_pct double precision,
  ADD COLUMN IF NOT EXISTS graded_stress_hours_heat double precision,
  ADD COLUMN IF NOT EXISTS graded_stress_hours_cold double precision,
  ADD COLUMN IF NOT EXISTS graded_stress_hours_vpd_high double precision,
  ADD COLUMN IF NOT EXISTS graded_stress_hours_vpd_low double precision,
  ADD COLUMN IF NOT EXISTS feasibility_unknown_min double precision;

COMMENT ON COLUMN daily_summary.compliance_v2_attributable_pct IS
'Priority-weighted house controller-attributable graded compliance (unachievable misses credited). '
'Dual-written by tasks.py alongside the untouched binary compliance_pct; becomes the reward in migration 147.';

CREATE TABLE IF NOT EXISTS daily_zone_compliance (
  date date NOT NULL,
  zone text NOT NULL,
  crop_catalog_id int,
  raw_compliance_pct double precision,
  ctrl_compliance_pct double precision,
  graded_temp_compliance_pct double precision,
  graded_vpd_compliance_pct double precision,
  graded_stress_hours_heat double precision,
  graded_stress_hours_cold double precision,
  graded_stress_hours_vpd_high double precision,
  graded_stress_hours_vpd_low double precision,
  unachievable_min double precision,
  controller_miss_min double precision,
  proxy_flag boolean DEFAULT false,
  captured_at timestamptz DEFAULT now(),
  PRIMARY KEY (date, zone)
);

COMMENT ON TABLE daily_zone_compliance IS
'Per (date,zone) graded compliance + feasibility split, written by the tasks.py dual-write loop. '
'proxy_flag=true for center (vpd_avg proxy until HW-1). (band-compliance design §6.7)';

-- =====================================================================
-- 146.9  fn_compliance_pct SHIM + re-point v_stress_hours_today to graded deficit  [G3/G6]
-- =====================================================================
-- Replace the slow v_setpoint_compliance-backed fn_compliance_pct with a thin shim
-- over fn_compliance_v2 that returns ONLY the legacy 5 columns in the SAME order
-- (zone, temp_pct, rh_pct, vpd_pct, overall_pct). SELECT * callers
-- (gather-plan-context.sh:221) keep working with no column shift; rh_pct stays NULL.
CREATE OR REPLACE FUNCTION public.fn_compliance_pct(lookback interval)
RETURNS TABLE(zone text, temp_pct numeric, rh_pct numeric, vpd_pct numeric, overall_pct numeric)
LANGUAGE sql STABLE AS $function$
  SELECT v.zone, v.temp_pct, v.rh_pct, v.vpd_pct, v.overall_pct
    FROM fn_compliance_v2(lookback) v;
$function$;

COMMENT ON FUNCTION public.fn_compliance_pct(interval) IS
'SHIM (migration 146): legacy 5-col shape (zone, temp_pct, rh_pct=NULL, vpd_pct, overall_pct) sourced from '
'fn_compliance_v2 over fn_zone_band_grade. Replaces the >120s v_setpoint_compliance path (closes M11). '
'Positional/SELECT* callers unchanged.';

-- Re-point v_stress_hours_today to graded-deficit hours (subsumes the 3rd binary
-- stress path). Keep the same column names so alert-monitor.py:248 / tasks.py:1271
-- and Grafana keep reading; the numbers are now graded-deficit (<= binary).
-- graded_stress = sum (1 - g) * sample_hours over the deficit side, for the CENTER
-- zone (the priority/served zone) so the alert tracks the controlled line.
CREATE OR REPLACE VIEW v_stress_hours_today AS
WITH bounds AS (
    SELECT (date_trunc('day', now() AT TIME ZONE 'America/Denver') AT TIME ZONE 'America/Denver') AS start_ts,
           ((date_trunc('day', now() AT TIME ZONE 'America/Denver') + interval '1 day') AT TIME ZONE 'America/Denver') AS end_ts
),
g AS (
    SELECT gg.*, lead(gg.ts) OVER (ORDER BY gg.ts) AS next_ts
      FROM (SELECT * FROM fn_zone_band_grade((SELECT start_ts FROM bounds), LEAST(now(), (SELECT end_ts FROM bounds)))
             WHERE zone = 'center') gg
),
w AS (
    SELECT g.*,
           LEAST(GREATEST(EXTRACT(epoch FROM COALESCE(g.next_ts, LEAST(now(), g.ts + interval '2 minutes')) - g.ts)/3600.0, 0), 5.0/60.0) AS sample_hours,
           (SELECT b.temp_low FROM fn_zone_band('center', g.ts) b) AS t_lo,
           (SELECT b.temp_high FROM fn_zone_band('center', g.ts) b) AS t_hi,
           (SELECT b.vpd_low FROM fn_zone_band('center', g.ts) b) AS v_lo,
           (SELECT b.vpd_high FROM fn_zone_band('center', g.ts) b) AS v_hi
      FROM g
)
SELECT
    (SELECT start_ts FROM bounds) AS date,
    ROUND(SUM(CASE WHEN reading_temp < t_lo THEN (1 - COALESCE(g_temp,0)) * sample_hours ELSE 0 END)::numeric, 2) AS cold_stress_hours,
    ROUND(SUM(CASE WHEN reading_temp > t_hi THEN (1 - COALESCE(g_temp,0)) * sample_hours ELSE 0 END)::numeric, 2) AS heat_stress_hours,
    ROUND(SUM(CASE WHEN reading_vpd  > v_hi THEN (1 - COALESCE(g_vpd,0))  * sample_hours ELSE 0 END)::numeric, 2) AS vpd_stress_hours,
    ROUND(SUM(CASE WHEN reading_vpd  < v_lo THEN (1 - COALESCE(g_vpd,0))  * sample_hours ELSE 0 END)::numeric, 2) AS vpd_low_hours
FROM w;

COMMENT ON VIEW v_stress_hours_today IS
'Graded-deficit stress-hours for the CENTER (priority) zone today: sum (1-g)*sample_hours over each '
'deficit side. Re-points the 3rd binary stress path to the graded model (band-compliance design §6.5). '
'Column names preserved for alert-monitor.py / tasks.py / Grafana. Values are severity-weighted minutes '
'out of band (<= the old binary count).';

-- =====================================================================
-- 146.10  Deprecation drops (additive-safe): dead views + deprecated step funcs
-- =====================================================================
-- v_setpoint_compliance: replaced by fn_zone_band_grade (M11). fn_compliance_pct
-- no longer reads it (shim re-pointed above), so it is safe to drop.
-- v_setpoint_compliance: replaced by fn_zone_band_grade (M11). Nothing depends on
-- it (verified pg_depend) and it is NOT in tests/test_02_database.py REQUIRED_VIEWS,
-- so it is safe to drop here.
DROP VIEW IF EXISTS v_setpoint_compliance;

-- v_plan_compliance / v_plan_accuracy family: verified dead (0 rows each) and
-- slated for drop (design §4.4). BUT they are still listed in
-- tests/test_02_database.py REQUIRED_VIEWS (lines 37-38), and tests/ is owned by
-- coordinator/web (G6/P1a), not this migration's file-group. Dropping them here
-- would break the live required-views assertion before that test is updated.
-- THEREFORE: deferred to the P1a rebuild cycle, where these views are rebuilt
-- against the new curve in the SAME coordinator PR that updates REQUIRED_VIEWS.
-- They are dead and harmless in the meantime.
--   DEFERRED (do not drop until tests/test_02_database.py REQUIRED_VIEWS is updated):
--   DROP VIEW IF EXISTS v_plan_accuracy_by_day;
--   DROP VIEW IF EXISTS v_plan_accuracy_72h;
--   DROP VIEW IF EXISTS v_plan_accuracy;
--   DROP VIEW IF EXISTS v_plan_compliance;

-- v_target_curve was repointed off fn_target_band_smooth in migration 145, so the
-- deprecated step functions can now be dropped without a CASCADE surprise. Verify
-- no remaining dependency before dropping (belt-and-suspenders): if anything still
-- depends, leave the function in place (it stays COMMENT-deprecated from 145).
DO $dep$
DECLARE
    n_smooth int;
    n_step int;
BEGIN
    SELECT count(*) INTO n_smooth
      FROM pg_depend d
      JOIN pg_proc p ON p.oid = d.refobjid
     WHERE p.proname = 'fn_target_band_smooth'
       AND d.deptype = 'n'
       AND d.classid = 'pg_rewrite'::regclass;
    IF n_smooth = 0 THEN
        DROP FUNCTION IF EXISTS public.fn_target_band_smooth(timestamptz);
    ELSE
        RAISE NOTICE 'fn_target_band_smooth still has % view dependencies; left in place (COMMENT-deprecated).', n_smooth;
    END IF;

    SELECT count(*) INTO n_step
      FROM pg_depend d
      JOIN pg_proc p ON p.oid = d.refobjid
     WHERE p.proname = 'fn_target_band'
       AND d.deptype = 'n'
       AND d.classid = 'pg_rewrite'::regclass;
    IF n_step = 0 THEN
        DROP FUNCTION IF EXISTS public.fn_target_band(timestamptz);
    ELSE
        RAISE NOTICE 'fn_target_band still has % view dependencies; left in place (COMMENT-deprecated).', n_step;
    END IF;
END
$dep$;
