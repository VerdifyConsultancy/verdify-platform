-- One preselected complete adjacent-day window. ROOT executes once, no retry/expansion.
-- Calendar completeness is not a claim of telemetry coverage or efficacy.
BEGIN READ ONLY;
SET LOCAL statement_timeout='2s';
SET LOCAL lock_timeout='500ms';
SET LOCAL idle_in_transaction_session_timeout='10s';
SET LOCAL max_parallel_workers_per_gather=0;
SET LOCAL work_mem='4MB';
SET LOCAL TimeZone='UTC';
WITH expected_functions(name,sha) AS (VALUES
('fn_absolute_humidity_g_m3','c14ce18c1811fec25416262983126072deca690991c3b60df73b69d08fb13c92'),
('fn_band_setpoints','58a0e95e646a5de45c022438546608e1677adad000800c6784e0f6316241b281'),
('fn_center_band_setpoints','718de2d0507b4dc0adc86b8b8fdc6e93be0008a3b0041c320b8ac97eff45aaea'),
('fn_crop_band_value','e5682cb83e81c57ee44254c555d775e4f7dedf1078af3ba98ccc97f71c03d9e0'),
('fn_current_season','c35ff6fc0aa046b208da7ff7a9f03a9fcba658d0ed652c01b08f0b826e544130'),
('fn_diurnal_interp','002befc3228b8484c5bd130039ea375d987044ee53a83d3b2d6626cce37f1978'),
('fn_hermite_phase','f9642002e69bbac2d1a2dc7f9a19fb069ea86295c995713eaf37c8efa2e164fb'),
('fn_house_vpd_control_band','7aad695981e7883e562a93a1a2dceaf55a1a5bb17ccf4007748a9c851069279e'),
('fn_realized_solar_night_dryout','b9626fd5882392bcf00c080550b6ab5000cc5ed8a63ea8dceb806e76aae6d3a6'),
('fn_solar_phase','960f6e4c435ce534db8956630bd32408a3d1294362b2aef44c018189d250e715'),
('fn_solar_sunrise_hour','66795353790f6678c02f8f6af5e6d576fe8177714b07a679925360bd1f851d51'),
('fn_solar_sunset_hour','227310e3542f7e0bc06f0639724a72444a8366320cb30da07ecfc01f185a9c79'),
('fn_zone_vpd_targets','71109e38a269c68d9d1112ad4e6cf60a5efaba15b49045b1ef119a6ba658e1ff')
), expected_ledger(filename,sha) AS (VALUES
('db/migrations/145-vanda-band-and-join-fix.sql','c599744bc497a0c6bc5c39644a17eeeec7b658fc4b1081afcf65aa94e6818f35'),
('db/migrations/164-zone-vpd-targets-solar-repoint.sql','078c7e1afe025c3fa14d8ec39d422707a1f22867844959bce526907cacb0b262'),
('db/migrations/165-band-solar-noon-repoint.sql','2cdf3d6b657e2e8d1c95ebb73cc724bc9f8465a95b0e4c5e74a6deb0c9d46bed'),
('db/migrations/170-band-harmonic-smooth-curve.sql','f3e93f7df52ad899ea192ed90849a36ec79b7bc7f86a487a28d5af53024ef49b'),
('db/migrations/172-smooth-solar-phase.sql','bf0c60dc6f3553be395c32a22bdd5d1c00f3addfd689aa1def3f796db6db431e'),
('db/migrations/181-serve-band-target-one-source.sql','7992b80335450c5851545d9695811f54c20df780ec18c5ee95f2a15b888fe67a'),
('db/migrations/186-noaa-solar-phase-parity.sql','9e1e33b5005e183a35d84dd9b41d96b4d535faa99945d7ae6c89b196d4569e28'),
('db/migrations/191-realized-solar-night-dryout-episodes.sql','9aea5b0f7878df3a585884743a4d5eaf8b989c39f18cd09698e9b4973394c211')
), verdict AS (
 SELECT current_database()='verdify'
  AND current_user='verdify' AND session_user='verdify'
  AND current_setting('transaction_read_only')='on'
  AND pg_postmaster_start_time()='2026-09-29T00:40:56.280382+00:00'::timestamptz
  AND DATE '2026-10-16' <= (now() AT TIME ZONE 'America/Denver')::date - 2
  AND (SELECT count(*)=13 AND bool_and(
       encode(sha256(convert_to(pg_get_functiondef(p.oid),'UTF8')),'hex')=e.sha
       AND pg_get_userbyid(p.proowner)='verdify' AND p.proconfig IS NULL)
    FROM expected_functions e JOIN pg_namespace n ON n.nspname='public'
    JOIN pg_proc p ON p.pronamespace=n.oid AND p.proname=e.name AND p.prokind='f')
  AND (SELECT count(*)=8 AND bool_and(m.sha256=e.sha)
    FROM expected_ledger e JOIN public.schema_migrations m
      ON m.source='db/migrations' AND m.filename=e.filename) AS ok
)
SELECT json_build_object('kind','pilot_identity_guard','passed',1/(CASE WHEN ok THEN 1 ELSE 0 END),
 'identity_receipt_sha256','970852793d4ab0648c6b5cf8b52a5139df9f83bb7c3fa20594253fef83296388','night_date','2026-10-16',
 'database',current_database(),'postmaster_started_at',pg_postmaster_start_time(),
 'captured_at',clock_timestamp(), 'read_only',current_setting('transaction_read_only')) FROM verdict;
-- The existing getter executes once. 501 is an overflow sentinel; emit at most 500 rows.
-- A 2s cancellation ends this attempt; preserve stderr and do not widen/retry.
WITH bounded AS MATERIALIZED (
 SELECT * FROM public.fn_realized_solar_night_dryout(DATE '2026-10-16',DATE '2026-10-16','vallery')
 ORDER BY episode_started_at,episode_id LIMIT 501
), returned AS (SELECT * FROM bounded ORDER BY episode_started_at,episode_id LIMIT 500)
SELECT json_build_object(
 'kind','episodes','night_date','2026-10-16','greenhouse_id','vallery',
 'identity_receipt_sha256','970852793d4ab0648c6b5cf8b52a5139df9f83bb7c3fa20594253fef83296388',
 'window_start',DATE '2026-10-16'::timestamp AT TIME ZONE 'America/Denver',
 'window_end',(DATE '2026-10-16'+2)::timestamp AT TIME ZONE 'America/Denver',
 'row_cap',500,'row_cap_exceeded',(SELECT count(*)>500 FROM bounded),
 'episode_rows_returned',(SELECT count(*) FROM returned),
 'episodes',COALESCE((SELECT json_agg(r ORDER BY episode_started_at,episode_id) FROM returned r),'[]'::json),
 'snapshot_at',now(),'completed_at',clock_timestamp());
ROLLBACK;
