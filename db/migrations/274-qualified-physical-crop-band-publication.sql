-- #371 owner-authenticated physical evidence publication. No evidence rows seeded.
-- Artifact hashes identify inputs; only the existing trusted operator authenticates
-- historical targets, hardware placement and per-probe observation provenance.
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations,public.runtime_ordinary_login_attestation_receipts,
 public.mcp_runtime_boundary_receipt IN SHARE ROW EXCLUSIVE MODE;
DO $preflight$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq=273
 AND filename='db/migrations/273-native-route-measurement-reader.sql'
 AND sha256='a0e4fe7bea3d27ea0d4218c5d423707e19b821e2684d5884c1280b03f78df76a' AND stamp_method='runner')
 OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>=274)
 OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
 OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt)<>1
 OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'aef9e39647d84c313d76795f15b382eb5ebccb5828eecac83e73cbb97002e10e'
 OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_api_runtime_login' AND boundary_sha256=decode('aef9e39647d84c313d76795f15b382eb5ebccb5828eecac83e73cbb97002e10e','hex'))
 OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '98e59209b41ba7a445150fde66be889bdc98689c43c80aa8bc5d0c3f7a677ef7'
 OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_ingestor_runtime_login' AND boundary_sha256=decode('98e59209b41ba7a445150fde66be889bdc98689c43c80aa8bc5d0c3f7a677ef7','hex'))
 OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '79e5bd322c1b9c60104c26b82b3d302d89fda26366031f7871f697f3c97ccf4b'
 OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton AND boundary_sha256=decode('79e5bd322c1b9c60104c26b82b3d302d89fda26366031f7871f697f3c97ccf4b','hex'))
 THEN RAISE EXCEPTION '274 refuses unqualified predecessor'; END IF;
END $preflight$;
CREATE FUNCTION public.fn_validate_physical_crop_band_diagnostic(d jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,public,pg_temp AS $validate$
DECLARE a jsonb; n integer; inside integer; hi integer; lo integer; day date;
 start_at timestamptz; end_at timestamptz; expected integer; k text;
BEGIN
 IF jsonb_typeof(d)<>'object' OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(d) key) IS DISTINCT FROM ARRAY['calculation_source_sha256','center_probe_measured','day','definition','dli_available','duration_basis','expected_bins','experiment_endpoint_eligible','fixed_sensor_panel','gas_available','greenhouse_id','historical_crop_target_verified','input_sha256','joint','panel_manifest_sha256','panel_members','panel_version','per_probe_freshness_verified','physical_proof_eligible','resource_cost_available','sample_basis','target_basis','target_manifest_sha256','target_version','temp','vpd','window_end','window_start'] THEN RETURN false; END IF;
 FOREACH k IN ARRAY ARRAY['day','window_start','window_end','target_version','panel_version','target_manifest_sha256','panel_manifest_sha256','input_sha256','calculation_source_sha256'] LOOP
 IF jsonb_typeof(d->k) IS DISTINCT FROM 'string' THEN RETURN false; END IF; END LOOP;
 IF jsonb_typeof(d->'expected_bins') IS DISTINCT FROM 'number' OR (d->>'expected_bins') !~ '^[0-9]+$' THEN RETURN false; END IF;
 day:=(d->>'day')::date;
 start_at:=day::timestamp AT TIME ZONE 'America/Denver';
 end_at:=(day+1)::timestamp AT TIME ZONE 'America/Denver';
 expected:=extract(epoch FROM end_at-start_at)::integer/900;
 IF d->>'definition' IS DISTINCT FROM 'fixed-panel-crop-band-v1'
 OR d->>'greenhouse_id' IS DISTINCT FROM 'vallery'
 OR (d->>'window_start')::timestamptz IS DISTINCT FROM start_at
 OR (d->>'window_end')::timestamptz IS DISTINCT FROM end_at
 OR (d->>'expected_bins')::integer IS DISTINCT FROM expected
 OR d->>'target_basis' IS DISTINCT FROM 'immutable_crop_targets_as_of_bin'
 OR d->>'sample_basis' IS DISTINCT FROM 'fixed_panel_fresh_probe_observations'
 OR d->>'duration_basis' IS DISTINCT FROM 'qualified_15_minute_bins_not_continuous_exposure'
 OR d->'panel_members' IS DISTINCT FROM '["north","east","west"]'::jsonb
 OR coalesce(length(d->>'target_version'),0)=0 OR coalesce(length(d->>'panel_version'),0)=0
 THEN RETURN false; END IF;
 FOREACH k IN ARRAY ARRAY['fixed_sensor_panel','historical_crop_target_verified','per_probe_freshness_verified','physical_proof_eligible'] LOOP
 IF d->k IS DISTINCT FROM 'true'::jsonb THEN RETURN false; END IF; END LOOP;
 FOREACH k IN ARRAY ARRAY['center_probe_measured','experiment_endpoint_eligible','dli_available','gas_available','resource_cost_available'] LOOP
 IF d->k IS DISTINCT FROM 'false'::jsonb THEN RETURN false; END IF; END LOOP;
 FOREACH k IN ARRAY ARRAY['target_manifest_sha256','panel_manifest_sha256','input_sha256','calculation_source_sha256'] LOOP
 IF coalesce(d->>k,'') !~ '^[0-9a-f]{64}$' THEN RETURN false; END IF; END LOOP;
 FOREACH k IN ARRAY ARRAY['temp','vpd','joint'] LOOP
 a:=d->k;
 IF jsonb_typeof(a)<>'object' THEN RETURN false; END IF;
 IF jsonb_typeof(a->'eligible_bins')<>'number' OR jsonb_typeof(a->'in_band_bins')<>'number'
 OR (a->>'eligible_bins') !~ '^[0-9]+$' OR (a->>'in_band_bins') !~ '^[0-9]+$' THEN RETURN false; END IF;
 n:=(a->>'eligible_bins')::integer; inside:=(a->>'in_band_bins')::integer;
 IF n>expected OR inside>n THEN RETURN false; END IF;
 IF n=0 THEN IF a->'in_band_pct' IS DISTINCT FROM 'null'::jsonb THEN RETURN false; END IF;
 ELSE IF jsonb_typeof(a->'in_band_pct')<>'number'
 OR abs((a->>'in_band_pct')::numeric-100.0*inside/n)>0.001 THEN RETURN false; END IF; END IF;
 IF k='joint' THEN
 IF (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(a) key) IS DISTINCT FROM ARRAY['eligible_bins','in_band_bins','in_band_pct'] OR n>least((d->'temp'->>'eligible_bins')::integer,(d->'vpd'->>'eligible_bins')::integer) THEN RETURN false; END IF;
 ELSE
 IF (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(a) key) IS DISTINCT FROM ARRAY['eligible_bins','high_miss_bins','in_band_bins','in_band_pct','low_miss_bins','mean_high_distance','mean_low_distance','mean_outside_distance','worst_measured_zone'] OR (a->>'high_miss_bins') !~ '^[0-9]+$' OR (a->>'low_miss_bins') !~ '^[0-9]+$' THEN RETURN false; END IF;
 hi:=(a->>'high_miss_bins')::integer; lo:=(a->>'low_miss_bins')::integer;
 IF hi+lo<>n-inside THEN RETURN false; END IF;
 IF n=0 THEN
 IF a->'mean_high_distance' IS DISTINCT FROM 'null'::jsonb OR a->'mean_low_distance' IS DISTINCT FROM 'null'::jsonb
 OR a->'mean_outside_distance' IS DISTINCT FROM 'null'::jsonb OR a->'worst_measured_zone' IS DISTINCT FROM 'null'::jsonb THEN RETURN false; END IF;
 ELSE
 IF jsonb_typeof(a->'mean_high_distance')<>'number' OR jsonb_typeof(a->'mean_low_distance')<>'number' OR jsonb_typeof(a->'mean_outside_distance')<>'number'
 OR (a->>'mean_high_distance')::numeric<0 OR (a->>'mean_low_distance')::numeric<0
 OR abs((a->>'mean_outside_distance')::numeric-(a->>'mean_high_distance')::numeric-(a->>'mean_low_distance')::numeric)>0.001
 OR ((a->>'mean_high_distance')::numeric>0) IS DISTINCT FROM (hi>0)
 OR ((a->>'mean_low_distance')::numeric>0) IS DISTINCT FROM (lo>0)
 OR (n-inside>0 AND coalesce(a->>'worst_measured_zone','') NOT IN('north','east','west'))
 OR (a->>'worst_measured_zone' IS NOT NULL AND a->>'worst_measured_zone' NOT IN('north','east','west')) THEN RETURN false; END IF;
 END IF; END IF;
 END LOOP;
 RETURN true;
EXCEPTION WHEN others THEN RETURN false;
END $validate$;
REVOKE ALL ON FUNCTION public.fn_validate_physical_crop_band_diagnostic(jsonb) FROM PUBLIC;
CREATE TABLE public.physical_crop_band_revisions(
 revision_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 day date NOT NULL, greenhouse_id text NOT NULL CHECK(greenhouse_id='vallery'),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 published_by text NOT NULL DEFAULT session_user CHECK(published_by='verdify'),
 diagnostic jsonb NOT NULL CHECK(public.fn_validate_physical_crop_band_diagnostic(diagnostic)),
 qualification jsonb NOT NULL,
 CHECK(octet_length(diagnostic::text)<=65536 AND octet_length(qualification::text)<=16384),
 CHECK(day=(diagnostic->>'day')::date),
 CHECK((diagnostic->>'window_end')::timestamptz<=recorded_at),
 CHECK((qualification->>'contract'='trusted-owner-physical-qualification-v1') IS TRUE),
 CHECK((qualification->>'attested_by'='verdify') IS TRUE),
 CHECK((jsonb_typeof(qualification)='object' AND jsonb_typeof(qualification->'authenticity_statement')='string'
 AND length(qualification->>'authenticity_statement') BETWEEN 40 AND 4096) IS TRUE),
 CHECK((qualification->>'artifact_sha256' ~ '^[0-9a-f]{64}$') IS TRUE),
 CHECK((qualification->'input_hashes'=jsonb_build_object(
 'target_manifest_sha256',diagnostic->>'target_manifest_sha256',
 'panel_manifest_sha256',diagnostic->>'panel_manifest_sha256',
 'input_sha256',diagnostic->>'input_sha256',
 'calculation_source_sha256',diagnostic->>'calculation_source_sha256')) IS TRUE)
);
CREATE INDEX physical_crop_band_revision_scope ON public.physical_crop_band_revisions(day,greenhouse_id,revision_id DESC);
REVOKE ALL ON public.physical_crop_band_revisions FROM PUBLIC;
REVOKE ALL ON SEQUENCE public.physical_crop_band_revisions_revision_id_seq FROM PUBLIC;
CREATE FUNCTION public.fn_physical_crop_band_revision_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $immutable$
BEGIN RAISE EXCEPTION 'physical evidence revisions are append-only'; END $immutable$;
REVOKE ALL ON FUNCTION public.fn_physical_crop_band_revision_immutable() FROM PUBLIC;
CREATE TRIGGER physical_crop_band_revision_immutable BEFORE UPDATE OR DELETE ON public.physical_crop_band_revisions
 FOR EACH ROW EXECUTE FUNCTION public.fn_physical_crop_band_revision_immutable();
CREATE FUNCTION public.fn_publish_physical_crop_band_evidence(d jsonb,q jsonb) RETURNS bigint
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public,pg_temp AS $publish$
DECLARE id bigint;
BEGIN
 IF session_user<>'verdify' OR current_user<>'verdify' THEN RAISE EXCEPTION 'trusted owner publication required'; END IF;
 IF NOT public.fn_validate_physical_crop_band_diagnostic(d)
 OR q->>'contract' IS DISTINCT FROM 'trusted-owner-physical-qualification-v1'
 OR q->>'attested_by' IS DISTINCT FROM 'verdify'
 OR jsonb_typeof(q) IS DISTINCT FROM 'object'
 OR jsonb_typeof(q->'authenticity_statement') IS DISTINCT FROM 'string'
 OR coalesce(length(q->>'authenticity_statement'),0)<40
 OR length(q->>'authenticity_statement')>4096
 OR coalesce(q->>'artifact_sha256','') !~ '^[0-9a-f]{64}$'
 OR q->'input_hashes' IS DISTINCT FROM jsonb_build_object(
 'target_manifest_sha256',d->>'target_manifest_sha256','panel_manifest_sha256',d->>'panel_manifest_sha256',
 'input_sha256',d->>'input_sha256','calculation_source_sha256',d->>'calculation_source_sha256')
 THEN RAISE EXCEPTION 'qualified scoped evidence required'; END IF;
 INSERT INTO public.physical_crop_band_revisions(day,greenhouse_id,diagnostic,qualification)
 VALUES((d->>'day')::date,'vallery',d,q) RETURNING revision_id INTO id;
 RETURN id;
END $publish$;
REVOKE ALL ON FUNCTION public.fn_publish_physical_crop_band_evidence(jsonb,jsonb) FROM PUBLIC;
CREATE FUNCTION public.fn_physical_crop_band_evidence(p_day date,p_greenhouse text DEFAULT 'vallery')
RETURNS TABLE(reader_contract_version integer,day date,greenhouse_id text,served_at timestamptz,
 revision_id bigint,recorded_at timestamptz,diagnostic jsonb,unavailable_reason text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $reader$
 SELECT 1,p_day,p_greenhouse,statement_timestamp(),r.revision_id,r.recorded_at,r.diagnostic,
 CASE WHEN p_day IS NULL OR p_greenhouse IS DISTINCT FROM 'vallery' THEN 'unsupported_scope'
 WHEN r.revision_id IS NULL THEN 'publication_not_qualified' END
 FROM(SELECT 1) anchor LEFT JOIN LATERAL(
 SELECT x.* FROM public.physical_crop_band_revisions x
 WHERE x.day=p_day AND x.greenhouse_id=p_greenhouse AND x.recorded_at<=statement_timestamp()
 ORDER BY x.revision_id DESC LIMIT 1) r ON p_day IS NOT NULL AND p_greenhouse='vallery';
$reader$;
REVOKE ALL ON FUNCTION public.fn_physical_crop_band_evidence(date,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.fn_physical_crop_band_evidence(date,text) TO verdify_api_runtime,verdify_mcp_runtime;
DO $postflight$ BEGIN
 IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM '67bb961f69e72f8d59f8d38dce96201682b1fb8fb40d423071c987b075cf994e'
 OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '126f95dc75242a126579331fadf711f0c1e8e408d20ec7cd94e3d1e27a0dcf75'
 OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '7083e4d43044e7f2b0150b58fa3cc8cc45c599a68b1f3cafaf9242e0f0f79e68'
 THEN RAISE EXCEPTION '274 refuses unqualified successor'; END IF;
 UPDATE public.runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode('67bb961f69e72f8d59f8d38dce96201682b1fb8fb40d423071c987b075cf994e','hex'),captured_at=clock_timestamp() WHERE login_name='verdify_api_runtime_login';
 UPDATE public.runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode('126f95dc75242a126579331fadf711f0c1e8e408d20ec7cd94e3d1e27a0dcf75','hex'),captured_at=clock_timestamp() WHERE login_name='verdify_ingestor_runtime_login';
 UPDATE public.mcp_runtime_boundary_receipt SET boundary_sha256=decode('7083e4d43044e7f2b0150b58fa3cc8cc45c599a68b1f3cafaf9242e0f0f79e68','hex') WHERE singleton;
END $postflight$;
