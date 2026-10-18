-- P3 metadata only. ROOT executes once with psql -X -qAt -v ON_ERROR_STOP=1 -f -.
-- Each statement has a 2s timeout; no data-function invocation or mutation.
BEGIN READ ONLY;
SET LOCAL statement_timeout = '2s';
SET LOCAL lock_timeout = '500ms';
SET LOCAL idle_in_transaction_session_timeout = '10s';
SET LOCAL max_parallel_workers_per_gather = 0;
SET LOCAL work_mem = '4MB';
SET LOCAL TimeZone = 'UTC';
SELECT json_build_object('kind','context','captured_at',clock_timestamp(),
 'database',current_database(),'current_user',current_user,'session_user',session_user,
 'server_version_num',current_setting('server_version_num'),
 'read_only',current_setting('transaction_read_only'),
 'postmaster_started_at',pg_postmaster_start_time(),
 'server_addr',inet_server_addr(),'server_port',inet_server_port(),
 'denver_date',(now() AT TIME ZONE 'America/Denver')::date,
 'latest_complete_adjacent_day_night',(now() AT TIME ZONE 'America/Denver')::date - 2);
WITH wanted(name) AS (VALUES
    ('fn_absolute_humidity_g_m3'),
    ('fn_band_setpoints'),
    ('fn_center_band_setpoints'),
    ('fn_crop_band_value'),
    ('fn_current_season'),
    ('fn_diurnal_interp'),
    ('fn_hermite_phase'),
    ('fn_house_vpd_control_band'),
    ('fn_realized_solar_night_dryout'),
    ('fn_solar_phase'),
    ('fn_solar_sunrise_hour'),
    ('fn_solar_sunset_hour'),
    ('fn_zone_vpd_targets')
)
SELECT json_build_object('kind','function','requested_name',w.name,
 'oid',p.oid,'schema',n.nspname,'name',p.proname,
 'identity_arguments',pg_get_function_identity_arguments(p.oid),
 'argument_types',oidvectortypes(p.proargtypes),'argument_names',p.proargnames,
 'result',pg_get_function_result(p.oid),'language',l.lanname,
 'volatility',p.provolatile,'security_definer',p.prosecdef,
 'owner',pg_get_userbyid(p.proowner),'config',p.proconfig,
 'prosrc_sha256',encode(sha256(convert_to(p.prosrc,'UTF8')),'hex'),
 'definition_sha256',encode(sha256(convert_to(pg_get_functiondef(p.oid),'UTF8')),'hex'))
FROM wanted w
LEFT JOIN pg_namespace n ON n.nspname='public'
LEFT JOIN pg_proc p ON p.pronamespace=n.oid AND p.proname=w.name AND p.prokind='f'
LEFT JOIN pg_language l ON l.oid=p.prolang
ORDER BY w.name,p.oid;
WITH wanted(filename) AS (VALUES
    ('db/migrations/145-vanda-band-and-join-fix.sql'),
    ('db/migrations/164-zone-vpd-targets-solar-repoint.sql'),
    ('db/migrations/165-band-solar-noon-repoint.sql'),
    ('db/migrations/170-band-harmonic-smooth-curve.sql'),
    ('db/migrations/172-smooth-solar-phase.sql'),
    ('db/migrations/181-serve-band-target-one-source.sql'),
    ('db/migrations/186-noaa-solar-phase-parity.sql'),
    ('db/migrations/191-realized-solar-night-dryout-episodes.sql')
)
SELECT json_build_object('kind','migration','requested_filename',w.filename,
 'filename',m.filename,'source',m.source,'sha256',m.sha256,
 'applied_at',m.applied_at,'stamp_method',m.stamp_method)
FROM wanted w LEFT JOIN public.schema_migrations m
 ON m.source='db/migrations' AND m.filename=w.filename
ORDER BY w.filename;
ROLLBACK;
