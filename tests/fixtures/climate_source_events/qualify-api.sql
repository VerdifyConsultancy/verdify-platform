\set ON_ERROR_STOP on
BEGIN;
DO $guard$ BEGIN
 IF current_database() NOT IN ('verdify_rehearsal','climate_265_clone_b')
 OR inet_server_addr() IS NOT NULL OR current_setting('listen_addresses')<>'' THEN
 RAISE EXCEPTION 'socket-only disposable qualification required'; END IF;
END $guard$;
SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;
DO $positive$
DECLARE original_id uuid:='38226500-0000-4000-8000-000000000001';
 second_id uuid:='38226500-0000-4000-8000-000000000002';
 t timestamptz:='2026-09-29 16:01:00+00'; r uuid:='38226500-0000-4000-8000-000000000003'; denied boolean;
BEGIN
 IF NOT public.fn_record_climate_source_event(original_id,t,'vallery',r,7,'{"temp_avg":73.333222}','{"temp_avg":{"received_at":"2026-09-29T16:00:50Z","connection_generation":6}}') THEN RAISE EXCEPTION 'first event not inserted'; END IF;
 IF public.fn_record_climate_source_event(original_id,t,'vallery',r,7,'{"temp_avg":73.333222}','{"temp_avg":{"received_at":"2026-09-29T16:00:50Z","connection_generation":6}}') THEN RAISE EXCEPTION 'retry inserted duplicate'; END IF;
 IF NOT public.fn_record_climate_source_event(second_id,t,'vallery',r,7,'{"temp_avg":73.333222}','{"temp_avg":{"received_at":"2026-09-29T16:00:50Z","connection_generation":6}}') THEN RAISE EXCEPTION 'distinct UUID with same timestamp/value incorrectly deduplicated'; END IF;
 denied:=false;
 BEGIN PERFORM public.fn_record_climate_source_event(original_id,t,'vallery',r,8,'{"temp_avg":73.333222}','{}'); EXCEPTION WHEN OTHERS THEN denied:=true; END;
 IF NOT denied THEN RAISE EXCEPTION 'changed UUID evidence accepted'; END IF;
 denied:=false;
 BEGIN PERFORM public.fn_record_climate_source_event(gen_random_uuid(),t,'vallery',r,7,'{"unapproved_column":1}','{}'); EXCEPTION WHEN OTHERS THEN denied:=true; END;
 IF NOT denied THEN RAISE EXCEPTION 'foreign column accepted'; END IF;
 denied:=false;
 BEGIN PERFORM public.fn_record_climate_source_event(gen_random_uuid(),t,'foreign',r,7,'{"temp_avg":73}','{}'); EXCEPTION WHEN OTHERS THEN denied:=true; END;
 IF NOT denied THEN RAISE EXCEPTION 'foreign greenhouse accepted'; END IF;
 denied:=false;
 BEGIN DELETE FROM public.climate_source_events; EXCEPTION WHEN insufficient_privilege THEN denied:=true; END;
 IF NOT denied THEN RAISE EXCEPTION 'runtime mutated event ledger'; END IF;
END $positive$;
RESET SESSION AUTHORIZATION;
DO $verify$
BEGIN
 IF (SELECT count(*) FROM public.climate_source_events WHERE event_id IN ('38226500-0000-4000-8000-000000000001','38226500-0000-4000-8000-000000000002'))<>2
 OR (SELECT count(*) FROM public.climate WHERE ts='2026-09-29 16:01:00+00' AND temp_avg=73.333222)<>2 THEN
 RAISE EXCEPTION 'event/climate atomic identity count failed'; END IF;
 IF EXISTS(SELECT 1 FROM public.climate_source_events WHERE event_id='38226500-0000-4000-8000-000000000001' AND (source_connection_generation<>7 OR source_ts<>'2026-09-29 16:01:00+00' OR sample_provenance->'temp_avg'->>'connection_generation'<>'6')) THEN
 RAISE EXCEPTION 'original event/source timestamps or generations changed'; END IF;
END $verify$;
SET SESSION AUTHORIZATION verdify_api_runtime_login;
DO $api_denied$
DECLARE denied boolean:=false;
BEGIN
 BEGIN PERFORM public.fn_record_climate_source_event(gen_random_uuid(),now(),'vallery',gen_random_uuid(),1,'{"temp_avg":73}','{}'); EXCEPTION WHEN insufficient_privilege THEN denied:=true; END;
 IF NOT denied THEN RAISE EXCEPTION 'API wrote climate source event'; END IF;
END $api_denied$;
RESET SESSION AUTHORIZATION;
SELECT 'PASS: exact UUID retry, same-timestamp distinct UUID, immutable lineage, invalid payload/greenhouse and API/ledger DML denial';
ROLLBACK;
