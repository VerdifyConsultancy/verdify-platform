\set ON_ERROR_STOP on
BEGIN READ ONLY;
SET LOCAL statement_timeout='5s';
SET LOCAL lock_timeout='1s';
SELECT 'diagnostics' AS kind, row_to_json(d) AS evidence FROM (SELECT ts,firmware_version FROM public.diagnostics WHERE greenhouse_id='vallery' AND ts>now()-interval '10 minutes' ORDER BY ts DESC LIMIT 1)d;
SELECT 'climate' AS kind,row_to_json(c) AS evidence FROM (SELECT ts,temp_north,temp_south,temp_east,temp_west FROM public.climate WHERE greenhouse_id='vallery' AND ts>now()-interval '5 minutes' ORDER BY ts DESC LIMIT 1)c;
SELECT 'lighting_cfg' AS kind,row_to_json(s) AS evidence FROM (SELECT DISTINCT ON(parameter) ts,parameter,value FROM public.setpoint_snapshot WHERE greenhouse_id='vallery' AND ts>now()-interval '10 minutes' AND zone IS NULL AND band_role IS NULL AND parameter IN ('gl_main_sunset_hour','gl_grow_sunset_hour','gl_main_min_on_s','gl_grow_min_on_s','sw_gl_main_auto_mode','sw_gl_grow_auto_mode') ORDER BY parameter,ts DESC)s;
SELECT 'lighting_text' AS kind,row_to_json(s) AS evidence FROM (SELECT DISTINCT ON(entity) ts,entity,value FROM public.system_state WHERE greenhouse_id='vallery' AND ts>now()-interval '10 minutes' AND entity IN ('gl_main_reason','gl_grow_reason','gl_main_state','gl_grow_state') ORDER BY entity,ts DESC)s;
ROLLBACK;
