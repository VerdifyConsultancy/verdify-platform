-- 181-serve-band-target-one-source.sql
-- ADR0003 §6.3 (band-compliance build, BC-9): serve the band TARGET from the ONE
-- device curve, so the served target == the device on-chip target == the graph.
--
-- Today fn_band_setpoints (mig 171) returns only the 4 EDGES; "target" is re-derived
-- in three places as the WRONG midpoint (low+high)/2: v_greenhouse_state (mig 141)
-- and the ingestor climate_action_log writer. Post-mig-174/175 the house band is
-- ASYMMETRIC (temp_target sm=84 vs midpoint(76,86)=81 — a 3°F error at solar noon),
-- so the midpoint is provably not the served target.
--
-- Fix: fn_band_setpoints gains temp_target/vpd_target = fn_crop_band_value('house',
-- 'temp_target'/'vpd_target', ts) — byte-for-byte the device's on-chip sv2_*_tgt
-- harmonic (controls.yaml:422/425). The (low+high)/2 derivations on the served
-- surface are retired (this fn + the ingestor writer change in the same PR).
-- The lower-quartile heat target (mig 136) is a CONTROL THRESHOLD in fn_band_timeline,
-- NOT a displayed band center — left untouched here.
--
-- ADDITIVE: the existing 4 edge columns keep position+name, so every SELECT * /
-- name-indexed consumer (dispatcher.py band_row, api/main.py band_row,
-- fn_house_vpd_control_band, fn_band_timeline, fn_band_trace, band dashboards) is
-- unaffected (ZoneBandRow uses extra='ignore').
--
-- DEVICE-MATCH CAVEAT (documented, NOT modelled): the device adds night_vpd_bias_kpa
-- (sin² bump, controls.yaml:435-442) to vpd_target overnight. Registry default is 0.0,
-- so served==device under default ops. When the planner raises it, served vpd_target
-- lags the device by the bias bump — the mig-182 divergence view tolerates that band.
-- The bias is a transient planner knob, not a crop anchor, so it is NOT folded here.
--
-- Non-self-transactional (plain CREATE OR REPLACE FUNCTION; no top-level COMMIT, no
-- CONCURRENTLY) -> SAFE to rollback-validate under BEGIN; ... ROLLBACK;.
-- Functional rollback: CREATE OR REPLACE back to the mig-171 4-column body.
-- RESTARTS: touches no verdify_schemas/**, ingestor/entity_map.py, or mcp/server.py
-- from THIS file. (The companion ingestor.py writer change requires a verdify-ingestor
-- restart — see that file / the PR body.)

CREATE OR REPLACE FUNCTION public.fn_band_setpoints(target_ts timestamp with time zone)
 RETURNS TABLE(temp_low double precision, temp_high double precision,
               vpd_low double precision, vpd_high double precision,
               temp_target double precision, vpd_target double precision)
 LANGUAGE sql
 STABLE
 ROWS 1
AS $function$
  SELECT fn_crop_band_value('house', 'temp_low',    target_ts) AS temp_low,
         fn_crop_band_value('house', 'temp_high',   target_ts) AS temp_high,
         fn_crop_band_value('house', 'vpd_low',     target_ts) AS vpd_low,
         fn_crop_band_value('house', 'vpd_high',    target_ts) AS vpd_high,
         fn_crop_band_value('house', 'temp_target', target_ts) AS temp_target,
         fn_crop_band_value('house', 'vpd_target',  target_ts) AS vpd_target;
$function$;

COMMENT ON FUNCTION public.fn_band_setpoints(timestamp with time zone) IS
  'Served house band = the device''s harmonic curve (fn_crop_band_value house). '
  'mig 181 (BC-9/ADR0003 §6.3) adds temp_target/vpd_target so the served TARGET is '
  'the device on-chip sv2_*_tgt curve; the (low+high)/2 midpoint derivations on the '
  'served surface are retired. night_vpd_bias_kpa device bump is NOT modelled here '
  '(see mig 182 divergence tolerance).';
