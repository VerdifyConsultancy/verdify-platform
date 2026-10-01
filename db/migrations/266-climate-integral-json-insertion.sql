-- #382 forward repair: preserve immutable accepted evidence, normalize only insertion.
-- Serialized runner applies this file and exact ledger stamp atomically.
SET LOCAL search_path = pg_catalog, public, pg_temp;
LOCK TABLE public.schema_migrations, public.runtime_ordinary_login_attestation_receipts IN SHARE ROW EXCLUSIVE MODE;
DO $preflight$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq=265 AND filename='db/migrations/265-idempotent-climate-source-events.sql' AND sha256='f7bded636fd1d98ef37a192ecf4fdd085585ac3a0ceadb388d1330edea067ae2' AND stamp_method='runner')
 OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>=266)
 OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
 OR (SELECT encode(boundary_sha256,'hex') FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_api_runtime_login') IS DISTINCT FROM '0f166e52d683519ed94cd72d1b074c3e0aed85aa404299220fe85d4bf8235b38'
 OR (SELECT encode(boundary_sha256,'hex') FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_ingestor_runtime_login') IS DISTINCT FROM '582eed065ddcd463d8542af6f0184b7e90780ce0f28c42360a0e494fb396489d'
 OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name)) THEN
 RAISE EXCEPTION '266 refuses changed predecessor ledger, receipt or owner role'; END IF;
END $preflight$;
CREATE OR REPLACE FUNCTION public.fn_record_climate_source_event(
 p_event_id uuid,p_source_ts timestamptz,p_greenhouse_id text,
 p_runtime_instance_id uuid,p_connection_generation bigint,
 p_payload jsonb,p_sample_provenance jsonb
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public,pg_temp
SET timezone='UTC'
AS $body$
DECLARE h bytea; inserted uuid; column_names text; expressions text; k text; typed_payload jsonb; v jsonb; n numeric; t oid;
BEGIN
 IF p_event_id IS NULL OR p_source_ts IS NULL OR p_greenhouse_id IS DISTINCT FROM 'vallery'
 OR p_runtime_instance_id IS NULL OR p_connection_generation IS NULL
 OR p_connection_generation NOT BETWEEN 0 AND 9007199254740991
 OR jsonb_typeof(p_payload) IS DISTINCT FROM 'object' OR p_payload='{}'::jsonb
 OR jsonb_typeof(p_sample_provenance) IS DISTINCT FROM 'object'
 OR octet_length(p_payload::text)>1048576 OR octet_length(p_sample_provenance::text)>1048576 THEN
 RAISE EXCEPTION 'invalid climate source event'; END IF;
 FOR k IN SELECT jsonb_object_keys(p_payload) LOOP
 IF k IN ('ts','greenhouse_id') OR NOT EXISTS(
 SELECT 1 FROM pg_attribute a WHERE a.attrelid='public.v_runtime_climate_write'::regclass
 AND a.attname=k AND a.attnum>0 AND NOT a.attisdropped
 AND has_column_privilege('verdify_ingestor_runtime',a.attrelid,a.attnum,'INSERT')) THEN
 RAISE EXCEPTION 'climate payload column is outside runtime INSERT projection: %',k; END IF;
 END LOOP;
 h:=sha256(convert_to(jsonb_build_object('event_id',p_event_id,'source_ts',p_source_ts,
 'greenhouse_id',p_greenhouse_id,'runtime',p_runtime_instance_id,'generation',p_connection_generation,
 'payload',p_payload,'sample_provenance',p_sample_provenance)::text,'UTF8'));
 INSERT INTO public.climate_source_events(event_id,source_ts,greenhouse_id,source_runtime_instance_id,source_connection_generation,climate_payload,sample_provenance,event_sha256)
 VALUES(p_event_id,p_source_ts,p_greenhouse_id,p_runtime_instance_id,p_connection_generation,p_payload,p_sample_provenance,h)
 ON CONFLICT(event_id) DO NOTHING RETURNING event_id INTO inserted;
 IF inserted IS NULL THEN
 IF NOT EXISTS(SELECT 1 FROM public.climate_source_events WHERE event_id=p_event_id AND event_sha256=h) THEN
 RAISE EXCEPTION 'climate event UUID already binds different immutable evidence'; END IF;
 RETURN false;
 END IF;
 -- Preserve the original UUID payload/hash. Normalize only the insertion copy:
 -- ESPHome numbers arrive as JSON integral decimals (e.g. 770.0), while
 -- jsonb_populate_record integer input requires integer text. Never round.
 typed_payload := p_payload;
 FOR k, t IN
 SELECT a.attname,a.atttypid FROM pg_attribute a
 WHERE a.attrelid='public.climate'::regclass AND a.attnum>0 AND NOT a.attisdropped
 AND a.atttypid IN ('smallint'::regtype,'integer'::regtype,'bigint'::regtype)
 AND p_payload ? a.attname
 LOOP
 v := p_payload -> k;
 IF v = 'null'::jsonb THEN CONTINUE; END IF;
 IF jsonb_typeof(v) IS DISTINCT FROM 'number' THEN
 RAISE EXCEPTION 'climate integer column requires a JSON number: %',k;
 END IF;
 n := (v #>> '{}')::numeric;
 IF n <> trunc(n) THEN
 RAISE EXCEPTION 'climate integer column refuses fractional value: %',k;
 END IF;
 -- Exact PostgreSQL integer casts enforce each destination range after the
 -- explicit integral check. Their JSON serialization removes decimal scale.
 IF t='smallint'::regtype THEN v:=to_jsonb(n::smallint);
 ELSIF t='integer'::regtype THEN v:=to_jsonb(n::integer);
 ELSE v:=to_jsonb(n::bigint); END IF;
 typed_payload:=jsonb_set(typed_payload,ARRAY[k],v);
 END LOOP;
 SELECT string_agg(format('%I',key),',' ORDER BY key),
 string_agg(format('(jsonb_populate_record(NULL::public.climate,$3)).%I',key),',' ORDER BY key)
 INTO column_names,expressions FROM jsonb_object_keys(p_payload) AS key;
 EXECUTE 'INSERT INTO public.v_runtime_climate_write(ts,greenhouse_id,'||column_names||') SELECT $1,$2,'||expressions
 USING p_source_ts,p_greenhouse_id,typed_payload;
 RETURN true;
END $body$;
ALTER FUNCTION public.fn_record_climate_source_event(uuid,timestamptz,text,uuid,bigint,jsonb,jsonb) OWNER TO verdify;
REVOKE ALL ON FUNCTION public.fn_record_climate_source_event(uuid,timestamptz,text,uuid,bigint,jsonb,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.fn_record_climate_source_event(uuid,timestamptz,text,uuid,bigint,jsonb,jsonb) TO verdify_ingestor_runtime;
-- Both successor literals are production-OID/database-name projections of
-- two qualified disposable clones. No production receipt was refreshed there.
DO $postflight$
BEGIN
 IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM '5bb375800baf2e61d29da1e1327b49c090da4a3e53656b1fbf5cf839bfff5e43'
 OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '17287481525f6e831d9d2a55cedfc27af3c9c320f8f196ae9a0d976feb5ed19a'
 OR has_function_privilege('verdify_api_runtime_login','public.fn_record_climate_source_event(uuid,timestamptz,text,uuid,bigint,jsonb,jsonb)','EXECUTE')
 OR NOT has_function_privilege('verdify_ingestor_runtime_login','public.fn_record_climate_source_event(uuid,timestamptz,text,uuid,bigint,jsonb,jsonb)','EXECUTE')
 OR has_table_privilege('verdify_ingestor_runtime_login','public.climate_source_events','INSERT,UPDATE,DELETE,TRUNCATE')
 OR has_table_privilege('verdify_api_runtime_login','public.climate_source_events','SELECT,INSERT,UPDATE,DELETE,TRUNCATE') THEN
 RAISE EXCEPTION '266 refuses unqualified successor or climate duty expansion'; END IF;
 UPDATE public.runtime_ordinary_login_attestation_receipts
 SET boundary_sha256=CASE login_name
 WHEN 'verdify_api_runtime_login' THEN decode('5bb375800baf2e61d29da1e1327b49c090da4a3e53656b1fbf5cf839bfff5e43','hex')
 WHEN 'verdify_ingestor_runtime_login' THEN decode('17287481525f6e831d9d2a55cedfc27af3c9c320f8f196ae9a0d976feb5ed19a','hex') END,
 captured_at=clock_timestamp();
END $postflight$;
