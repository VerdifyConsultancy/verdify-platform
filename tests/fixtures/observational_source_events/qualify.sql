-- Disposable-clone regression only. Never execute on production.
BEGIN;
SET LOCAL timezone='UTC';
DO $acl$
BEGIN
 IF has_table_privilege('verdify_ingestor_runtime_login','public.observational_source_events','SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
 OR has_table_privilege('verdify_api_runtime_login','public.observational_source_events','SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
 OR has_function_privilege('verdify_api_runtime_login','public.fn_record_observational_source_event(uuid,text,timestamptz,text,uuid,bigint,jsonb)','EXECUTE')
 OR NOT has_function_privilege('verdify_ingestor_runtime_login','public.fn_record_observational_source_event(uuid,text,timestamptz,text,uuid,bigint,jsonb)','EXECUTE') THEN
 RAISE EXCEPTION 'observational duty expansion'; END IF;
END $acl$;
DO $fixture$
DECLARE x record; first_id uuid; n integer; rejected boolean; payload jsonb;
 runtime uuid:='38200000-0000-0000-0000-000000000001';
 observed timestamptz:='2000-01-01T00:00:00Z';
BEGIN
 FOR x IN SELECT * FROM (VALUES
 ('system_state','{"entity":"greenhouse_state","value":"HEAT"}'::jsonb),
 ('override','{"override_type":"fog_gate_rh","mode":"HEAT"}'::jsonb),
 ('setpoint_observed','{"parameter":"vent_prefer_temp_delta_f","value":5}'::jsonb),
 ('esp32_log','{"level":"WARN","tag":"original","message":"preserved"}'::jsonb),
 ('diagnostics','{"uptime_s":30,"active_probe_count":4.0,"sntp_valid":1.0}'::jsonb)
 ) cases(kind,payload) LOOP
 first_id:=gen_random_uuid();
 IF NOT public.fn_record_observational_source_event(first_id,x.kind,observed,'vallery',runtime,7,x.payload)
 OR public.fn_record_observational_source_event(first_id,x.kind,observed,'vallery',runtime,7,x.payload) THEN
 RAISE EXCEPTION 'insert/exact unknown commit retry failed'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.observational_source_events e WHERE e.event_id=first_id
 AND e.event_payload=x.payload AND e.source_ts=observed AND e.source_runtime_instance_id=runtime AND e.source_connection_generation=7
 AND e.event_sha256=sha256(convert_to(jsonb_build_object('event_id',first_id,'kind',x.kind,'source_ts',observed,'greenhouse_id','vallery','runtime',runtime,'generation',7,'payload',x.payload)::text,'UTF8'))) THEN
 RAISE EXCEPTION 'immutable observation evidence changed'; END IF;
 rejected:=false;
 BEGIN
 PERFORM public.fn_record_observational_source_event(first_id,x.kind,observed,'vallery',runtime,99,x.payload);
 EXCEPTION WHEN OTHERS THEN rejected:=true; END;
 IF NOT rejected THEN RAISE EXCEPTION 'UUID collision accepted changed generation'; END IF;
 rejected:=false;
 BEGIN
 PERFORM public.fn_record_observational_source_event(first_id,x.kind,observed,'vallery',runtime,7,x.payload||jsonb_build_object('unexpected','changed'));
 EXCEPTION WHEN OTHERS THEN rejected:=true; END;
 IF NOT rejected THEN RAISE EXCEPTION 'UUID collision accepted changed payload'; END IF;
 END LOOP;
 IF NOT EXISTS(SELECT 1 FROM public.system_state WHERE ts=observed AND entity='greenhouse_state' AND value='HEAT' AND greenhouse_id='vallery')
 OR NOT EXISTS(SELECT 1 FROM public.override_events WHERE ts=observed AND override_type='fog_gate_rh' AND mode='HEAT')
 OR NOT EXISTS(SELECT 1 FROM public.esp32_logs WHERE ts=observed AND level='WARN' AND tag='original' AND message='preserved') THEN
 RAISE EXCEPTION 'original typed state/override/log row missing'; END IF;
 IF (SELECT count(*) FROM public.setpoint_changes WHERE ts=observed AND source='esp32' AND parameter='vent_prefer_temp_delta_f' AND value=5 AND confirmed_at=observed AND delivery_status='observed')<>1 THEN
 RAISE EXCEPTION 'replay invented fresh setpoint confirmation'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.diagnostics WHERE ts=observed AND active_probe_count=4 AND sntp_valid=1 AND uptime_s=30) THEN
 RAISE EXCEPTION 'integral diagnostics typed insertion incorrect'; END IF;
 -- Distinct observations at the same timestamp must remain distinct.
 IF NOT public.fn_record_observational_source_event(gen_random_uuid(),'system_state',observed,'vallery',runtime,1,'{"entity":"greenhouse_state","value":"HEAT"}') THEN
 RAISE EXCEPTION 'distinct UUID insert failed'; END IF;
 IF (SELECT count(*) FROM public.system_state WHERE ts=observed AND entity='greenhouse_state' AND value='HEAT')<>2 THEN
 RAISE EXCEPTION 'timestamp dedup lost distinct UUID'; END IF;
 FOR x IN SELECT * FROM (VALUES
 ('control','{"entity":"x","value":"x"}'::jsonb),
 ('setpoint_observed','{"parameter":"vent_prefer_temp_delta_f","value":5,"confirmed_at":"2099-01-01"}'::jsonb),
 ('setpoint_observed','{"parameter":"vent_prefer_temp_delta_f","value":true}'::jsonb),
 ('setpoint_observed','{"parameter":"vent_prefer_temp_delta_f","value":"72"}'::jsonb),
 ('diagnostics','{"active_probe_count":1.5}'::jsonb),
 ('diagnostics','{"active_probe_count":2147483648}'::jsonb),
 ('diagnostics','{"sntp_valid":true}'::jsonb),
 ('system_state','{"entity":"x","value":"x","ts":"2099-01-01"}'::jsonb),
 ('esp32_log','{"level":"WARN","message":""}'::jsonb)
 ) cases(kind,payload) LOOP
 SELECT count(*) INTO n FROM public.observational_source_events;
 rejected:=false;
 BEGIN
 PERFORM public.fn_record_observational_source_event(gen_random_uuid(),x.kind,observed,'vallery',runtime,7,x.payload);
 EXCEPTION WHEN OTHERS THEN rejected:=true; END;
 IF NOT rejected OR (SELECT count(*) FROM public.observational_source_events)<>n THEN
 RAISE EXCEPTION 'unsafe typed/kind payload accepted or ledger partially committed: %',x.payload; END IF;
 END LOOP;
END $fixture$;
SET LOCAL ROLE verdify_api_runtime_login;
DO $duty$
DECLARE denied boolean:=false;
BEGIN
 BEGIN
 PERFORM public.fn_record_observational_source_event(gen_random_uuid(),'system_state','2000-01-01','vallery','38200000-0000-0000-0000-000000000001',7,'{"entity":"x","value":"x"}');
 EXCEPTION WHEN insufficient_privilege THEN denied:=true; END;
 IF NOT denied THEN RAISE EXCEPTION 'API executed observational writer'; END IF;
END $duty$;
RESET ROLE;
SET LOCAL ROLE verdify_ingestor_runtime_login;
DO $duty$
DECLARE id uuid:=gen_random_uuid(); denied boolean:=false;
BEGIN
 IF NOT public.fn_record_observational_source_event(id,'system_state','2000-01-02','vallery','38200000-0000-0000-0000-000000000001',7,'{"entity":"greenhouse_state","value":"HEAT"}') THEN
 RAISE EXCEPTION 'ordinary ingestor could not execute pinned writer'; END IF;
 IF public.fn_record_observational_source_event(id,'system_state','2000-01-02','vallery','38200000-0000-0000-0000-000000000001',7,'{"entity":"greenhouse_state","value":"HEAT"}') THEN
 RAISE EXCEPTION 'ordinary ingestor retry duplicated'; END IF;
 BEGIN
 PERFORM 1 FROM public.observational_source_events;
 EXCEPTION WHEN insufficient_privilege THEN denied:=true; END;
 IF NOT denied THEN RAISE EXCEPTION 'ordinary ingestor reads private ledger'; END IF;
END $duty$;
RESET ROLE;
ROLLBACK;
