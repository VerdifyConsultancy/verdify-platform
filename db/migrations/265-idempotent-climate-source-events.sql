-- #382: explicit UUID climate-event acceptance, not timestamp deduplication.
-- Owning migration runner wraps this entire file and its ledger stamp atomically.
SET LOCAL search_path = pg_catalog, public, pg_temp;
LOCK TABLE public.schema_migrations, public.runtime_ordinary_login_attestation_receipts
 IN SHARE ROW EXCLUSIVE MODE;
DO $preflight$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq=264 AND filename='db/migrations/264-facility-safe-closure-startup-handoff.sql' AND sha256='406f284c941e599d142c336ce0a9afec1efd46d108d06c2f8069d0a08d7b1dc3' AND stamp_method='runner')
 OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>=265)
 OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
 OR (SELECT encode(boundary_sha256,'hex') FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_api_runtime_login') IS DISTINCT FROM 'fcedb02292df921dcc0f106e41ee53338065a16c6b8ed8e58780c544bd03551b'
 OR (SELECT encode(boundary_sha256,'hex') FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_ingestor_runtime_login') IS DISTINCT FROM '1ee6b4aa40eb9e56c7ab90ebb18cc6c2cb094a0e5d67092c8c406f72fc321fbc'
 OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name)) THEN
 RAISE EXCEPTION '265 refuses changed predecessor ledger, receipt or owner role'; END IF;
END $preflight$;
CREATE TABLE public.climate_source_events (
 event_id uuid PRIMARY KEY,
 source_ts timestamptz NOT NULL,
 greenhouse_id text NOT NULL CHECK(greenhouse_id='vallery'),
 source_runtime_instance_id uuid NOT NULL,
 source_connection_generation bigint NOT NULL CHECK(source_connection_generation BETWEEN 0 AND 9007199254740991),
 climate_payload jsonb NOT NULL CHECK(jsonb_typeof(climate_payload)='object'),
 sample_provenance jsonb NOT NULL CHECK(jsonb_typeof(sample_provenance)='object'),
 event_sha256 bytea NOT NULL CHECK(octet_length(event_sha256)=32),
 accepted_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE public.climate_source_events OWNER TO verdify;
REVOKE ALL ON public.climate_source_events FROM PUBLIC;
-- Existing database-owned definer pattern. Input names are constrained to
-- the independently pinned ordinary climate INSERT projection below.
CREATE FUNCTION public.fn_record_climate_source_event(
 p_event_id uuid,p_source_ts timestamptz,p_greenhouse_id text,
 p_runtime_instance_id uuid,p_connection_generation bigint,
 p_payload jsonb,p_sample_provenance jsonb
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public,pg_temp
SET timezone='UTC'
AS $body$
DECLARE h bytea; inserted uuid; column_names text; expressions text; k text;
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
 SELECT string_agg(format('%I',key),',' ORDER BY key),
 string_agg(format('(jsonb_populate_record(NULL::public.climate,$3)).%I',key),',' ORDER BY key)
 INTO column_names,expressions FROM jsonb_object_keys(p_payload) AS key;
 EXECUTE 'INSERT INTO public.v_runtime_climate_write(ts,greenhouse_id,'||column_names||') SELECT $1,$2,'||expressions
 USING p_source_ts,p_greenhouse_id,p_payload;
 RETURN true;
END $body$;
ALTER FUNCTION public.fn_record_climate_source_event(uuid,timestamptz,text,uuid,bigint,jsonb,jsonb) OWNER TO verdify;
REVOKE ALL ON FUNCTION public.fn_record_climate_source_event(uuid,timestamptz,text,uuid,bigint,jsonb,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.fn_record_climate_source_event(uuid,timestamptz,text,uuid,bigint,jsonb,jsonb) TO verdify_ingestor_runtime;
-- Both successor literals are production-OID/database-name projections of
-- two qualified disposable clones. No production receipt was refreshed there.
DO $postflight$
BEGIN
 IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM '0f166e52d683519ed94cd72d1b074c3e0aed85aa404299220fe85d4bf8235b38'
 OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '582eed065ddcd463d8542af6f0184b7e90780ce0f28c42360a0e494fb396489d'
 OR has_function_privilege('verdify_api_runtime_login','public.fn_record_climate_source_event(uuid,timestamptz,text,uuid,bigint,jsonb,jsonb)','EXECUTE')
 OR NOT has_function_privilege('verdify_ingestor_runtime_login','public.fn_record_climate_source_event(uuid,timestamptz,text,uuid,bigint,jsonb,jsonb)','EXECUTE')
 OR has_table_privilege('verdify_ingestor_runtime_login','public.climate_source_events','INSERT,UPDATE,DELETE,TRUNCATE')
 OR has_table_privilege('verdify_api_runtime_login','public.climate_source_events','SELECT,INSERT,UPDATE,DELETE,TRUNCATE') THEN
 RAISE EXCEPTION '265 refuses unqualified successor or climate duty expansion'; END IF;
 UPDATE public.runtime_ordinary_login_attestation_receipts
 SET boundary_sha256=CASE login_name
 WHEN 'verdify_api_runtime_login' THEN decode('0f166e52d683519ed94cd72d1b074c3e0aed85aa404299220fe85d4bf8235b38','hex')
 WHEN 'verdify_ingestor_runtime_login' THEN decode('582eed065ddcd463d8542af6f0184b7e90780ce0f28c42360a0e494fb396489d','hex') END,
 captured_at=clock_timestamp();
END $postflight$;
