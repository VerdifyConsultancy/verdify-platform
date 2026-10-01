-- #382 durable historical observations; serialized C0 runner transaction.
SET LOCAL search_path = pg_catalog, public, pg_temp;
LOCK TABLE public.schema_migrations, public.runtime_ordinary_login_attestation_receipts IN SHARE ROW EXCLUSIVE MODE;
DO $preflight$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq=266 AND filename='db/migrations/266-climate-integral-json-insertion.sql' AND sha256='742a99ba133dee5cbcba5bd2d7f80b5902da51ec0455b992170c89b9f4bf7526' AND stamp_method='runner')
 OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>=267)
 OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
 OR (SELECT encode(boundary_sha256,'hex') FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_api_runtime_login') IS DISTINCT FROM '5bb375800baf2e61d29da1e1327b49c090da4a3e53656b1fbf5cf839bfff5e43'
 OR (SELECT encode(boundary_sha256,'hex') FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_ingestor_runtime_login') IS DISTINCT FROM '17287481525f6e831d9d2a55cedfc27af3c9c320f8f196ae9a0d976feb5ed19a'
 OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name)) THEN
 RAISE EXCEPTION '267 refuses changed predecessor ledger, receipt or owner role'; END IF;
END $preflight$;
CREATE TABLE public.observational_source_events (
 event_id uuid PRIMARY KEY,
 kind text NOT NULL CHECK(kind IN ('system_state','override','setpoint_observed','esp32_log','diagnostics')),
 source_ts timestamptz NOT NULL,
 greenhouse_id text NOT NULL CHECK(greenhouse_id='vallery'),
 source_runtime_instance_id uuid NOT NULL,
 source_connection_generation bigint NOT NULL CHECK(source_connection_generation BETWEEN 0 AND 9007199254740991),
 event_payload jsonb NOT NULL CHECK(jsonb_typeof(event_payload)='object'),
 event_sha256 bytea NOT NULL CHECK(octet_length(event_sha256)=32),
 accepted_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE public.observational_source_events OWNER TO verdify;
REVOKE ALL ON public.observational_source_events FROM PUBLIC;
CREATE FUNCTION public.fn_record_observational_source_event(
 p_event_id uuid,p_kind text,p_source_ts timestamptz,p_greenhouse_id text,
 p_runtime_instance_id uuid,p_connection_generation bigint,p_payload jsonb
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public,pg_temp
SET timezone='UTC'
AS $body$
DECLARE target_table text; target_view text; allowed text[]; required text[];
 h bytea; inserted uuid; k text; v jsonb; t oid; category "char"; n numeric;
 typed_payload jsonb; column_names text; expressions text;
BEGIN
 IF p_event_id IS NULL OR p_source_ts IS NULL OR p_greenhouse_id IS DISTINCT FROM 'vallery'
 OR p_runtime_instance_id IS NULL OR p_connection_generation IS NULL
 OR p_connection_generation NOT BETWEEN 0 AND 9007199254740991
 OR jsonb_typeof(p_payload) IS DISTINCT FROM 'object' OR p_payload='{}'::jsonb
 OR octet_length(p_payload::text)>1048576 THEN
 RAISE EXCEPTION 'invalid observational source event'; END IF;
 CASE p_kind
 WHEN 'system_state' THEN target_table:='system_state'; target_view:='v_runtime_system_state_write'; allowed:=ARRAY['entity','value']; required:=allowed;
 WHEN 'override' THEN target_table:='override_events'; target_view:='v_runtime_override_events_write'; allowed:=ARRAY['override_type','mode']; required:=ARRAY['override_type'];
 WHEN 'setpoint_observed' THEN target_table:='setpoint_changes'; target_view:='v_runtime_setpoint_changes_write'; allowed:=ARRAY['parameter','value']; required:=allowed;
 WHEN 'esp32_log' THEN target_table:='esp32_logs'; target_view:='v_runtime_esp32_logs_write'; allowed:=ARRAY['level','tag','message']; required:=ARRAY['level','message'];
 WHEN 'diagnostics' THEN target_table:='diagnostics'; target_view:='v_runtime_diagnostics_write'; allowed:=ARRAY['wifi_rssi','heap_bytes','heap_min_free_kb','heap_largest_free_block_kb','uptime_s','probe_health','reset_reason','firmware_version','active_probe_count','relief_cycle_count','vent_latch_timer_s','sealed_timer_s','vpd_watch_timer_s','mist_backoff_timer_s','vent_mist_assist_active','effective_heat_target_f','effective_cool_stage2_delta_f','effective_vpd_hysteresis_kpa','effective_dehum_aggressive_kpa','controller_time_epoch','controller_local_hour','sntp_valid','sntp_miss_count','last_sntp_sync_age_s','band_source','zone_wet_granted']; required:=ARRAY[]::text[];
 ELSE RAISE EXCEPTION 'unknown observational source kind'; END CASE;
 FOREACH k IN ARRAY required LOOP
 IF NOT p_payload ? k OR p_payload->k='null'::jsonb THEN
 RAISE EXCEPTION 'observational required field missing: %',k; END IF;
 END LOOP;
 typed_payload:=p_payload;
 FOR k,v IN SELECT key,value FROM jsonb_each(p_payload) LOOP
 IF NOT k=ANY(allowed) OR NOT EXISTS(
 SELECT 1 FROM pg_attribute a WHERE a.attrelid=format('public.%I',target_view)::regclass
 AND a.attname=k AND a.attnum>0 AND NOT a.attisdropped
 AND has_column_privilege('verdify_ingestor_runtime',a.attrelid,a.attnum,'INSERT')) THEN
 RAISE EXCEPTION 'observational field outside pinned INSERT projection: %',k; END IF;
 SELECT a.atttypid,ty.typcategory INTO STRICT t,category FROM pg_attribute a JOIN pg_type ty ON ty.oid=a.atttypid
 WHERE a.attrelid=format('public.%I',target_table)::regclass AND a.attname=k AND a.attnum>0 AND NOT a.attisdropped;
 IF v='null'::jsonb THEN CONTINUE; END IF;
 IF (category='N' AND jsonb_typeof(v)<>'number') OR (category='S' AND jsonb_typeof(v)<>'string')
 OR (category='B' AND jsonb_typeof(v)<>'boolean') THEN
 RAISE EXCEPTION 'observational JSON type mismatch: %',k; END IF;
 IF t IN ('smallint'::regtype,'integer'::regtype,'bigint'::regtype) THEN
 n:=(v #>> '{}')::numeric;
 IF n<>trunc(n) THEN RAISE EXCEPTION 'fractional observational integer: %',k; END IF;
 IF t='smallint'::regtype THEN v:=to_jsonb(n::smallint);
 ELSIF t='integer'::regtype THEN v:=to_jsonb(n::integer);
 ELSE v:=to_jsonb(n::bigint); END IF;
 typed_payload:=jsonb_set(typed_payload,ARRAY[k],v);
 END IF;
 END LOOP;
 IF p_kind='esp32_log' AND (p_payload->>'message'='' OR p_payload->>'level' NOT IN ('NONE','ERROR','WARN','INFO','DEBUG','VERBOSE','VERY_VERBOSE','UNKNOWN')) THEN
 RAISE EXCEPTION 'invalid observational log'; END IF;
 h:=sha256(convert_to(jsonb_build_object('event_id',p_event_id,'kind',p_kind,'source_ts',p_source_ts,
 'greenhouse_id',p_greenhouse_id,'runtime',p_runtime_instance_id,'generation',p_connection_generation,
 'payload',p_payload)::text,'UTF8'));
 INSERT INTO public.observational_source_events(event_id,kind,source_ts,greenhouse_id,source_runtime_instance_id,source_connection_generation,event_payload,event_sha256)
 VALUES(p_event_id,p_kind,p_source_ts,p_greenhouse_id,p_runtime_instance_id,p_connection_generation,p_payload,h)
 ON CONFLICT(event_id) DO NOTHING RETURNING event_id INTO inserted;
 IF inserted IS NULL THEN
 IF NOT EXISTS(SELECT 1 FROM public.observational_source_events WHERE event_id=p_event_id AND event_sha256=h) THEN
 RAISE EXCEPTION 'observational UUID already binds different immutable evidence'; END IF;
 RETURN false; END IF;
 IF p_kind='setpoint_observed' THEN
 -- Historical observed evidence; never now(), UPDATE, control replay or fresh confirmation.
 typed_payload:=typed_payload||jsonb_build_object('source','esp32','confirmed_at',p_source_ts,'delivery_status','observed');
 END IF;
 SELECT string_agg(format('%I',key),',' ORDER BY key),
 string_agg(format('(jsonb_populate_record(NULL::public.%I,$3)).%I',target_table,key),',' ORDER BY key)
 INTO column_names,expressions FROM jsonb_object_keys(typed_payload) AS key;
 IF p_kind IN ('override','esp32_log') THEN
 EXECUTE format('INSERT INTO public.%I(ts,%s) SELECT $1,%s',target_view,column_names,expressions)
 USING p_source_ts,p_greenhouse_id,typed_payload;
 ELSE
 EXECUTE format('INSERT INTO public.%I(ts,greenhouse_id,%s) SELECT $1,$2,%s',target_view,column_names,expressions)
 USING p_source_ts,p_greenhouse_id,typed_payload;
 END IF;
 RETURN true;
END $body$;
ALTER FUNCTION public.fn_record_observational_source_event(uuid,text,timestamptz,text,uuid,bigint,jsonb) OWNER TO verdify;
REVOKE ALL ON FUNCTION public.fn_record_observational_source_event(uuid,text,timestamptz,text,uuid,bigint,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.fn_record_observational_source_event(uuid,text,timestamptz,text,uuid,bigint,jsonb) TO verdify_ingestor_runtime;
-- Actual production-OID/name projections independently qualified on two clones.
DO $postflight$
BEGIN
 IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'fe79f986d58ba6deec513312441b5ba5d579168d3e7e28d5721bb5771150af81'
 OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '15e4eff5d86ff58bf3fc98075dfc4613b5fd2a418bf3be9251fd7e6b1634a96e'
 OR has_function_privilege('verdify_api_runtime_login','public.fn_record_observational_source_event(uuid,text,timestamptz,text,uuid,bigint,jsonb)','EXECUTE')
 OR NOT has_function_privilege('verdify_ingestor_runtime_login','public.fn_record_observational_source_event(uuid,text,timestamptz,text,uuid,bigint,jsonb)','EXECUTE')
 OR has_table_privilege('verdify_ingestor_runtime_login','public.observational_source_events','SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
 OR has_table_privilege('verdify_api_runtime_login','public.observational_source_events','SELECT,INSERT,UPDATE,DELETE,TRUNCATE') THEN
 RAISE EXCEPTION '267 refuses unqualified successor or observational duty expansion'; END IF;
 UPDATE public.runtime_ordinary_login_attestation_receipts
 SET boundary_sha256=CASE login_name
 WHEN 'verdify_api_runtime_login' THEN decode('fe79f986d58ba6deec513312441b5ba5d579168d3e7e28d5721bb5771150af81','hex')
 WHEN 'verdify_ingestor_runtime_login' THEN decode('15e4eff5d86ff58bf3fc98075dfc4613b5fd2a418bf3be9251fd7e6b1634a96e','hex') END,
 captured_at=clock_timestamp();
END $postflight$;
