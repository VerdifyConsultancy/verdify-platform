\set ON_ERROR_STOP on
BEGIN;
DO $guard$ BEGIN
 IF current_database() NOT IN ('verdify_rehearsal','climate_266_clone_b')
 OR inet_server_addr() IS NOT NULL OR current_setting('listen_addresses')<>'' THEN
 RAISE EXCEPTION 'socket-only disposable qualification required'; END IF;
END $guard$;
SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;
DO $exercise$
DECLARE eid uuid:='38226600-0000-4000-8000-000000000001';
 r uuid:='38226600-0000-4000-8000-000000000003';
 ts timestamptz:='2026-10-01 00:26:53.076366+00';
 payload jsonb:='{"lightning_count":0.0,"solar_noon_min":770.0,"solar_sunrise_min":414.0,"solar_sunset_min":1126.0,"temp_avg":76.04000091552734}';
 provenance jsonb:='{"lightning_count":{"connection_generation":1,"received_at":"2026-10-01T00:26:28.376643Z"}}';
 invalid jsonb; denied boolean;
BEGIN
 IF NOT public.fn_record_climate_source_event(eid,ts,'vallery',r,1,payload,provenance) THEN RAISE EXCEPTION 'original integral decimal insert failed'; END IF;
 IF public.fn_record_climate_source_event(eid,ts,'vallery',r,1,payload,provenance) THEN RAISE EXCEPTION 'unknown-commit exact UUID replay inserted duplicate'; END IF;
 IF NOT public.fn_record_climate_source_event('38226600-0000-4000-8000-000000000002',ts,'vallery',r,1,payload,provenance) THEN RAISE EXCEPTION 'distinct UUID same timestamp incorrectly deduplicated'; END IF;
 FOR invalid IN SELECT value FROM jsonb_array_elements('[{"lightning_count":0.5},{"lightning_count":true},{"lightning_count":"0.0"},{"lightning_count":2147483648.0},{"lightning_count":-2147483649.0}]') LOOP
 denied:=false;
 BEGIN PERFORM public.fn_record_climate_source_event(gen_random_uuid(),ts,'vallery',r,1,invalid,'{}'); EXCEPTION WHEN OTHERS THEN denied:=true; END;
 IF NOT denied THEN RAISE EXCEPTION 'fraction, bool, string or out-of-range integer accepted: %',invalid; END IF;
 END LOOP;
 denied:=false;
 BEGIN PERFORM public.fn_record_climate_source_event(eid,ts,'vallery',r,1,'{"lightning_count":1.0}','{}'); EXCEPTION WHEN OTHERS THEN denied:=true; END;
 IF NOT denied THEN RAISE EXCEPTION 'changed original UUID payload accepted'; END IF;
END $exercise$;
RESET SESSION AUTHORIZATION;
DO $verify$
DECLARE h bytea;
BEGIN
 IF (SELECT count(*) FROM public.climate_source_events WHERE event_id IN ('38226600-0000-4000-8000-000000000001','38226600-0000-4000-8000-000000000002'))<>2
 OR (SELECT count(*) FROM public.climate WHERE ts='2026-10-01 00:26:53.076366+00' AND lightning_count=0 AND solar_noon_min=770 AND solar_sunrise_min=414 AND solar_sunset_min=1126 AND temp_avg=76.04000091552734)<>2 THEN RAISE EXCEPTION 'typed climate / exact identity atomic count failed'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.climate_source_events WHERE event_id='38226600-0000-4000-8000-000000000001' AND climate_payload::text LIKE '%770.0%' AND sample_provenance->'lightning_count'->>'connection_generation'='1') THEN RAISE EXCEPTION 'original decimal payload/provenance changed'; END IF;
 IF EXISTS(SELECT 1 FROM public.climate_source_events e WHERE event_id IN ('38226600-0000-4000-8000-000000000001','38226600-0000-4000-8000-000000000002') AND event_sha256<>sha256(convert_to(jsonb_build_object('event_id',e.event_id,'source_ts',e.source_ts,'greenhouse_id',e.greenhouse_id,'runtime',e.source_runtime_instance_id,'generation',e.source_connection_generation,'payload',e.climate_payload,'sample_provenance',e.sample_provenance)::text,'UTF8'))) THEN RAISE EXCEPTION 'original immutable hash changed'; END IF;
 IF (SELECT count(*) FROM public.climate_source_events WHERE source_runtime_instance_id='38226600-0000-4000-8000-000000000003')<>2 THEN RAISE EXCEPTION 'invalid request escaped transaction atomicity'; END IF;
END $verify$;
SELECT 'PASS: original four integral decimals, exact unknown-commit UUID replay, same-timestamp distinct UUID, immutable evidence, fractional/bool/string/overflow rejection and atomicity';
ROLLBACK;
